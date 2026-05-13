from __future__ import annotations

import json
from typing import Any, Literal, Protocol

from agents import (
    Agent,
    FunctionTool,
    OpenAIChatCompletionsModel,
    RunContextWrapper,
    Runner,
    Tool,
    set_default_openai_api,
    set_tracing_disabled,
    set_tracing_export_api_key,
)
from openai import AsyncOpenAI
from openai.types.responses import ResponseInputItemParam
from pydantic import BaseModel, Field

from andela_mcp.client import MCPClient, MCPToolError
from andela_mcp.logging import get_logger

log = get_logger(__name__)

_TOOL_NAME_SEPARATOR = "__"
_DEFAULT_MAX_TURNS = 10
_DEFAULT_INSTRUCTIONS = (
    "You are a helpful customer service assistant for an online electronics store. "
    "Use the provided tools to look up products, customers, and orders, and to "
    "create orders. Before creating an order or revealing customer-specific data, "
    "verify the customer's identity with verify_customer_pin. Keep replies concise."
)


MAX_MESSAGE_CHARS = 8000
MAX_HISTORY_MESSAGES = 50

# Who is talking to the chatbot. The frontend asserts this on behalf of its
# authenticated session; the gateway trusts it (frontend ↔ gateway is m2m auth).
# Tool exposure is scoped per principal in build_function_tools.
Principal = Literal["anonymous", "customer", "staff"]
DEFAULT_PRINCIPAL: Principal = "anonymous"

# Qualified `server__tool` names exposed to the `anonymous` principal. Empty by
# default — populate with the read-only tools you want public visitors to reach
# (e.g. {"remote-mcp__list_products", "remote-mcp__verify_customer_pin"}).
# `customer` and `staff` always see the full catalog.
ANONYMOUS_ALLOWED_TOOLS: frozenset[str] = frozenset()


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ToolCallTrace(BaseModel):
    server: str
    tool: str
    arguments: dict[str, Any]
    result: Any


class ChatReply(BaseModel):
    reply: str
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)


class _MCPClientProto(Protocol):
    async def list_tools(self) -> list[dict[str, Any]]: ...
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


def _qualify(server: str, tool: str) -> str:
    return f"{server}{_TOOL_NAME_SEPARATOR}{tool}"


def _stringify_mcp_result(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            text = getattr(item, "text", None)
            if text is None and isinstance(item, dict):
                text = item.get("text")
            parts.append(text if text is not None else str(item))
        return "\n".join(parts)
    return str(value)


def _ensure_object_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    """Force schema into an object-with-properties shape (Agents SDK requires it)."""
    base = dict(schema or {})
    if base.get("type") != "object":
        base = {"type": "object", "properties": {}}
    base.setdefault("properties", {})
    base.setdefault("additionalProperties", False)
    return base


async def build_function_tools(
    clients: dict[str, _MCPClientProto],
    traces: list[ToolCallTrace],
    *,
    principal: Principal = DEFAULT_PRINCIPAL,
) -> list[Tool]:
    """Wrap every MCP tool from every server as a FunctionTool that records traces.

    `principal` scopes the visible toolset: anonymous callers only see tools
    listed in ANONYMOUS_ALLOWED_TOOLS; customer and staff see the full catalog.
    """
    tools: list[Tool] = []
    for server, client in clients.items():
        for t in await client.list_tools():
            qualified = _qualify(server, t["name"])
            if principal == "anonymous" and qualified not in ANONYMOUS_ALLOWED_TOOLS:
                continue
            schema = _ensure_object_schema(t.get("inputSchema"))
            tools.append(_make_function_tool(server, client, t, schema, traces))
    return tools


def _make_function_tool(
    server: str,
    client: _MCPClientProto,
    tool_def: dict[str, Any],
    schema: dict[str, Any],
    traces: list[ToolCallTrace],
) -> FunctionTool:
    tool_name = tool_def["name"]
    qualified = _qualify(server, tool_name)
    description = (tool_def.get("description") or "").strip() or qualified

    async def on_invoke(_ctx: RunContextWrapper[Any], args_json: str) -> str:
        args: dict[str, Any] = json.loads(args_json) if args_json else {}
        try:
            result = await client.call_tool(tool_name, args)
        except MCPToolError as exc:
            log.warning("chat_tool_error", server=server, tool=tool_name)
            traces.append(ToolCallTrace(server=server, tool=tool_name, arguments=args, result=None))
            return f"error: {exc}"
        traces.append(ToolCallTrace(server=server, tool=tool_name, arguments=args, result=result))
        return _stringify_mcp_result(result)

    return FunctionTool(
        name=qualified,
        description=description,
        params_json_schema=schema,
        on_invoke_tool=on_invoke,
        strict_json_schema=False,
    )


def _history_to_input(history: list[ChatMessage]) -> list[ResponseInputItemParam]:
    items: list[ResponseInputItemParam] = []
    for m in history:
        items.append({"role": m.role, "content": m.content})
    return items


class ChatService:
    """OpenAI-Agents-SDK chatbot, model served via Groq, MCP tools via existing clients."""

    def __init__(
        self,
        clients: dict[str, _MCPClientProto],
        groq_api_key: str,
        model: str,
        instructions: str = _DEFAULT_INSTRUCTIONS,
        max_turns: int = _DEFAULT_MAX_TURNS,
        groq_base_url: str = "https://api.groq.com/openai/v1",
    ) -> None:
        self._clients = clients
        self._instructions = instructions
        self._max_turns = max_turns
        self._openai_client = AsyncOpenAI(
            api_key=groq_api_key,
            base_url=groq_base_url,
        )
        self._model = OpenAIChatCompletionsModel(model=model, openai_client=self._openai_client)

    async def respond(
        self,
        history: list[ChatMessage],
        principal: Principal = DEFAULT_PRINCIPAL,
    ) -> ChatReply:
        traces: list[ToolCallTrace] = []
        tools = await build_function_tools(self._clients, traces, principal=principal)
        agent: Agent[Any] = Agent(
            name="andela-mcp-chat",
            instructions=self._instructions,
            tools=tools,
            model=self._model,
        )
        result = await Runner.run(
            agent, input=_history_to_input(history), max_turns=self._max_turns
        )
        return ChatReply(
            reply=str(result.final_output or "").strip(),
            tool_calls=traces,
        )


def configure_tracing(openai_api_key: str | None) -> None:
    """Route Agents-SDK traces to OpenAI when a key is present, otherwise disable."""
    if openai_api_key:
        # Force chat completions API surface — gpt-oss / OpenRouter don't speak the Responses API.
        set_default_openai_api("chat_completions")
        set_tracing_export_api_key(openai_api_key)
    else:
        set_tracing_disabled(True)


def build_chat_service(
    *,
    clients: dict[str, MCPClient],
    groq_api_key: str,
    model: str,
    openai_api_key: str | None = None,
) -> ChatService:
    configure_tracing(openai_api_key)
    return ChatService(
        clients=dict(clients),
        groq_api_key=groq_api_key,
        model=model,
    )
