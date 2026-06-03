#!/usr/bin/env python3
"""Final verification that the guardrails are working correctly"""

import asyncio
from andela_mcp.guardrails.technical_support import (
    technical_support_input_guardrail,
    technical_support_output_guardrail,
    is_technical_support_related
)
from agents.run_context import RunContextWrapper
from agents import Agent

class MockAgent:
    pass

# Create a proper RunContextWrapper
def create_mock_context():
    return RunContextWrapper(context=None)

async def test_guardrail_functions():
    """Test that the guardrail functions work as expected"""
    print("Testing guardrail functions...")
    print("=" * 50)
    
    # Test cases for input guardrail
    test_cases = [
        # Should NOT tripwire (allowed)
        ("What is the price of iPhone?", False, "Product inquiry - should be allowed"),
        ("How do I track my order?", False, "Order tracking - should be allowed"),
        ("Hello", False, "Greeting - should be allowed"),
        ("Thanks", False, "Thanks - should be allowed"),
        ("", True, "Empty string - should be blocked"),
        ("   ", True, "Whitespace only - should be blocked"),
        ("What is the weather today?", True, "Weather query - should be blocked"),
        ("Tell me a joke", True, "Joke request - should be blocked"),
        ("Write a poem", True, "Poem request - should be blocked"),
        ("What is 2+2?", True, "Math question - should be blocked"),
    ]
    
    print("Testing input guardrail:")
    ctx = create_mock_context()
    agent = MockAgent()
    
    passed = 0
    total = len(test_cases)
    
    for text, should_trip, description in test_cases:
        try:
            result = await technical_support_input_guardrail(ctx, agent, text)
            tripped = result.tripwire_triggered
            if tripped == should_trip:
                print(f"✓ PASS: {description}")
                print(f"   Text: {text!r}")
                print(f"   Tripwire: {tripped} (expected {should_trip})")
                passed += 1
            else:
                print(f"✗ FAIL: {description}")
                print(f"   Text: {text!r}")
                print(f"   Tripwire: {tripped} (expected {should_trip})")
                print(f"   Reason: {result.output_info.get('reason', 'N/A')}")
        except Exception as e:
            print(f"✗ ERROR: {description}")
            print(f"   Text: {text!r}")
            print(f"   Error: {e}")
        print()
    
    print(f"Input guardrail results: {passed}/{total} passed")
    print()
    
    # Test is_technical_support_related directly
    print("Testing is_technical_support_related function:")
    related_cases = [
        ("What is the price of iPhone?", True),
        ("How do I track my order?", True),
        ("Hello", True),
        ("Thanks", True),
        ("Please help me", True),
        ("", False),
        ("   ", False),
        ("What is the weather today?", False),
        ("Tell me a joke", False),
        ("Write a poem about love", False),
        ("What is 2+2?", False),
        ("Translate hello to Spanish", False),
        ("Recommend a good book", False),
        ("How to fix my computer?", False),
        ("What is the capital of France?", False),
        ("Who won the football match?", False),
        ("Let's talk about movies", False),
        ("Listen to this song", False),
        ("What's in the news today?", False),
        ("product", True),
        ("joke", False),
        ("help", True),
        ("store", True),
        ("price", True),
        ("order", True),
    ]
    
    passed2 = 0
    total2 = len(related_cases)
    
    for text, expected in related_cases:
        result = is_technical_support_related(text)
        if result == expected:
            print(f"✓ PASS: {text!r} -> {result}")
            passed2 += 1
        else:
            print(f"✗ FAIL: {text!r} -> {result} (expected {expected})")
        print()
    
    print(f"Direct function results: {passed2}/{total2} passed")
    print()
    
    # Overall result
    if passed == total and passed2 == total2:
        print("🎉 All guardrail tests passed!")
        return True
    else:
        print("❌ Some guardrail tests failed!")
        return False

if __name__ == "__main__":
    success = asyncio.run(test_guardrail_functions())
    exit(0 if success else 1)