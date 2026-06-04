"""
Specific tests for guardrail behavior to ensure only technical support
and customer service tasks are allowed
"""

import pytest

from andela_mcp.chat import build_function_tools


class _StubMCPClient:
    def __init__(self, tools):
        self._tools = tools

    async def list_tools(self):
        return self._tools

    async def call_tool(self, name, arguments):
        return f"result_from_{name}"


@pytest.mark.asyncio
async def test_anonymous_can_only_access_customer_service_tools():
    """Anonymous users should only see customer service appropriate tools"""
    # Mix of customer service and non-customer service tools
    tools = [
        # Customer service tools (should be visible to anonymous)
        {"name": "list_products", "description": "", "inputSchema": {"type": "object"}},
        {"name": "get_product", "description": "", "inputSchema": {"type": "object"}},
        {"name": "search_products", "description": "", "inputSchema": {"type": "object"}},
        {"name": "verify_customer_pin", "description": "", "inputSchema": {"type": "object"}},
        {"name": "list_orders", "description": "", "inputSchema": {"type": "object"}},
        {"name": "get_order", "description": "", "inputSchema": {"type": "object"}},
        {"name": "create_order", "description": "", "inputSchema": {"type": "object"}},
        # Non-customer service / administrative tools (should NOT be visible to anonymous)
        {
            "name": "get_customer",
            "description": "",
            "inputSchema": {"type": "object"},
        },  # Admin only
        {
            "name": "system_config",
            "description": "",
            "inputSchema": {"type": "object"},
        },  # Admin only
        {
            "name": "admin_stats",
            "description": "",
            "inputSchema": {"type": "object"},
        },  # Admin only
        {
            "name": "delete_user",
            "description": "",
            "inputSchema": {"type": "object"},
        },  # Admin only
        {
            "name": "modify_pricing",
            "description": "",
            "inputSchema": {"type": "object"},
        },  # Admin only
    ]

    client = _StubMCPClient(tools)

    # Test anonymous principal - use the actual server name from TOOL_MIN_PRINCIPAL
    anonymous_tools = await build_function_tools({"remote-mcp": client}, [], principal="anonymous")
    anonymous_names = {t.name for t in anonymous_tools}

    # Should only see customer service tools
    expected_customer_service = {
        "remote-mcp__list_products",
        "remote-mcp__get_product",
        "remote-mcp__search_products",
        "remote-mcp__verify_customer_pin",
        "remote-mcp__list_orders",
        "remote-mcp__get_order",
        "remote-mcp__create_order",
    }

    assert anonymous_names == expected_customer_service, (
        "Anonymous principal sees wrong tools. "
        f"Got: {anonymous_names}, Expected: {expected_customer_service}"
    )

    # Verify that administrative tools are NOT accessible
    admin_tools = {
        "remote-mcp__get_customer",
        "remote-mcp__system_config",
        "remote-mcp__admin_stats",
        "remote-mcp__delete_user",
        "remote-mcp__modify_pricing",
    }

    intersection = anonymous_names.intersection(admin_tools)
    assert len(intersection) == 0, (
        f"Anonymous principal incorrectly sees admin tools: {intersection}"
    )


@pytest.mark.asyncio
async def test_customer_can_only_access_customer_service_tools():
    """Customer users should only see customer service appropriate tools"""
    # Same tools as above
    tools = [
        # Customer service tools
        {"name": "list_products", "description": "", "inputSchema": {"type": "object"}},
        {"name": "get_product", "description": "", "inputSchema": {"type": "object"}},
        {"name": "search_products", "description": "", "inputSchema": {"type": "object"}},
        {"name": "verify_customer_pin", "description": "", "inputSchema": {"type": "object"}},
        {"name": "list_orders", "description": "", "inputSchema": {"type": "object"}},
        {"name": "get_order", "description": "", "inputSchema": {"type": "object"}},
        {"name": "create_order", "description": "", "inputSchema": {"type": "object"}},
        # Administrative tools
        {"name": "get_customer", "description": "", "inputSchema": {"type": "object"}},
        {"name": "system_config", "description": "", "inputSchema": {"type": "object"}},
        {"name": "admin_stats", "description": "", "inputSchema": {"type": "object"}},
    ]

    client = _StubMCPClient(tools)

    # Test customer principal
    customer_tools = await build_function_tools({"remote-mcp": client}, [], principal="customer")
    customer_names = {t.name for t in customer_tools}

    # Should see same as anonymous (customer service tools)
    expected_customer_service = {
        "remote-mcp__list_products",
        "remote-mcp__get_product",
        "remote-mcp__search_products",
        "remote-mcp__verify_customer_pin",
        "remote-mcp__list_orders",
        "remote-mcp__get_order",
        "remote-mcp__create_order",
    }

    assert customer_names == expected_customer_service, (
        "Customer principal sees wrong tools. "
        f"Got: {customer_names}, Expected: {expected_customer_service}"
    )


@pytest.mark.asyncio
async def test_staff_can_access_all_tools():
    """Staff users should be able to access both customer service and administrative tools"""
    tools = [
        # Customer service tools
        {"name": "list_products", "description": "", "inputSchema": {"type": "object"}},
        {"name": "get_product", "description": "", "inputSchema": {"type": "object"}},
        {"name": "verify_customer_pin", "description": "", "inputSchema": {"type": "object"}},
        {"name": "list_orders", "description": "", "inputSchema": {"type": "object"}},
        {"name": "get_order", "description": "", "inputSchema": {"type": "object"}},
        {"name": "create_order", "description": "", "inputSchema": {"type": "object"}},
        # Administrative tools
        {"name": "get_customer", "description": "", "inputSchema": {"type": "object"}},
        {"name": "system_config", "description": "", "inputSchema": {"type": "object"}},
        {"name": "admin_stats", "description": "", "inputSchema": {"type": "object"}},
    ]

    client = _StubMCPClient(tools)

    # Test staff principal
    staff_tools = await build_function_tools({"remote-mcp": client}, [], principal="staff")
    staff_names = {t.name for t in staff_tools}

    # Should see ALL tools
    expected_all_tools = {f"remote-mcp__{tool['name']}" for tool in tools}

    assert staff_names == expected_all_tools, (
        f"Staff principal sees wrong tools. Got: {staff_names}, Expected: {expected_all_tools}"
    )


@pytest.mark.asyncio
async def test_guardrail_prevents_unmapped_tools_for_non_staff():
    """Tools not explicitly mapped in TOOL_MIN_PRINCIPAL should be blocked for non-staff"""
    tools = [
        {
            "name": "list_products",
            "description": "",
            "inputSchema": {"type": "object"},
        },  # Mapped to anonymous
        {
            "name": "mystery_tool",
            "description": "",
            "inputSchema": {"type": "object"},
        },  # Not mapped - should default to staff
    ]

    client = _StubMCPClient(tools)

    # Anonymous should NOT see mystery_tool (defaults to staff)
    anonymous_tools = await build_function_tools({"remote-mcp": client}, [], principal="anonymous")
    anonymous_names = {t.name for t in anonymous_tools}
    assert "remote-mcp__mystery_tool" not in anonymous_names
    assert "remote-mcp__list_products" in anonymous_names

    # Customer should NOT see mystery_tool (defaults to staff)
    customer_tools = await build_function_tools({"remote-mcp": client}, [], principal="customer")
    customer_names = {t.name for t in customer_tools}
    assert "remote-mcp__mystery_tool" not in customer_names
    assert "remote-mcp__list_products" in customer_names

    # Staff SHOULD see mystery_tool
    staff_tools = await build_function_tools({"remote-mcp": client}, [], principal="staff")
    staff_names = {t.name for t in staff_tools}
    assert "remote-mcp__mystery_tool" in staff_names
    assert "remote-mcp__list_products" in staff_names


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
