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
    tools = await build_function_tools({"shop": shop, "fs": fs}, traces, principal="staff")

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
    tools = await build_function_tools({"shop": shop}, traces, principal="staff")
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
    reply = await svc.respond(
        [ChatMessage(role="user", content="show products")], principal="staff"
    )

    assert isinstance(reply, ChatReply)
    assert reply.reply == "Here are the products."
    assert len(reply.tool_calls) == 1
    assert reply.tool_calls[0].tool == "list_products"
    assert captured["max_turns"] == 5
    assert captured["input"] == [{"role": "user", "content": "show products"}]


@pytest.mark.asyncio
async def test_build_function_tools_default_deny_for_unmapped_tools() -> None:
    """Default TOOL_MIN_PRINCIPAL is empty — every tool is staff-only by default."""
    shop = _StubMCPClient(
        tools=[
            {"name": "list_products", "description": "x", "inputSchema": {"type": "object"}},
            {"name": "list_orders", "description": "x", "inputSchema": {"type": "object"}},
        ],
    )
    traces: list[ToolCallTrace] = []
    assert await build_function_tools({"shop": shop}, traces, principal="anonymous") == []
    assert await build_function_tools({"shop": shop}, traces, principal="customer") == []
    staff_tools = await build_function_tools({"shop": shop}, traces, principal="staff")
    assert {t.name for t in staff_tools} == {"shop__list_products", "shop__list_orders"}


@pytest.mark.asyncio
async def test_build_function_tools_hierarchical_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """anonymous < customer < staff. Each principal sees their tier and below."""
    from andela_mcp import chat as chat_mod

    monkeypatch.setattr(
        chat_mod,
        "TOOL_MIN_PRINCIPAL",
        {
            "shop__list_products": "anonymous",
            "shop__list_my_orders": "customer",
            "shop__list_orders": "staff",
        },
    )
    shop = _StubMCPClient(
        tools=[
            {"name": "list_products", "description": "x", "inputSchema": {"type": "object"}},
            {"name": "list_my_orders", "description": "x", "inputSchema": {"type": "object"}},
            {"name": "list_orders", "description": "x", "inputSchema": {"type": "object"}},
        ],
    )

    async def names_for(principal: str) -> set[str]:
        traces: list[ToolCallTrace] = []
        tools = await build_function_tools({"shop": shop}, traces, principal=principal)  # type: ignore[arg-type]
        return {t.name for t in tools}

    assert await names_for("anonymous") == {"shop__list_products"}
    assert await names_for("customer") == {"shop__list_products", "shop__list_my_orders"}
    assert await names_for("staff") == {
        "shop__list_products",
        "shop__list_my_orders",
        "shop__list_orders",
    }


@pytest.mark.asyncio
async def test_customer_order_tool_blocked_until_verify() -> None:
    """Customer principal calling list_orders without prior verify_customer_pin
    must be refused at the gateway — never reaches the upstream."""
    server = "remote-mcp"
    upstream = _StubMCPClient(
        tools=[
            {"name": "list_orders", "description": "x", "inputSchema": {"type": "object"}},
            {"name": "verify_customer_pin", "description": "x", "inputSchema": {"type": "object"}},
        ],
    )
    traces: list[ToolCallTrace] = []
    tools = await build_function_tools({server: upstream}, traces, principal="customer")
    list_orders = next(t for t in tools if t.name == "remote-mcp__list_orders")

    out = await list_orders.on_invoke_tool(None, "{}")  # type: ignore[arg-type]
    assert "verify your identity" in out
    assert upstream.calls == []  # upstream never called


@pytest.mark.asyncio
async def test_customer_verify_captures_id_then_list_orders_is_scoped() -> None:
    """End-to-end customer flow: verify → capture customer_id → list_orders
    gets the id injected and the returned rows are filtered to that customer."""
    server = "remote-mcp"
    upstream = _StubMCPClient(
        tools=[
            {"name": "verify_customer_pin", "description": "x", "inputSchema": {"type": "object"}},
            {"name": "list_orders", "description": "x", "inputSchema": {"type": "object"}},
        ],
        results={
            "verify_customer_pin": [
                {"type": "text", "text": json.dumps({"verified": True, "customer_id": "c-42"})}
            ],
            "list_orders": [
                {
                    "type": "text",
                    "text": json.dumps(
                        [
                            {"order_id": "o-1", "customer_id": "c-42", "total": 10},
                            {"order_id": "o-2", "customer_id": "c-99", "total": 20},
                            {"order_id": "o-3", "customer_id": "c-42", "total": 30},
                        ]
                    ),
                }
            ],
        },
    )
    traces: list[ToolCallTrace] = []
    tools = await build_function_tools({server: upstream}, traces, principal="customer")
    verify = next(t for t in tools if t.name == "remote-mcp__verify_customer_pin")
    list_orders = next(t for t in tools if t.name == "remote-mcp__list_orders")

    await verify.on_invoke_tool(  # type: ignore[arg-type]
        None, json.dumps({"email": "x@y.com", "pin": "1234"})
    )

    out = await list_orders.on_invoke_tool(None, "{}")  # type: ignore[arg-type]
    # Upstream got customer_id injected
    assert upstream.calls[-1] == ("list_orders", {"customer_id": "c-42"})
    # Output only contains c-42 rows
    parsed = json.loads(out)
    assert {row["order_id"] for row in parsed} == {"o-1", "o-3"}


@pytest.mark.asyncio
async def test_customer_get_order_returns_not_found_when_other_customer() -> None:
    """get_order doesn't accept customer_id — gateway must post-check the
    returned order's owner and hide it if it isn't the verified customer's."""
    server = "remote-mcp"
    upstream = _StubMCPClient(
        tools=[
            {"name": "verify_customer_pin", "description": "x", "inputSchema": {"type": "object"}},
            {"name": "get_order", "description": "x", "inputSchema": {"type": "object"}},
        ],
        results={
            "verify_customer_pin": [
                {"type": "text", "text": json.dumps({"customer_id": "c-42"})}
            ],
            "get_order": [
                {
                    "type": "text",
                    "text": json.dumps({"order_id": "o-stolen", "customer_id": "c-99"}),
                }
            ],
        },
    )
    traces: list[ToolCallTrace] = []
    tools = await build_function_tools({server: upstream}, traces, principal="customer")
    verify = next(t for t in tools if t.name == "remote-mcp__verify_customer_pin")
    get_order = next(t for t in tools if t.name == "remote-mcp__get_order")

    await verify.on_invoke_tool(None, "{}")  # type: ignore[arg-type]
    out = await get_order.on_invoke_tool(  # type: ignore[arg-type]
        None, json.dumps({"order_id": "o-stolen"})
    )
    assert out == "not found"


@pytest.mark.asyncio
async def test_customer_create_order_overrides_customer_id_argument() -> None:
    """If the model is tricked into passing a different customer_id, the
    gateway overrides it with the verified one."""
    server = "remote-mcp"
    upstream = _StubMCPClient(
        tools=[
            {"name": "verify_customer_pin", "description": "x", "inputSchema": {"type": "object"}},
            {"name": "create_order", "description": "x", "inputSchema": {"type": "object"}},
        ],
        results={
            "verify_customer_pin": [
                {"type": "text", "text": json.dumps({"customer_id": "c-42"})}
            ],
        },
    )
    traces: list[ToolCallTrace] = []
    tools = await build_function_tools({server: upstream}, traces, principal="customer")
    verify = next(t for t in tools if t.name == "remote-mcp__verify_customer_pin")
    create = next(t for t in tools if t.name == "remote-mcp__create_order")

    await verify.on_invoke_tool(None, "{}")  # type: ignore[arg-type]
    # Agent tries to pass a different customer_id — gateway must override.
    await create.on_invoke_tool(  # type: ignore[arg-type]
        None, json.dumps({"customer_id": "c-99-attacker", "items": []})
    )
    call_name, call_args = upstream.calls[-1]
    assert call_name == "create_order"
    assert call_args["customer_id"] == "c-42"


@pytest.mark.asyncio
async def test_anonymous_cannot_see_order_tools() -> None:
    """Anonymous principal: order tools are simply not in the visible set."""
    server = "remote-mcp"
    upstream = _StubMCPClient(
        tools=[
            {"name": "list_products", "description": "x", "inputSchema": {"type": "object"}},
            {"name": "verify_customer_pin", "description": "x", "inputSchema": {"type": "object"}},
            {"name": "list_orders", "description": "x", "inputSchema": {"type": "object"}},
            {"name": "get_order", "description": "x", "inputSchema": {"type": "object"}},
            {"name": "create_order", "description": "x", "inputSchema": {"type": "object"}},
        ],
    )
    traces: list[ToolCallTrace] = []
    tools = await build_function_tools({server: upstream}, traces, principal="anonymous")
    names = {t.name for t in tools}
    assert "remote-mcp__list_orders" not in names
    assert "remote-mcp__get_order" not in names
    assert "remote-mcp__create_order" not in names
    # But the public + verify tools ARE there
    assert "remote-mcp__list_products" in names
    assert "remote-mcp__verify_customer_pin" in names


@pytest.mark.asyncio
async def test_build_function_tools_unmapped_tool_is_staff_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool absent from TOOL_MIN_PRINCIPAL is reachable only by staff."""
    from andela_mcp import chat as chat_mod

    monkeypatch.setattr(chat_mod, "TOOL_MIN_PRINCIPAL", {"shop__list_products": "anonymous"})
    shop = _StubMCPClient(
        tools=[
            {"name": "list_products", "description": "x", "inputSchema": {"type": "object"}},
            {"name": "secret_admin_tool", "description": "x", "inputSchema": {"type": "object"}},
        ],
    )

    async def names_for(principal: str) -> set[str]:
        traces: list[ToolCallTrace] = []
        tools = await build_function_tools({"shop": shop}, traces, principal=principal)  # type: ignore[arg-type]
        return {t.name for t in tools}

    assert await names_for("customer") == {"shop__list_products"}  # admin tool hidden
    assert "shop__secret_admin_tool" in await names_for("staff")


@pytest.mark.asyncio
async def test_anonymous_instructions_do_not_reference_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anonymous gets no tools, so the prompt must not mention any tool name —
    otherwise the model hallucinates a tool call and Groq returns a 400."""
    shop = _StubMCPClient(tools=[])
    captured: dict[str, str] = {}

    async def fake_run(agent: Any, *, input: Any, max_turns: int) -> Any:
        captured["instructions"] = agent.instructions

        class _R:
            final_output = "hi"

        return _R()

    from andela_mcp import chat as chat_mod

    monkeypatch.setattr(chat_mod.Runner, "run", staticmethod(fake_run))
    svc = ChatService(clients={"shop": shop}, groq_api_key="gk-test", model="x")

    await svc.respond([ChatMessage(role="user", content="hi")], principal="anonymous")
    assert "verify_customer_pin" not in captured["instructions"]
    assert "do NOT have any tools" in captured["instructions"]

    await svc.respond([ChatMessage(role="user", content="hi")], principal="customer")
    assert "verify_customer_pin" in captured["instructions"]


@pytest.mark.asyncio
async def test_respond_forwards_principal_to_build_function_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shop = _StubMCPClient(
        tools=[{"name": "list_products", "description": "x", "inputSchema": {"type": "object"}}],
    )
    seen: dict[str, Any] = {}

    from andela_mcp import chat as chat_mod

    real_build = chat_mod.build_function_tools

    async def spy_build(*args: Any, **kwargs: Any) -> Any:
        seen["principal"] = kwargs.get("principal")
        return await real_build(*args, **kwargs)

    monkeypatch.setattr(chat_mod, "build_function_tools", spy_build)

    async def fake_run(agent: Any, *, input: Any, max_turns: int) -> Any:
        class _R:
            final_output = "ok"

        return _R()

    monkeypatch.setattr(chat_mod.Runner, "run", staticmethod(fake_run))

    svc = ChatService(clients={"shop": shop}, groq_api_key="gk-test", model="x")
    await svc.respond([ChatMessage(role="user", content="hi")], principal="customer")
    assert seen["principal"] == "customer"
