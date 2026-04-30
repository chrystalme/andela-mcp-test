from __future__ import annotations

import json
import os
import re
from contextlib import AsyncExitStack
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client

# mcp ≥ 1.x marks `streamablehttp_client` as deprecated in favor of
# `streamable_http_client`, but the new function dropped the `headers` kwarg —
# callers must build an httpx.AsyncClient themselves. Until we need anything
# beyond a simple bearer header, the deprecated form is the smaller surface.
from mcp.client.streamable_http import streamablehttp_client
from pydantic import ValidationError

from andela_mcp.config import MCPServerConfig, MCPTransport
from andela_mcp.logging import get_logger

log = get_logger(__name__)

_ERROR_CONTENT_MAX_CHARS = 500
_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _truncate_for_error(value: object, limit: int = _ERROR_CONTENT_MAX_CHARS) -> str:
    if limit < 0:
        raise ValueError("limit must be non-negative")
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}... [truncated, {len(text)} chars total]"


class ServersConfigError(ValueError):
    """Raised when the upstream MCP server registry cannot be loaded."""


class MCPConnectError(RuntimeError):
    """Raised when an upstream MCP server cannot be connected."""


class MCPToolError(RuntimeError):
    """Raised when an upstream MCP tool call returns an error result."""


def _expand_env_vars(value: Any) -> Any:
    """Substitute ${VAR} placeholders against the process environment.

    Recurses into dict/list values. Unset variables become empty strings; the
    caller is expected to validate that critical secrets aren't blank.
    """
    if isinstance(value, str):
        return _ENV_VAR_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(v) for v in value]
    return value


def load_server_configs(path: Path) -> list[MCPServerConfig]:
    """Load and validate the upstream MCP server registry from JSON.

    Schema: a top-level `servers` list, each entry shaped like `MCPServerConfig`.
    `${ENV_VAR}` placeholders in any string field are expanded against the
    process environment so secrets (e.g. bearer tokens) can be injected without
    landing in the committed config.
    """
    if not path.exists():
        log.warning("servers_config_missing", path=str(path))
        return []

    log.debug("servers_config_loading", path=str(path))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log.error("servers_config_invalid_json", path=str(path), error=str(exc))
        raise ServersConfigError(f"invalid JSON in {path}: {exc}") from exc

    raw = _expand_env_vars(raw)

    try:
        configs = [MCPServerConfig.model_validate(s) for s in raw.get("servers", [])]
    except ValidationError as exc:
        log.error("servers_config_invalid_schema", path=str(path), error=str(exc))
        raise ServersConfigError(f"invalid server config in {path}: {exc}") from exc

    log.debug("servers_config_loaded", count=len(configs))
    return configs


class MCPClient:
    """Manages a single MCP session over stdio, HTTP, or SSE."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def connect(self) -> None:
        if self._session is not None:
            raise RuntimeError(
                f"MCP client {self.config.name!r} is already connected; "
                "call close() before reconnecting"
            )
        log.info("mcp_connect", server=self.config.name, transport=self.config.transport)

        try:
            match self.config.transport:
                case MCPTransport.STDIO:
                    if self.config.command is None:
                        raise MCPConnectError(
                            f"server {self.config.name!r}: stdio transport requires `command`"
                        )
                    log.debug(
                        "mcp_connect_stdio",
                        server=self.config.name,
                        command=self.config.command,
                        args=list(self.config.args),
                    )
                    params = StdioServerParameters(
                        command=self.config.command,
                        args=list(self.config.args),
                    )
                    read, write = await self._stack.enter_async_context(stdio_client(params))
                case MCPTransport.HTTP:
                    if self.config.url is None:
                        raise MCPConnectError(
                            f"server {self.config.name!r}: http transport requires `url`"
                        )
                    log.debug("mcp_connect_http", server=self.config.name, url=str(self.config.url))
                    read, write, _ = await self._stack.enter_async_context(
                        streamablehttp_client(str(self.config.url), headers=self.config.headers)
                    )
                case MCPTransport.SSE:
                    if self.config.url is None:
                        raise MCPConnectError(
                            f"server {self.config.name!r}: sse transport requires `url`"
                        )
                    log.debug("mcp_connect_sse", server=self.config.name, url=str(self.config.url))
                    read, write = await self._stack.enter_async_context(
                        sse_client(str(self.config.url), headers=self.config.headers)
                    )

            session = await self._stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except Exception as exc:
            log.error(
                "mcp_connect_failed",
                server=self.config.name,
                transport=self.config.transport,
                error=str(exc),
                exc_info=True,
            )
            try:
                await self._stack.aclose()
            except Exception:
                log.exception("mcp_connect_cleanup_failed", server=self.config.name)
            self._session = None
            raise MCPConnectError(
                f"failed to connect to MCP server {self.config.name!r}: {exc}"
            ) from exc

        self._session = session
        log.info("mcp_connected", server=self.config.name)

    async def close(self) -> None:
        try:
            await self._stack.aclose()
        except Exception:
            log.exception("mcp_close_failed", server=self.config.name)
        finally:
            self._session = None

    @property
    def session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError("MCP client is not connected; call `connect()` first")
        return self._session

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self.session.list_tools()
        tools = [t.model_dump() for t in result.tools]
        log.debug("mcp_list_tools", server=self.config.name, count=len(tools))
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        log.info("mcp_call_tool", server=self.config.name, tool=name)
        log.debug(
            "mcp_call_tool_args",
            server=self.config.name,
            tool=name,
            argument_keys=sorted(arguments),
        )
        result = await self.session.call_tool(name, arguments=arguments)
        if result.isError:
            log.warning(
                "mcp_tool_returned_error",
                server=self.config.name,
                tool=name,
            )
            log.debug(
                "mcp_tool_error_full_content",
                server=self.config.name,
                tool=name,
                content=str(result.content),
            )
            raise MCPToolError(
                f"tool {name!r} returned an error: {_truncate_for_error(result.content)}"
            )
        return result.content
