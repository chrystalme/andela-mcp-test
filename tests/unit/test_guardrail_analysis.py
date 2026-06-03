"""
Test to verify the access control/guardrail behavior in andela-mcp chat service
"""
import json
from andela_mcp.chat import (
    build_function_tools,
    TOOL_MIN_PRINCIPAL,
    _principal_can_call,
    Principal
)

class _StubMCPClient:
    def __init__(self, tools):
        self._tools = tools
    
    async def list_tools(self):
        return self._tools
        
    async def call_tool(self, name, arguments):
        # Stub implementation - return success for testing
        return f"result_from_{name}"

def test_tool_visibility_by_principal():
    """Test that tools are correctly visible/hidden based on principal"""
    
    # Define a mix of tools that represent what might be available
    test_tools = [
        # Public catalog tools
        {"name": "list_products", "description": "", "inputSchema": {"type": "object"}},
        {"name": "get_product", "description": "", "inputSchema": {"type": "object"}},
        {"name": "search_products", "description": "", "inputSchema": {"type": "object"}},
        
        # Verification tool
        {"name": "verify_customer_pin", "description": "", "inputSchema": {"type": "object"}},
        
        # Order tools
        {"name": "list_orders", "description": "", "inputSchema": {"type": "object"}},
        {"name": "get_order", "description": "", "inputSchema": {"type": "object"}},
        {"name": "create_order", "description": "", "inputSchema": {"type": "object"}},
        
        # Admin/tools that should be staff-only
        {"name": "get_customer", "description": "", "inputSchema": {"type": "object"}},
        {"name": "system_config", "description": "", "inputSchema": {"type": "object"}},
        {"name": "admin_stats", "description": "", "inputSchema": {"type": "object"}},
    ]
    
    import asyncio
    
    async def run_tests():
        client = _StubMCPClient(test_tools)
        
        # Test anonymous principal
        anonymous_tools = await build_function_tools({"remote-mcp": client}, [], principal="anonymous")
        anonymous_names = {t.name for t in anonymous_tools}
        print(f"Anonymous can see: {sorted(anonymous_names)}")
        
        # Expected for anonymous: public tools + verification + order tools (visible but gated)
        expected_anonymous = {
            "remote-mcp__list_products",
            "remote-mcp__get_product", 
            "remote-mcp__search_products",
            "remote-mcp__verify_customer_pin",
            "remote-mcp__list_orders",
            "remote-mcp__get_order",
            "remote-mcp__create_order"
        }
        assert anonymous_names == expected_anonymous, f"Anonymous mismatch: got {anonymous_names}, expected {expected_anonymous}"
        
        # Test customer principal
        customer_tools = await build_function_tools({"remote-mcp": client}, [], principal="customer")
        customer_names = {t.name for t in customer_tools}
        print(f"Customer can see: {sorted(customer_names)}")
        
        # Customer should see same as anonymous (visible tools)
        assert customer_names == expected_anonymous, f"Customer mismatch: got {customer_names}, expected {expected_anonymous}"
        
        # Test staff principal
        staff_tools = await build_function_tools({"remote-mcp": client}, [], principal="staff")
        staff_names = {t.name for t in staff_tools}
        print(f"Staff can see: {sorted(staff_names)}")
        
        # Staff should see ALL tools
        expected_staff = {f"remote-mcp__{tool['name']}" for tool in test_tools}
        assert staff_names == expected_staff, f"Staff mismatch: got {staff_names}, expected {expected_staff}"
        
        print("✓ Tool visibility tests passed")
    
    asyncio.run(run_tests())

def test_principal_hierarchy():
    """Test the principal hierarchy logic"""
    # Test the _principal_can_call function directly with actual tool names
    print("Testing _principal_can_call function:")
    print(f"_PRINCIPAL_RANK: {dict(_principal_can_call.__globals__['_PRINCIPAL_RANK'])}")
    print(f"TOOL_MIN_PRINCIPAL: {dict(_principal_can_call.__globals__['TOOL_MIN_PRINCIPAL'])}")
    
    # Test with actual tools from TOOL_MIN_PRINCIPAL
    # Test anonymous principal accessing anonymous-level tools
    result = _principal_can_call("anonymous", "remote-mcp__list_products")
    print(f"_principal_can_call('anonymous', 'remote-mcp__list_products') = {result}")
    assert result == True, f"Expected True, got {result}"
    
    # Test anonymous principal accessing customer-level tool (should fail)
    result = _principal_can_call("anonymous", "remote-mcp__get_customer")  # This requires staff
    print(f"_principal_can_call('anonymous', 'remote-mcp__get_customer') = {result}")
    assert result == False, f"Expected False, got {result}"
    
    # Test customer principal accessing anonymous-level tools
    result = _principal_can_call("customer", "remote-mcp__list_products")
    print(f"_principal_can_call('customer', 'remote-mcp__list_products') = {result}")
    assert result == True, f"Expected True, got {result}"
    
    # Test customer principal accessing customer-level tools (none in current mapping, but let's test staff tool)
    result = _principal_can_call("customer", "remote-mcp__get_customer")  # Requires staff
    print(f"_principal_can_call('customer', 'remote-mcp__get_customer') = {result}")
    assert result == False, f"Expected False, got {result}"
    
    # Test staff principal accessing any tool
    result = _principal_can_call("staff", "remote-mcp__list_products")
    print(f"_principal_can_call('staff', 'remote-mcp__list_products') = {result}")
    assert result == True, f"Expected True, got {result}"
    
    result = _principal_can_call("staff", "remote-mcp__get_customer")
    print(f"_principal_can_call('staff', 'remote-mcp__get_customer') = {result}")
    assert result == True, f"Expected True, got {result}"
    
    # Test default-deny (tools not in TOOL_MIN_PRINCIPAL require staff)
    result = _principal_can_call("anonymous", "unknown_tool")
    print(f"_principal_can_call('anonymous', 'unknown_tool') = {result}")
    assert result == False, f"Expected False, got {result}"
    
    result = _principal_can_call("customer", "unknown_tool")
    print(f"_principal_can_call('customer', 'unknown_tool') = {result}")
    assert result == False, f"Expected False, got {result}"
    
    result = _principal_can_call("staff", "unknown_tool")
    print(f"_principal_can_call('staff', 'unknown_tool') = {result}")
    assert result == True, f"Expected True, got {result}"
    
    print("✓ Principal hierarchy tests passed")

def test_current_tool_mappings():
    """Test that the current TOOL_MIN_PRINCIPAL mapping makes sense for customer service"""
    print("\nCurrent TOOL_MIN_PRINCIPAL mapping:")
    for tool, principal in TOOL_MIN_PRINCIPAL.items():
        print(f"  {tool}: {principal}")
    
    # Verify that customer service appropriate tools are accessible to anonymous/customers
    customer_service_tools = [
        "remote-mcp__list_products",
        "remote-mcp__get_product", 
        "remote-mcp__search_products",
        "remote-mcp__verify_customer_pin",
        "remote-mcp__list_orders",
        "remote-mcp__get_order",
        "remote-mcp__create_order"
    ]
    
    for tool in customer_service_tools:
        required = TOOL_MIN_PRINCIPAL.get(tool, "staff")  # default to staff if not listed
        print(f"Tool {tool} requires: {required}")
        assert required in ["anonymous", "customer"], f"Customer service tool {tool} requires {required}"
    
    # Verify that administrative tools are properly restricted
    admin_tools = [
        "remote-mcp__get_customer"
    ]
    
    for tool in admin_tools:
        required = TOOL_MIN_PRINCIPAL.get(tool, "staff")  # default to staff if not listed
        print(f"Admin tool {tool} requires: {required}")
        assert required == "staff", f"Admin tool {tool} requires {required}, expected staff"
    
    print("✓ Current tool mapping tests passed")

if __name__ == "__main__":
    test_principal_hierarchy()
    test_current_tool_mappings()
    test_tool_visibility_by_principal()
    print("\n🎉 All guardrail/access control tests passed!")