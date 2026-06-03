#!/usr/bin/env python3
"""Test the fixed guardrail logic"""

from andela_mcp.guardrails.technical_support import is_technical_support_related

def test_fixed_guardrail():
    """Test the fixed is_technical_support_related function"""
    
    # Test cases: (input, expected_result, description)
    test_cases = [
        # Should be TRUE (support-related)
        ("What is the price of iPhone?", True, "Product inquiry"),
        ("How do I track my order?", True, "Order tracking"),
        ("How to return a product?", True, "Return process"),
        ("Can I exchange this item?", True, "Exchange inquiry"),
        ("I need help with my account", True, "Account help"),
        ("Where is your store located?", True, "Store location"),
        ("What are your store hours?", True, "Store hours"),
        ("Do you have any promotions?", True, "Promotions inquiry"),
        ("Hello", True, "Social greeting"),
        ("Thanks for your help", True, "Thanks"),
        ("Hi there", True, "Greeting"),
        ("Please help me", True, "Please request"),
        ("Sorry for the trouble", True, "Apology"),
        ("Nice to meet you", True, "Social pleasantry"),
        ("Have a good day", True, "Social pleasantry"),
        ("Goodbye", True, "Social pleasantry"),
        ("See you later", True, "Social pleasantry"),
        
        # Should be FALSE (non-support)
        ("Tell me a joke", False, "Joke request"),
        ("Write a poem about love", False, "Poem request"),
        ("What is the weather today?", False, "Weather query"),
        ("Explain quantum physics", False, "Science explanation"),
        ("What is 2+2?", False, "Math question"),
        ("Translate hello to Spanish", False, "Translation request"),
        ("Recommend a good book", False, "Book recommendation"),
        ("How to fix my computer?", False, "Technical fix (not store-related)"),
        ("What is the capital of France?", False, "General knowledge"),
        ("Who won the football match?", False, "Sports query"),
        ("Let's talk about movies", False, "Movie discussion"),
        ("Listen to this song", False, "Music request"),
        ("What's in the news today?", False, "News request"),
        ("", False, "Empty string"),
        ("   ", False, "Whitespace only"),
        ("\n\t ", False, "Whitespace and newlines"),
        
        # Edge cases
        ("product", True, "Single support keyword"),
        ("joke", False, "Single non-support keyword (should be caught by joke pattern)"),
        ("product joke", True, "Mixed: has support keyword"),
        ("joke product", True, "Mixed: has support keyword"),
        ("help", True, "Single support keyword 'help'"),
        ("store", True, "Single support keyword 'store'"),
        ("price", True, "Single support keyword 'price'"),
        ("order", True, "Single support keyword 'order'"),
        
        # Mixed cases with both support and non-support
        ("I want to write a joke about products", True, "Has support keyword 'products'"),
        ("Can you translate this help document?", True, "Has support keyword 'help'"),
        ("Let's calculate the price of this item", True, "Has support keyword 'price'"),
    ]
    
    print("Testing fixed guardrail logic...")
    print("=" * 60)
    
    passed = 0
    failed = 0
    
    for text, expected, description in test_cases:
        result = is_technical_support_related(text)
        if result == expected:
            print(f"✓ PASS: {description}")
            passed += 1
        else:
            print(f"✗ FAIL: {description}")
            print(f"   Text: {text!r}")
            print(f"   Expected: {expected}, Got: {result}")
            failed += 1
        print()
    
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    
    if failed == 0:
        print("All tests passed! 🎉")
        return True
    else:
        print("Some tests failed! ❌")
        return False

if __name__ == "__main__":
    test_fixed_guardrail()