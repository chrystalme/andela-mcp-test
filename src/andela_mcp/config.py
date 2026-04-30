from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"


class MCPTransport(StrEnum):
    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"


class MCPServerConfig(BaseSettings):
    """Configuration for a single upstream MCP server this service consumes."""

    name: str
    transport: MCPTransport = MCPTransport.STDIO

    command: str | None = None
    args: list[str] = Field(default_factory=list)

    url: HttpUrl | None = None
    headers: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_transport(self) -> MCPServerConfig:
        if self.transport == MCPTransport.STDIO and not self.command:
            raise ValueError(f"server {self.name!r}: stdio transport requires `command`")
        if self.transport in {MCPTransport.HTTP, MCPTransport.SSE} and self.url is None:
            raise ValueError(f"server {self.name!r}: {self.transport} transport requires `url`")
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ANDELA_MCP_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    environment: Environment = Environment.LOCAL
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"

    host: str = "0.0.0.0"  # noqa: S104  # bind in container; restrict via firewall/IAM
    port: int = 8080

    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-opus-4-7"

    gcp_project_id: str | None = None

    servers_config_path: Path = Path("config/servers.json")

    @property
    def is_production(self) -> bool:
        return self.environment == Environment.PROD


def get_settings() -> Settings:
    return Settings()
