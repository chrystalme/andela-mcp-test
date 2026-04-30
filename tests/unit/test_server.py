from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from andela_mcp.client import MCPToolError
from andela_mcp.config import Environment, Settings
from andela_mcp.server import create_app


def _settings(tmp_path: Path) -> Settings:
    """Settings pointed at an empty-but-existing servers config so the
    lifespan never tries to spawn a real MCP subprocess (which would hang in
    CI for stdio entries like `uvx mcp-server-filesystem`)."""
    cfg = tmp_path / "servers.json"
    cfg.write_text('{"servers": []}', encoding="utf-8")
    return Settings(
        environment=Environment.LOCAL,
        log_format="console",
        servers_config_path=cfg,
    )


def test_healthz_returns_ok(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_request_id_header_round_trip(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        resp = client.get("/healthz", headers={"x-request-id": "abc-123"})
    assert resp.headers["x-request-id"] == "abc-123"


def test_call_tool_unknown_server_returns_404(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        resp = client.post("/v1/tools/call", json={"server": "ghost", "tool": "x", "arguments": {}})
    assert resp.status_code == 404


class _StubClient:
    def __init__(
        self, *, list_exc: BaseException | None = None, call_exc: BaseException | None = None
    ) -> None:
        self._list_exc = list_exc
        self._call_exc = call_exc

    async def list_tools(self) -> list[dict[str, Any]]:
        if self._list_exc is not None:
            raise self._list_exc
        return [{"name": "ok"}]

    async def call_tool(self, _name: str, _args: dict[str, Any]) -> Any:
        if self._call_exc is not None:
            raise self._call_exc
        return "ok"

    async def close(self) -> None:
        return None


def _client_with_stub(stub: _StubClient, tmp_path: Path) -> TestClient:
    app = create_app(_settings(tmp_path))
    test_client = TestClient(app)
    test_client.__enter__()
    app.state.clients = {"fs": stub}
    return test_client


def test_call_tool_returns_502_on_mcp_tool_error(tmp_path: Path) -> None:
    tc = _client_with_stub(_StubClient(call_exc=MCPToolError("upstream failed: x")), tmp_path)
    try:
        resp = tc.post("/v1/tools/call", json={"server": "fs", "tool": "t", "arguments": {}})
    finally:
        tc.__exit__(None, None, None)
    assert resp.status_code == 502
    assert "upstream failed" in resp.json()["detail"]


def test_call_tool_returns_504_on_timeout(tmp_path: Path) -> None:
    tc = _client_with_stub(_StubClient(call_exc=TimeoutError()), tmp_path)
    try:
        resp = tc.post("/v1/tools/call", json={"server": "fs", "tool": "t", "arguments": {}})
    finally:
        tc.__exit__(None, None, None)
    assert resp.status_code == 504


def test_call_tool_returns_502_on_unexpected_error(tmp_path: Path) -> None:
    tc = _client_with_stub(_StubClient(call_exc=RuntimeError("session dropped")), tmp_path)
    try:
        resp = tc.post("/v1/tools/call", json={"server": "fs", "tool": "t", "arguments": {}})
    finally:
        tc.__exit__(None, None, None)
    assert resp.status_code == 502


def test_list_tools_returns_502_on_failure(tmp_path: Path) -> None:
    tc = _client_with_stub(_StubClient(list_exc=RuntimeError("session dropped")), tmp_path)
    try:
        resp = tc.get("/v1/tools")
    finally:
        tc.__exit__(None, None, None)
    assert resp.status_code == 502


def test_list_tools_returns_504_on_timeout(tmp_path: Path) -> None:
    tc = _client_with_stub(_StubClient(list_exc=TimeoutError()), tmp_path)
    try:
        resp = tc.get("/v1/tools")
    finally:
        tc.__exit__(None, None, None)
    assert resp.status_code == 504


def test_list_tools_success_returns_per_server_results(tmp_path: Path) -> None:
    tc = _client_with_stub(_StubClient(), tmp_path)
    try:
        resp = tc.get("/v1/tools")
    finally:
        tc.__exit__(None, None, None)
    assert resp.status_code == 200
    assert resp.json() == {"fs": [{"name": "ok"}]}
