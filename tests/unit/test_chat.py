from __future__ import annotations

import json
from typing import Any

import pytest

from andela_mcp.chat import (
    ChatMessage,
    ChatReply,
    ChatService,
    ToolCallTrace,
    _ensure_object_schema,
    _stringify_mcp_result,
    build_function_tools,
)
from andela_mcp.client import MCPToolError


class _StubMCPClient:
    def __init__(
        self,
        tools: list[dict[str, Any]],
        results: dict[str, Any] | None = None,
        errors: dict[str, BaseException] | None = None,
    ) -> None:
        self._tools = tools
        self._results = results or {}
        self._errors = errors or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[dict[str, Any]]:
        return self._tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        if name in self._errors:
            raise self._errors[name]
        return self._results.get(name, "ok")


def test_ensure_object_schema_normalizes_input() -> None:
    assert _ensure_object_schema(None)["type"] == "object"
    schema = _ensure_object_schema({"type": "string"})
    assert schema["type"] == "object"
    schema = _ensure_object_schema({"type": "object", "properties": {"x": {"type": "string"}}})
    assert schema["properties"] == {"x": {"type": "string"}}


def test_stringify_handles_strings_lists_and_objects() -> None:
    assert _stringify_mcp_result("plain") == "plain"
    assert _stringify_mcp_result([{"text": "a"}, {"text": "b"}]) == "a\nb"
    assert _stringify_mcp_result({"x": 1}) == "{'x': 1}"


@pytest.mark.asyncio
async def test_build_function_tools_qualifies_names_and_invokes_mcp() -> None:
    shop = _StubMCPClient(
        tools=[{"name": "list_products", "description": "list", "inputSchema": {"type": "object"}}],
        results={"list_products": [{"type": "text", "text": "ITEM-1"}]},
    )
    fs = _StubMCPClient(
        tools=[{"name": "read_file", "description": "", "inputSchema": {"type": "object"}}],
    )
    traces: list[ToolCallTrace] = []
    tools = await build_function_tools({"shop": shop, "fs": fs}, traces)

    names = {t.name for t in tools}
    assert names == {"shop__list_products", "fs__read_file"}

    list_tool = next(t for t in tools if t.name == "shop__list_products")
    out = await list_tool.on_invoke_tool(None, json.dumps({"category": "Computers"}))  # type: ignore[arg-type]
    assert out == "ITEM-1"
    assert shop.calls == [("list_products", {"category": "Computers"})]
    assert traces[0].server == "shop"
    assert traces[0].tool == "list_products"


@pytest.mark.asyncio
async def test_function_tool_records_mcp_error_as_string() -> None:
    shop = _StubMCPClient(
        tools=[{"name": "create_order", "description": "", "inputSchema": {"type": "object"}}],
        errors={"create_order": MCPToolError("out of stock")},
    )
    traces: list[ToolCallTrace] = []
    tools = await build_function_tools({"shop": shop}, traces)
    out = await tools[0].on_invoke_tool(None, "{}")  # type: ignore[arg-type]
    assert out.startswith("error:")
    assert traces[0].result is None


@pytest.mark.asyncio
async def test_respond_uses_runner_and_returns_traces(monkeypatch: pytest.MonkeyPatch) -> None:
    """ChatService.respond should run the agent and surface traces from invoked tools."""
    shop = _StubMCPClient(
        tools=[{"name": "list_products", "description": "x", "inputSchema": {"type": "object"}}],
        results={"list_products": "PROD-1"},
    )

    captured: dict[str, Any] = {}

    async def fake_run(agent: Any, *, input: Any, max_turns: int) -> Any:
        captured["agent"] = agent
        captured["input"] = input
        captured["max_turns"] = max_turns
        # simulate the model invoking the one tool
        for t in agent.tools:
            await t.on_invoke_tool(None, "{}")

        class _R:
            final_output = "Here are the products."

        return _R()

    from andela_mcp import chat as chat_mod

    monkeypatch.setattr(chat_mod.Runner, "run", staticmethod(fake_run))

    svc = ChatService(
        clients={"shop": shop},
        groq_api_key="gk-test",
        model="openai/gpt-oss-120b",
        max_turns=5,
    )
    reply = await svc.respond([ChatMessage(role="user", content="show products")])

    assert isinstance(reply, ChatReply)
    assert reply.reply == "Here are the products."
    assert len(reply.tool_calls) == 1
    assert reply.tool_calls[0].tool == "list_products"
    assert captured["max_turns"] == 5
    assert captured["input"] == [{"role": "user", "content": "show products"}]
