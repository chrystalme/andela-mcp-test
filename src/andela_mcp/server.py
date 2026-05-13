from __future__ import annotations

import hmac
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from andela_mcp import __version__
from andela_mcp.chat import (
    DEFAULT_PRINCIPAL,
    MAX_HISTORY_MESSAGES,
    ChatMessage,
    ChatReply,
    ChatService,
    Principal,
    build_chat_service,
)
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
_CHAT_RATE_LIMIT = "10/minute"  # per remote IP

# Module-level limiter so the @limiter.limit decorator can attach metadata
# at import time. The Limiter instance is also bound to app.state in create_app.
limiter = Limiter(key_func=get_remote_address)

# auto_error=False so we can return a structured 401 instead of FastAPI's default 403.
_admin_bearer = HTTPBearer(auto_error=False)


async def require_admin(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_admin_bearer),  # noqa: B008
) -> None:
    """Gate admin routes behind ANDELA_MCP_ADMIN_TOKEN. Fail-closed if unset."""
    settings: Settings = request.app.state.settings
    expected = settings.admin_token
    if expected is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="admin auth not configured: set ANDELA_MCP_ADMIN_TOKEN",
        )
    presented = creds.credentials if creds is not None else ""
    if not hmac.compare_digest(presented, expected.get_secret_value()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing admin bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


class ToolCallRequest(BaseModel):
    server: str
    tool: str
    arguments: dict[str, Any] = {}


class ToolCallResponse(BaseModel):
    server: str
    tool: str
    result: Any


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_HISTORY_MESSAGES)
    principal: Principal = DEFAULT_PRINCIPAL


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
        if settings.groq_api_key is not None:
            app.state.chat = build_chat_service(
                clients=clients,
                groq_api_key=settings.groq_api_key.get_secret_value(),
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


# ── route handlers (registered in create_app via add_api_route) ──────────────


async def _index(_request: Request) -> HTMLResponse:
    return HTMLResponse((_STATIC_DIR / "index.html").read_text(encoding="utf-8"))


async def _healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


async def _readyz(request: Request) -> dict[str, Any]:
    clients: dict[str, MCPClient] = request.app.state.clients
    return {"status": "ok", "servers": list(clients)}


async def _list_tools(request: Request) -> dict[str, list[dict[str, Any]]]:
    clients: dict[str, MCPClient] = request.app.state.clients
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


async def _call_tool(req: ToolCallRequest, request: Request) -> ToolCallResponse:
    clients: dict[str, MCPClient] = request.app.state.clients
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


@limiter.limit(_CHAT_RATE_LIMIT)
async def _chat(request: Request, req: ChatRequest) -> ChatReply:
    chat_service: ChatService | None = request.app.state.chat
    if chat_service is None:
        raise HTTPException(
            status_code=503,
            detail="chat is unavailable: ANDELA_MCP_GROQ_API_KEY not configured",
        )
    try:
        return await chat_service.respond(req.messages, principal=req.principal)
    except Exception as exc:
        log.exception("chat_failed")
        raise HTTPException(status_code=502, detail=f"chat failed: {exc}") from exc


def create_app(settings: Settings | None = None) -> FastAPI:
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
    app.state.limiter = limiter
    # slowapi's handler is typed `(Request, RateLimitExceeded) -> Response`,
    # which is narrower than Starlette's expected `(Request, Exception)`. Cast
    # is safe — the handler is only invoked with RateLimitExceeded.
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

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

    app.add_api_route(
        "/",
        _index,
        methods=["GET"],
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    app.add_api_route("/healthz", _healthz, methods=["GET"])
    app.add_api_route("/readyz", _readyz, methods=["GET"])
    app.add_api_route(
        "/v1/tools",
        _list_tools,
        methods=["GET"],
        dependencies=[Depends(require_admin)],
    )
    app.add_api_route(
        "/v1/tools/call",
        _call_tool,
        methods=["POST"],
        response_model=ToolCallResponse,
        dependencies=[Depends(require_admin)],
    )
    app.add_api_route("/v1/chat", _chat, methods=["POST"], response_model=ChatReply)

    return app


app = create_app()
