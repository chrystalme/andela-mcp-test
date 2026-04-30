from __future__ import annotations

import pytest
from pydantic import ValidationError

from andela_mcp.config import Environment, MCPServerConfig, MCPTransport, Settings


def test_settings_defaults() -> None:
    s = Settings()
    assert s.environment == Environment.LOCAL
    assert s.is_production is False
    assert s.port == 8080


def test_settings_production_flag() -> None:
    assert Settings(environment=Environment.PROD).is_production is True


def test_stdio_server_requires_command() -> None:
    with pytest.raises(ValidationError, match="requires `command`"):
        MCPServerConfig(name="x", transport=MCPTransport.STDIO)


def test_http_server_requires_url() -> None:
    with pytest.raises(ValidationError, match="requires `url`"):
        MCPServerConfig(name="x", transport=MCPTransport.HTTP)


def test_stdio_server_valid() -> None:
    cfg = MCPServerConfig(name="fs", transport=MCPTransport.STDIO, command="uvx", args=["mcp-fs"])
    assert cfg.command == "uvx"
    assert cfg.args == ["mcp-fs"]


def test_http_server_valid() -> None:
    cfg = MCPServerConfig(
        name="remote",
        transport=MCPTransport.HTTP,
        url="https://example.com/mcp",  # type: ignore[arg-type]
    )
    assert str(cfg.url).startswith("https://")
