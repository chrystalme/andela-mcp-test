from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from pydantic import SecretStr

from andela_mcp.client import MCPToolError
from andela_mcp.config import Environment, Settings
from andela_mcp.server import create_app

_ADMIN_TOKEN = "test-admin-token"
_ADMIN_HEADERS = {"Authorization": f"Bearer {_ADMIN_TOKEN}"}


def _settings(tmp_path: Path, *, admin_token: str | None = _ADMIN_TOKEN) -> Settings:
    """Settings pointed at an empty-but-existing servers config so the
    lifespan never tries to spawn a real MCP subprocess (which would hang in
    CI for stdio entries like `uvx mcp-server-filesystem`)."""
    cfg = tmp_path / "servers.json"
    cfg.write_text('{"servers": []}', encoding="utf-8")
    return Settings(
        environment=Environment.LOCAL,
        log_format="console",
        servers_config_path=cfg,
        admin_token=SecretStr(admin_token) if admin_token is not None else None,
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
        resp = client.post(
            "/v1/tools/call",
            json={"server": "ghost", "tool": "x", "arguments": {}},
            headers=_ADMIN_HEADERS,
        )
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


def _client_with_stub(
    stub: _StubClient, tmp_path: Path, *, admin_token: str | None = _ADMIN_TOKEN
) -> TestClient:
    app = create_app(_settings(tmp_path, admin_token=admin_token))
    test_client = TestClient(app)
    test_client.__enter__()
    app.state.clients = {"fs": stub}
    return test_client


def test_call_tool_returns_502_on_mcp_tool_error(tmp_path: Path) -> None:
    tc = _client_with_stub(_StubClient(call_exc=MCPToolError("upstream failed: x")), tmp_path)
    try:
        resp = tc.post(
            "/v1/tools/call",
            json={"server": "fs", "tool": "t", "arguments": {}},
            headers=_ADMIN_HEADERS,
        )
    finally:
        tc.__exit__(None, None, None)
    assert resp.status_code == 502
    assert "upstream failed" in resp.json()["detail"]


def test_call_tool_returns_504_on_timeout(tmp_path: Path) -> None:
    tc = _client_with_stub(_StubClient(call_exc=TimeoutError()), tmp_path)
    try:
        resp = tc.post(
            "/v1/tools/call",
            json={"server": "fs", "tool": "t", "arguments": {}},
            headers=_ADMIN_HEADERS,
        )
    finally:
        tc.__exit__(None, None, None)
    assert resp.status_code == 504


def test_call_tool_returns_502_on_unexpected_error(tmp_path: Path) -> None:
    tc = _client_with_stub(_StubClient(call_exc=RuntimeError("session dropped")), tmp_path)
    try:
        resp = tc.post(
            "/v1/tools/call",
            json={"server": "fs", "tool": "t", "arguments": {}},
            headers=_ADMIN_HEADERS,
        )
    finally:
        tc.__exit__(None, None, None)
    assert resp.status_code == 502


def test_list_tools_returns_502_on_failure(tmp_path: Path) -> None:
    tc = _client_with_stub(_StubClient(list_exc=RuntimeError("session dropped")), tmp_path)
    try:
        resp = tc.get("/v1/tools", headers=_ADMIN_HEADERS)
    finally:
        tc.__exit__(None, None, None)
    assert resp.status_code == 502


def test_list_tools_returns_504_on_timeout(tmp_path: Path) -> None:
    tc = _client_with_stub(_StubClient(list_exc=TimeoutError()), tmp_path)
    try:
        resp = tc.get("/v1/tools", headers=_ADMIN_HEADERS)
    finally:
        tc.__exit__(None, None, None)
    assert resp.status_code == 504


def test_list_tools_success_returns_per_server_results(tmp_path: Path) -> None:
    tc = _client_with_stub(_StubClient(), tmp_path)
    try:
        resp = tc.get("/v1/tools", headers=_ADMIN_HEADERS)
    finally:
        tc.__exit__(None, None, None)
    assert resp.status_code == 200
    assert resp.json() == {"fs": [{"name": "ok"}]}


def test_list_tools_without_admin_header_returns_401(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        resp = client.get("/v1/tools")
    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate") == "Bearer"


def test_list_tools_with_wrong_token_returns_401(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        resp = client.get("/v1/tools", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_call_tool_without_admin_header_returns_401(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        resp = client.post(
            "/v1/tools/call",
            json={"server": "fs", "tool": "t", "arguments": {}},
        )
    assert resp.status_code == 401


def test_admin_routes_return_503_when_token_unset(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path, admin_token=None))
    with TestClient(app) as client:
        resp = client.get("/v1/tools", headers=_ADMIN_HEADERS)
    assert resp.status_code == 503
    assert "ANDELA_MCP_ADMIN_TOKEN" in resp.json()["detail"]


def test_chat_request_rejects_invalid_principal(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        resp = client.post(
            "/v1/chat",
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "principal": "admin",
            },
        )
    assert resp.status_code == 422


def test_chat_route_is_not_gated_by_admin_token(tmp_path: Path) -> None:
    """/v1/chat must remain reachable without admin auth (frontends call it).
    The request may fail downstream for unrelated reasons (no Groq key, model
    error), but it must not be rejected by require_admin with 401."""
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        resp = client.post(
            "/v1/chat",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code != 401
