from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skip(reason="placeholder; wire up against a real MCP server in CI/staging")
async def test_real_mcp_round_trip() -> None:
    """Connect to a configured upstream MCP server and round-trip a tool call.

    Replace the skip with environment-driven config (e.g. ANDELA_MCP_INTEGRATION_URL)
    once a target server exists.
    """
