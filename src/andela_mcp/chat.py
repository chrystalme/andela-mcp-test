from __future__ import annotations

import json
from dataclasses import dataclass, field
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

MAX_MESSAGE_CHARS = 8000
MAX_HISTORY_MESSAGES = 50

# Who is talking to the chatbot. The frontend asserts this on behalf of its
# authenticated session; the gateway trusts it (frontend ↔ gateway is m2m auth).
# Tool exposure is scoped per principal in build_function_tools.
Principal = Literal["anonymous", "customer", "staff"]
DEFAULT_PRINCIPAL: Principal = "anonymous"

# Hierarchical authorization: higher rank = more authority.
# anonymous < customer < staff.
_PRINCIPAL_RANK: dict[Principal, int] = {"anonymous": 0, "customer": 1, "staff": 2}

# Maps qualified `server__tool` name → minimum principal authorized to call it.
# Tools NOT listed here are staff-only (default-deny).
#
# Note: the order tools (list_orders, get_order, create_order) are listed at
# `anonymous` so the model can SEE them from the start and plan the
# verify-then-list flow. They are runtime-gated by CUSTOMER_SCOPING below: any
# non-staff caller must successfully call verify_customer_pin in the same
# /v1/chat invocation before the order tools execute, and results are then
# row-scoped to the verified customer_id. Staff bypasses the gate.
TOOL_MIN_PRINCIPAL: dict[str, Principal] = {
    # Public catalog
    "remote-mcp__list_products": "anonymous",
    "remote-mcp__get_product": "anonymous",
    "remote-mcp__search_products": "anonymous",
    # In-chat identity verification — anonymous callers use this to "log in"
    # mid-conversation, which unlocks the order tools below.
    "remote-mcp__verify_customer_pin": "anonymous",
    # Order tools — visible to anonymous + customer, but gated by the runtime
    # CUSTOMER_SCOPING wrapper (no scoping for staff).
    "remote-mcp__list_orders": "anonymous",
    "remote-mcp__get_order": "anonymous",
    "remote-mcp__create_order": "anonymous",
    # Admin only — system-wide reads (out of scope for current testing)
    "remote-mcp__get_customer": "staff",
}


def _principal_can_call(principal: Principal, qualified_tool: str) -> bool:
    """True if `principal` has sufficient authority to invoke `qualified_tool`.

    Default-deny: tools missing from TOOL_MIN_PRINCIPAL require `staff`."""
    required: Principal = TOOL_MIN_PRINCIPAL.get(qualified_tool, "staff")
    return _PRINCIPAL_RANK[principal] >= _PRINCIPAL_RANK[required]


# ── customer-scoped tool wrapping ────────────────────────────────────────────
#
# The upstream order-mcp is not session-aware: list_orders returns every order
# in the system regardless of the verify_customer_pin call. We cannot modify
# the upstream, so the gateway enforces row-level scoping itself:
#
# 1. verify_customer_pin: when it returns success, the gateway parses the
#    response and stores the verified customer_id on a per-/v1/chat-call
#    `_ChatSession`. This is the "login" step that elevates an anonymous
#    caller into the customer-data scope.
# 2. list_orders / get_order / create_order (for any non-staff principal):
#    refuse to run if no customer_id is captured ("verify first"); inject
#    customer_id into args when the upstream accepts it; post-filter the
#    response so only rows owned by the verified customer are returned.
#    Staff principal bypasses the gate entirely (admin sees everything).
#
# This is single-turn enforcement — session state resets between /v1/chat
# calls. Multi-turn flows require the client to re-verify (or a future
# cross-request session store).
#
# Field names below assume the upstream response shape — adjust if your tools
# use different key names. The `_extract_customer_id` and `_filter_by_customer`
# helpers walk MCP content blocks and parse the embedded JSON text payloads.

_VERIFY_TOOL = "remote-mcp__verify_customer_pin"
_VERIFY_RESPONSE_FIELD = "customer_id"  # field on verify response holding the customer_id
_ORDER_OWNER_FIELD = "customer_id"  # field on each order indicating its owning customer


@dataclass(frozen=True)
class _ScopeSpec:
    """How to enforce row-level scoping on a single customer-scoped tool."""

    inject_arg: str | None = None
    """Arg name to inject (overriding any agent-supplied value) with the
    verified customer_id. None = no injection (tool doesn't accept it)."""

    filter_rows_by: str | None = None
    """Field name on each row of a list-returning tool to filter by.
    None = response isn't a list / no row filter."""

    filter_single_by: str | None = None
    """Field name on a single-object-returning tool. If the returned
    object's value doesn't match the verified customer_id, return
    'not found' instead (don't leak existence). None = not applicable."""


CUSTOMER_SCOPING: dict[str, _ScopeSpec] = {
    "remote-mcp__list_orders": _ScopeSpec(
        inject_arg="customer_id",
        filter_rows_by=_ORDER_OWNER_FIELD,
    ),
    "remote-mcp__get_order": _ScopeSpec(
        filter_single_by=_ORDER_OWNER_FIELD,
    ),
    "remote-mcp__create_order": _ScopeSpec(
        inject_arg="customer_id",
    ),
}


@dataclass
class _ChatSession:
    """Per-/v1/chat-call state shared across tool invocations within one
    Runner.run. Captures the verified customer_id so subsequent scoped tools
    can enforce isolation."""

    verified_customer_id: str | None = None
    # Traces are kept here too so the scoping wrappers can record the
    # *filtered* result rather than the raw upstream response.
    traces: list[ToolCallTrace] = field(default_factory=list)


def _content_text(item: Any) -> str | None:
    """Pull the text out of one MCP content block (object or dict)."""
    text = getattr(item, "text", None)
    if text is None and isinstance(item, dict):
        text = item.get("text")
    return text if isinstance(text, str) else None


def _extract_customer_id(mcp_result: Any) -> str | None:
    """Best-effort: walk MCP content blocks, JSON-parse text payloads, return
    the first `customer_id` value found. Returns None if verification failed
    or the response can't be parsed."""
    items = mcp_result if isinstance(mcp_result, list) else [mcp_result]
    for item in items:
        text = _content_text(item)
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            value = parsed.get(_VERIFY_RESPONSE_FIELD)
            if isinstance(value, str | int):
                return str(value)
    return None


def _filter_content_rows(mcp_result: Any, customer_id: str, field_name: str) -> Any:
    """For a list-returning tool: keep only rows where `row[field_name]` == customer_id.
    Returns MCP content blocks identical in shape to the input."""
    items = mcp_result if isinstance(mcp_result, list) else [mcp_result]
    out: list[Any] = []
    for item in items:
        text = _content_text(item)
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, list):
            kept = [
                row
                for row in parsed
                if isinstance(row, dict) and str(row.get(field_name)) == customer_id
            ]
            out.append({"type": "text", "text": json.dumps(kept)})
    return out


def _filter_content_single(mcp_result: Any, customer_id: str, field_name: str) -> Any:
    """For a single-object-returning tool: drop the payload (return 'not
    found') if the object's `field_name` doesn't match customer_id. Never
    confirm/deny existence — same shape either way."""
    items = mcp_result if isinstance(mcp_result, list) else [mcp_result]
    for item in items:
        text = _content_text(item)
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict) and str(parsed.get(field_name)) == customer_id:
            return [{"type": "text", "text": json.dumps(parsed)}]
    return [{"type": "text", "text": "not found"}]


# Instructions are chosen per principal so the prompt never references tools
# the caller can't actually invoke (that produces hallucinated tool calls and
# 400s from the upstream model).
# The store has no separate cart entity — pending/draft orders are the cart.
# Every prompt below tells the model to map cart/basket language to list_orders
# (filtered by draft / pending status when appropriate).
_CART_NOTE = (
    "There is no separate cart concept — treat any mention of 'cart', 'basket', "
    "'pending items', or 'what's in my cart' as a request for the user's draft / "
    "unsubmitted orders via list_orders (filter for status='draft' or similar)."
)

_INSTRUCTIONS_CUSTOMER = (
    "You are a helpful customer service assistant for an online electronics store. "
    "You ALWAYS verify the customer first with verify_customer_pin(email, pin) "
    "before calling any order tool — list_orders, get_order, and create_order "
    "require verification and only act on the verified customer's own data. "
    "If the user asks about orders before providing their email and PIN, ask "
    "for them. Use list_products / search_products / get_product for catalog "
    f"questions. {_CART_NOTE} Keep replies concise."
)
_INSTRUCTIONS_STAFF = (
    "You are an internal admin assistant for an online electronics store with "
    "full access to system-wide data (all customers, all orders). Use the "
    f"provided tools to look up any customer, order, or product. {_CART_NOTE} "
    "Keep replies concise."
)
_INSTRUCTIONS_ANONYMOUS = (
    "You are a helpful customer service assistant for an online electronics store. "
    "You can answer product questions using list_products / get_product / "
    "search_products. To help a guest with their orders or to place an order, "
    "first verify their identity with verify_customer_pin(email, pin) — once "
    "verified, list_orders / get_order / create_order will be scoped to that "
    "customer. If the user asks about orders or wants to place one and hasn't "
    f"given their email and PIN, ask for them. {_CART_NOTE} Keep replies concise."
)


def _instructions_for(principal: Principal) -> str:
    if principal == "anonymous":
        return _INSTRUCTIONS_ANONYMOUS
    if principal == "customer":
        return _INSTRUCTIONS_CUSTOMER
    return _INSTRUCTIONS_STAFF


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
    session: _ChatSession | None = None,
) -> list[Tool]:
    """Wrap every MCP tool from every server as a FunctionTool.

    `principal` filters which tools are exposed (see TOOL_MIN_PRINCIPAL).
    For `customer`, tools listed in CUSTOMER_SCOPING are wrapped to enforce
    row-level scoping via the `session` (which captures customer_id from
    verify_customer_pin and gates / filters subsequent order tool calls).
    """
    if session is None:
        session = _ChatSession(traces=traces)
    tools: list[Tool] = []
    for server, client in clients.items():
        for t in await client.list_tools():
            qualified = _qualify(server, t["name"])
            if not _principal_can_call(principal, qualified):
                continue
            schema = _ensure_object_schema(t.get("inputSchema"))
            tools.append(
                _make_function_tool(server, client, t, schema, session, principal=principal)
            )
    return tools


def _make_function_tool(
    server: str,
    client: _MCPClientProto,
    tool_def: dict[str, Any],
    schema: dict[str, Any],
    session: _ChatSession,
    *,
    principal: Principal,
) -> FunctionTool:
    tool_name = tool_def["name"]
    qualified = _qualify(server, tool_name)
    description = (tool_def.get("description") or "").strip() or qualified

    captures_customer = qualified == _VERIFY_TOOL
    # Staff sees everything system-wide; anyone else who hits an order tool
    # must verify first and gets row-scoped to their own customer_id.
    scope: _ScopeSpec | None = CUSTOMER_SCOPING.get(qualified) if principal != "staff" else None

    async def on_invoke(_ctx: RunContextWrapper[Any], args_json: str) -> str:
        args: dict[str, Any] = json.loads(args_json) if args_json else {}

        # Pre-gate scoped tools: require a verified customer_id.
        if scope is not None and session.verified_customer_id is None:
            session.traces.append(
                ToolCallTrace(server=server, tool=tool_name, arguments=args, result=None)
            )
            return (
                "error: please verify your identity first by calling "
                "verify_customer_pin(email, pin)"
            )

        # Inject verified customer_id (overriding any agent-supplied value).
        if scope is not None and scope.inject_arg is not None:
            assert session.verified_customer_id is not None  # checked above
            args[scope.inject_arg] = session.verified_customer_id

        try:
            result = await client.call_tool(tool_name, args)
        except MCPToolError as exc:
            log.warning("chat_tool_error", server=server, tool=tool_name)
            session.traces.append(
                ToolCallTrace(server=server, tool=tool_name, arguments=args, result=None)
            )
            return f"error: {exc}"

        # Capture verified customer_id from verify_customer_pin's response.
        if captures_customer:
            cid = _extract_customer_id(result)
            if cid is not None:
                session.verified_customer_id = cid

        # Post-filter customer-scoped responses.
        if scope is not None:
            assert session.verified_customer_id is not None
            if scope.filter_rows_by is not None:
                result = _filter_content_rows(
                    result, session.verified_customer_id, scope.filter_rows_by
                )
            elif scope.filter_single_by is not None:
                result = _filter_content_single(
                    result, session.verified_customer_id, scope.filter_single_by
                )

        session.traces.append(
            ToolCallTrace(server=server, tool=tool_name, arguments=args, result=result)
        )
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
        max_turns: int = _DEFAULT_MAX_TURNS,
        groq_base_url: str = "https://api.groq.com/openai/v1",
    ) -> None:
        self._clients = clients
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
        session = _ChatSession()
        tools = await build_function_tools(
            self._clients, session.traces, principal=principal, session=session
        )
        agent: Agent[Any] = Agent(
            name="andela-mcp-chat",
            instructions=_instructions_for(principal),
            tools=tools,
            model=self._model,
        )
        result = await Runner.run(
            agent, input=_history_to_input(history), max_turns=self._max_turns
        )
        return ChatReply(
            reply=str(result.final_output or "").strip(),
            tool_calls=session.traces,
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
