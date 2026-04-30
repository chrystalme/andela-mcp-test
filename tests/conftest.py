from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from andela_mcp.config import Environment, Settings
from andela_mcp.logging import configure_logging


@pytest.fixture(scope="session", autouse=True)
def _logging() -> None:
    configure_logging(Settings(environment=Environment.LOCAL, log_format="console"))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment=Environment.LOCAL,
        log_format="console",
        servers_config_path=tmp_path / "servers.json",
    )


@pytest.fixture
def servers_config_file(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "servers.json"
    yield path
    if path.exists():
        path.unlink()
