from __future__ import annotations

import pytest
from agents.run_context import RunContextWrapper

from andela_mcp.guardrails.technical_support import (
    technical_support_input_guardrail,
    technical_support_output_guardrail,
    is_technical_support_related,
)


class MockAgent:
    """Mock agent for guardrail testing."""
    pass


@pytest.mark.parametrize(
    "text,expected",
    [
        # Technical support related - should return True
        ("What products do you have available?", True),
        ("I want to check the status of my order #12345", True),
        ("Can you help me return this item?", True),
        ("What's the price of the laptop model X?", True),
        ("I need to verify my account with my PIN", True),
        ("Do you have this product in stock?", True),
        ("How do I track my shipment?", True),
        ("What are your store hours?", True),
        ("I have a complaint about the product quality", True),
        ("Can I exchange this item for a different size?", True),
        ("", True),  # Empty string - no non-support patterns
        ("Hello", True),  # Single word, no patterns
        
        # Non-technical support - should return False
        ("Tell me a story about a robot", False),
        ("Write a poem about customer service", False),
        ("What is the capital of France?", False),
        ("Explain quantum physics to me", False),
        ("How do I bake a chocolate cake?", False),
        ("What's the weather like today?", False),
        ("Translate 'hello' to Spanish", False),
        ("Calculate the derivative of x^2", False),
        ("Write a Python script to sort a list", False),
        ("Analyze this sales data for trends", False),
        ("What are the latest movies playing?", False),
        ("Who won the football match yesterday?", False),
        ("What are the side effects of this medication?", False),
        ("What are the current political news?", False),
    ],
)
def test_is_technical_support_related(text: str, expected: bool) -> None:
    """Test the helper function that determines if text is support-related."""
    assert is_technical_support_related(text) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "input_text,should_trip",
    [
        # Should NOT trip (support-related)
        ("What products do you have?", False),
        ("I want to check my order status", False),
        ("Help me return an item", False),
        ("", False),  # Empty
        
        # Should trip (non-support)
        ("Tell me a story", True),
        ("Write a poem", True),
        ("What is 2+2?", True),
        ("Explain relativity", True),
    ],
)
async def test_technical_support_input_guardrail(input_text: str, should_trip: bool) -> None:
    """Test the input guardrail."""
    ctx = RunContextWrapper(None)
    agent = MockAgent()
    
    result = await technical_support_input_guardrail(ctx, agent, input_text)
    
    assert result.tripwire_triggered is should_trip
    assert "reason" in result.output_info
    assert "input" in result.output_info


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output_text,should_trip",
    [
        # Should NOT trip (support-related or social)
        ("We have laptops, phones, and tablets available.", False),
        ("Your order #12345 is shipped and will arrive tomorrow.", False),
        ("To return an item, please visit our returns page.", False),
        ("Hello! How can I help you today?", False),  # Social
        ("Thank you for contacting us!", False),  # Social
        ("", False),  # Empty
        
        # Should trip (non-support)
        ("Once upon a time, there was a robot...", True),
        ("The answer is 4.", True),
        ("According to Einstein's theory...", True),
    ],
)
async def test_technical_support_output_guardrail(output_text: str, should_trip: bool) -> None:
    """Test the output guardrail."""
    ctx = RunContextWrapper(None)
    agent = MockAgent()
    
    result = await technical_support_output_guardrail(ctx, agent, output_text)
    
    assert result.tripwire_triggered is should_trip
    assert "reason" in result.output_info
    assert "output" in result.output_info


def test_guardrail_integration_with_chat_service() -> None:
    """Test that guardrails can be imported and are callable."""
    # This test ensures the guardrails are properly structured
    assert callable(technical_support_input_guardrail)
    assert callable(technical_support_output_guardrail)
    assert callable(is_technical_support_related)