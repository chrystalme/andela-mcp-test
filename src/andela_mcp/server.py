from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from andela_mcp import __version__
from andela_mcp.chat import ChatMessage, ChatReply, ChatService, build_chat_service
from andela_mcp.client import (
    MCPClient,
    MCPConnectError,
    MCPToolError,
    ServersConfigError,
    load_server_configs,
)
from andela_mcp.config import Settings, get_settings
from andela_mcp.logging import configure_logging, get_logger

log = get_logger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


class ToolCallRequest(BaseModel):
    server: str
    tool: str
    arguments: dict[str, Any] = {}


class ToolCallResponse(BaseModel):
    server: str
    tool: str
    result: Any


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    try:
        configs = load_server_configs(settings.servers_config_path)
    except ServersConfigError:
        log.exception("startup_failed_invalid_config")
        raise

    clients: dict[str, MCPClient] = {}
    try:
        for cfg in configs:
            client = MCPClient(cfg)
            try:
                await client.connect()
            except MCPConnectError:
                log.error(
                    "startup_failed_mcp_connect",
                    server=cfg.name,
                    already_connected=list(clients),
                )
                raise
            clients[cfg.name] = client
        app.state.clients = clients
        if settings.openrouter_api_key is not None:
            app.state.chat = build_chat_service(
                clients=clients,
                openrouter_api_key=settings.openrouter_api_key.get_secret_value(),
                model=settings.llm_model,
                openai_api_key=(
                    settings.openai_api_key.get_secret_value()
                    if settings.openai_api_key is not None
                    else None
                ),
            )
        else:
            app.state.chat = None
        log.info("startup_complete", servers=list(clients))
        yield
    finally:
        for client in clients.values():
            await client.close()
        log.info("shutdown_complete")


def create_app(settings: Settings | None = None) -> FastAPI:  # noqa: PLR0915 - inlined routes keep request-scoped middleware + lifespan-managed clients in one place per CLAUDE.md
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title="andela-mcp",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
    )
    app.state.settings = settings
    app.state.clients = {}
    app.state.chat = None

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Any:
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        token = structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        start = time.perf_counter()
        log.debug("request_started")
        try:
            response = await call_next(request)
        except Exception:
            log.exception("request_failed")
            raise
        finally:
            log.info(
                "request_completed",
                duration_ms=round((time.perf_counter() - start) * 1000, 2),
            )
            structlog.contextvars.reset_contextvars(**token)
        response.headers["x-request-id"] = request_id
        return response

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> HTMLResponse:
        return HTMLResponse((_STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    async def readyz() -> dict[str, Any]:
        clients: dict[str, MCPClient] = app.state.clients
        return {"status": "ok", "servers": list(clients)}

    @app.get("/v1/tools")
    async def list_tools() -> dict[str, list[dict[str, Any]]]:
        clients: dict[str, MCPClient] = app.state.clients
        out: dict[str, list[dict[str, Any]]] = {}
        for name, c in clients.items():
            try:
                out[name] = await c.list_tools()
            except TimeoutError as exc:
                log.warning("list_tools_timeout", server=name)
                raise HTTPException(
                    status_code=504,
                    detail=f"timeout listing tools on {name!r}",
                ) from exc
            except Exception as exc:
                log.exception("list_tools_failed", server=name)
                raise HTTPException(
                    status_code=502,
                    detail=f"upstream MCP server {name!r} failed to list tools: {exc}",
                ) from exc
        return out

    @app.post("/v1/tools/call", response_model=ToolCallResponse)
    async def call_tool(req: ToolCallRequest) -> ToolCallResponse:
        clients: dict[str, MCPClient] = app.state.clients
        client = clients.get(req.server)
        if client is None:
            raise HTTPException(status_code=404, detail=f"unknown server {req.server!r}")
        try:
            result = await client.call_tool(req.tool, req.arguments)
        except MCPToolError as exc:
            log.warning("call_tool_upstream_error", server=req.server, tool=req.tool)
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except TimeoutError as exc:
            log.warning("call_tool_timeout", server=req.server, tool=req.tool)
            raise HTTPException(
                status_code=504,
                detail=f"timeout calling tool {req.tool!r} on {req.server!r}",
            ) from exc
        except Exception as exc:
            log.exception("call_tool_failed", server=req.server, tool=req.tool)
            raise HTTPException(
                status_code=502,
                detail=f"upstream MCP server {req.server!r} failed: {exc}",
            ) from exc
        return ToolCallResponse(server=req.server, tool=req.tool, result=result)

    @app.post("/v1/chat", response_model=ChatReply)
    async def chat(req: ChatRequest) -> ChatReply:
        chat_service: ChatService | None = app.state.chat
        if chat_service is None:
            raise HTTPException(
                status_code=503,
                detail="chat is unavailable: ANDELA_MCP_OPENROUTER_API_KEY not configured",
            )
        if not req.messages:
            raise HTTPException(status_code=400, detail="messages must not be empty")
        try:
            return await chat_service.respond(req.messages)
        except Exception as exc:
            log.exception("chat_failed")
            raise HTTPException(status_code=502, detail=f"chat failed: {exc}") from exc

    return app


app = create_app()
