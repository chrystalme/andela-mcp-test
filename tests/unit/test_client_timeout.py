from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from andela_mcp.client import MCPClient
from andela_mcp.config import MCPServerConfig, MCPTransport


def test_mcp_client_timeout_init() -> None:
    """Test that MCPClient accepts and stores timeout parameter."""
    config = MCPServerConfig(name="test", transport=MCPTransport.STDIO, command="echo")
    client = MCPClient(config, timeout=5.0)
    assert client._timeout == 5.0

    client_no_timeout = MCPClient(config)
    assert client_no_timeout._timeout is None


@pytest.mark.asyncio
async def test_mcp_client_list_tools_applies_timeout() -> None:
    """Test that list_tools applies timeout when configured."""
    config = MCPServerConfig(name="test", transport=MCPTransport.STDIO, command="echo")
    client = MCPClient(config, timeout=0.001)  # Very short timeout

    # Mock the session and its list_tools method to be slow
    mock_session = AsyncMock()

    async def slow_list_tools(*args, **kwargs):
        await asyncio.sleep(0.1)  # Longer than timeout
        return MagicMock(tools=[])

    mock_session.list_tools = slow_list_tools
    client._session = mock_session

    # Should raise TimeoutError
    with pytest.raises(asyncio.TimeoutError):
        await client.list_tools()


@pytest.mark.asyncio
async def test_mcp_client_list_tools_no_timeout_when_none() -> None:
    """Test that list_tools doesn't apply timeout when timeout is None."""
    config = MCPServerConfig(name="test", transport=MCPTransport.STDIO, command="echo")
    client = MCPClient(config, timeout=None)

    # Mock the session
    mock_session = AsyncMock()
    # Create a mock tool that has a model_dump method returning a dict
    mock_tool = MagicMock()
    mock_tool.model_dump.return_value = {"name": "test"}
    mock_session.list_tools.return_value = MagicMock(tools=[mock_tool])
    client._session = mock_session

    # Should work normally
    result = await client.list_tools()
    assert result == [{"name": "test"}]
    mock_session.list_tools.assert_awaited_once()


@pytest.mark.asyncio
async def test_mcp_client_call_tool_applies_timeout() -> None:
    """Test that call_tool applies timeout when configured."""
    config = MCPServerConfig(name="test", transport=MCPTransport.STDIO, command="echo")
    client = MCPClient(config, timeout=0.001)  # Very short timeout

    # Mock the session and its call_tool method to be slow
    mock_session = AsyncMock()

    async def slow_call_tool(*args, **kwargs):
        await asyncio.sleep(0.1)  # Longer than timeout
        return MagicMock(isError=False, content="result")

    mock_session.call_tool = slow_call_tool
    client._session = mock_session

    # Should raise TimeoutError
    with pytest.raises(asyncio.TimeoutError):
        await client.call_tool("test_tool", {})


@pytest.mark.asyncio
async def test_mcp_client_call_tool_no_timeout_when_none() -> None:
    """Test that call_tool doesn't apply timeout when timeout is None."""
    config = MCPServerConfig(name="test", transport=MCPTransport.STDIO, command="echo")
    client = MCPClient(config, timeout=None)

    # Mock the session
    mock_session = AsyncMock()
    mock_session.call_tool.return_value = MagicMock(isError=False, content="result")
    client._session = mock_session

    # Should work normally
    result = await client.call_tool("test_tool", {"arg": "value"})
    assert result == "result"
    mock_session.call_tool.assert_awaited_once_with("test_tool", arguments={"arg": "value"})
