from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from andela_mcp.client import (
    MCPClient,
    MCPConnectError,
    MCPToolError,
    ServersConfigError,
    _truncate_for_error,
    load_server_configs,
)
from andela_mcp.config import MCPServerConfig, MCPTransport


def test_load_server_configs_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_server_configs(tmp_path / "nope.json") == []


def test_load_server_configs_parses_entries(tmp_path: Path) -> None:
    path = tmp_path / "servers.json"
    path.write_text(
        json.dumps(
            {
                "servers": [
                    {"name": "fs", "transport": "stdio", "command": "uvx", "args": ["mcp-fs"]},
                    {"name": "remote", "transport": "http", "url": "https://example.com/mcp"},
                ]
            }
        )
    )

    configs = load_server_configs(path)

    assert [c.name for c in configs] == ["fs", "remote"]
    assert configs[0].transport == MCPTransport.STDIO
    assert configs[1].transport == MCPTransport.HTTP


def test_load_server_configs_invalid_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "servers.json"
    path.write_text("{not-json")
    with pytest.raises(ServersConfigError, match="invalid JSON"):
        load_server_configs(path)


def test_load_server_configs_invalid_schema_raises(tmp_path: Path) -> None:
    path = tmp_path / "servers.json"
    path.write_text(json.dumps({"servers": [{"name": "bad", "transport": "stdio"}]}))
    with pytest.raises(ServersConfigError, match="invalid server config"):
        load_server_configs(path)


def test_load_server_configs_expands_set_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_TEST_TOKEN", "abc123")
    path = tmp_path / "servers.json"
    path.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "name": "remote",
                        "transport": "http",
                        "url": "https://example.com/mcp",
                        "headers": {"Authorization": "Bearer ${MCP_TEST_TOKEN}"},
                    }
                ]
            }
        )
    )
    configs = load_server_configs(path)
    assert configs[0].headers == {"Authorization": "Bearer abc123"}


def test_load_server_configs_drops_header_when_referenced_var_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MCP_TEST_TOKEN", raising=False)
    path = tmp_path / "servers.json"
    path.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "name": "remote",
                        "transport": "http",
                        "url": "https://example.com/mcp",
                        "headers": {
                            "Authorization": "Bearer ${MCP_TEST_TOKEN}",
                            "X-Static": "hello",
                        },
                    }
                ]
            }
        )
    )
    configs = load_server_configs(path)
    # The Authorization header is dropped (would have been "Bearer " — illegal),
    # but the static header survives.
    assert configs[0].headers == {"X-Static": "hello"}


def test_client_session_unavailable_before_connect() -> None:
    client = MCPClient(MCPServerConfig(name="fs", transport=MCPTransport.STDIO, command="echo"))
    with pytest.raises(RuntimeError, match="not connected"):
        _ = client.session


@pytest.mark.asyncio
async def test_mcp_client_connect_failure_wraps_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("boom")

    monkeypatch.setattr("andela_mcp.client.stdio_client", boom)
    client = MCPClient(MCPServerConfig(name="fs", transport=MCPTransport.STDIO, command="echo"))

    with pytest.raises(MCPConnectError, match="failed to connect"):
        await client.connect()

    with pytest.raises(RuntimeError, match="not connected"):
        _ = client.session


@pytest.mark.asyncio
async def test_mcp_client_connect_http_failure_wraps(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("http boom")

    monkeypatch.setattr("andela_mcp.client.streamablehttp_client", boom)
    client = MCPClient(
        MCPServerConfig(name="r", transport=MCPTransport.HTTP, url="https://example.com/mcp")
    )
    with pytest.raises(MCPConnectError, match="failed to connect"):
        await client.connect()


@pytest.mark.asyncio
async def test_mcp_client_connect_sse_failure_wraps(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("sse boom")

    monkeypatch.setattr("andela_mcp.client.sse_client", boom)
    client = MCPClient(
        MCPServerConfig(name="r", transport=MCPTransport.SSE, url="https://example.com/sse")
    )
    with pytest.raises(MCPConnectError, match="failed to connect"):
        await client.connect()


def test_truncate_for_error_short_passthrough() -> None:
    assert _truncate_for_error("hi") == "hi"


def test_truncate_for_error_long_truncates() -> None:
    out = _truncate_for_error("x" * 1000, limit=10)
    assert out.startswith("xxxxxxxxxx...")
    assert "1000 chars total" in out


def test_truncate_for_error_rejects_negative_limit() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        _truncate_for_error("anything", limit=-1)


@pytest.mark.asyncio
async def test_call_tool_raises_mcptool_error_on_upstream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Result:
        isError = True
        content = "y" * 5000

    class _Session:
        async def call_tool(self, *_a: Any, **_kw: Any) -> _Result:
            return _Result()

    client = MCPClient(MCPServerConfig(name="fs", transport=MCPTransport.STDIO, command="echo"))
    client._session = _Session()  # type: ignore[assignment]

    with pytest.raises(MCPToolError) as exc_info:
        await client.call_tool("t", {})
    msg = str(exc_info.value)
    assert "truncated" in msg
    assert len(msg) < 1000  # full 5000-char content not echoed
