#!/usr/bin/env python3
"""Test script to analyze and fix the guardrail logic"""

import re
from andela_mcp.guardrails.technical_support import TECHNICAL_SUPPORT_TOPICS, NON_SUPPORT_PATTERNS

def analyze_current_logic():
    """Analyze the current is_technical_support_related logic"""
    print("=== Analyzing Current Guardrail Logic ===")
    print()
    
    # Show the current logic
    print("Current logic in is_technical_support_related(text):")
    print("  1. support_score = count of TECHNICAL_SUPPORT_TOPICS found in text")
    print("  2. non_support_matches = count of NON_SUPPORT_PATTERNS matched in text")
    print("  3. IF non_support_matches > 0 AND support_score == 0: RETURN False")
    print("  4. ELSE RETURN (support_score > 0 OR non_support_matches == 0)")
    print()
    
    # Test cases that reveal the issue
    test_cases = [
        # Should be FALSE (non-support) but currently return TRUE
        ("What is the weather today?", False, "Weather query - no support keywords, no non-pattern matches"),
        ("What is 2+2?", False, "Math query - no support keywords, no non-pattern matches (doesn't contain math words)"),
        ("Who won the football match?", False, "Sports query - but sport pattern should catch this"),
        ("Let's talk about movies", False, "Movie discussion - movie pattern should catch this"),
        ("Translate hello to Spanish", False, "Translation - should be caught by translate pattern"),
        ("Recommend a good book", False, "Book recommendation - should be caught by book pattern"),
        ("Explain quantum physics", False, "Physics explanation - should be caught by physics pattern"),
        ("", False, "Empty string"),
        ("   ", False, "Whitespace only"),
        
        # Should be TRUE (support) and currently return TRUE
        ("What is the price of iPhone?", True, "Product inquiry"),
        ("How do I track my order?", True, "Order tracking"),
        ("How to return a product?", True, "Return process"),
        ("Can I exchange this item?", True, "Exchange inquiry"),
        ("I need help with my account", True, "Account help"),
        ("Where is your store located?", True, "Store location"),
        ("Hello", True, "Social greeting"),
        ("Thanks for your help", True, "Thanks"),
        
        # Edge cases that should work correctly
        ("product", True, "Single support keyword"),
        ("joke", False, "Single non-support keyword (should be caught by joke pattern)"),
        ("product joke", True, "Mixed: has support keyword"),
        ("joke product", True, "Mixed: has support keyword"),
    ]
    
    print("Testing current behavior:")
    print("-" * 80)
    
    issues_found = []
    
    for text, expected, description in test_cases:
        # Calculate what the current logic would return
        text_lower = text.lower()
        support_score = sum(1 for topic in TECHNICAL_SUPPORT_TOPICS if topic in text_lower)
        non_support_matches = sum(1 for pattern in NON_SUPPORT_PATTERNS 
                               if re.search(pattern, text_lower))
        
        # Current logic
        if non_support_matches > 0 and support_score == 0:
            result = False
        else:
            result = (support_score > 0 or non_support_matches == 0)
        
        status = "✓" if result == expected else "✗"
        if result != expected:
            issues_found.append((text, expected, result, description))
        
        print(f"{status} {description}")
        print(f"   Text: {text!r}")
        print(f"   Support score: {support_score}, Non-support matches: {non_support_matches}")
        print(f"   Expected: {expected}, Got: {result}")
        if result != expected:
            print(f"   *** ISSUE ***")
        print()
    
    print(f"Summary: {len(test_cases) - len(issues_found)}/{len(test_cases)} passed")
    if issues_found:
        print(f"Issues found: {len(issues_found)}")
        for text, expected, result, desc in issues_found:
            print(f"  - {text!r}: expected {expected}, got {result} ({desc})")
    
    return len(issues_found) == 0

def test_improved_logic():
    """Test an improved logic"""
    print("\n=== Testing Improved Logic ===")
    print()
    
    def is_technical_support_related_improved(text: str) -> bool:
        """Improved version of the guardrail logic"""
        if not text or not text.strip():
            return False
            
        text_lower = text.lower()
        
        # Check for support-related keywords
        support_score = sum(1 for topic in TECHNICAL_SUPPORT_TOPICS if topic in text_lower)
        
        # Check for non-support patterns
        non_support_matches = sum(1 for pattern in NON_SUPPORT_PATTERNS 
                               if re.search(pattern, text_lower))
        
        # If there are strong non-support indicators and weak/no support indicators, likely not support
        if non_support_matches > 0 and support_score == 0:
            return False
            
        # If there are support keywords, it's likely support-related
        if support_score > 0:
            return True
            
        # If no support keywords and no non-support matches, 
        # we need to decide based on other factors
        # For now, let's be conservative and say it's not support-related
        # unless it looks like a greeting or very short social phrase
        if non_support_matches == 0 and support_score == 0:
            # Check for social pleasantries
            social_phrases = {'hello', 'hi', 'hey', 'greetings', 'good morning', 'good afternoon', 
                            'good evening', 'thank you', 'thanks', 'welcome', 'please', 'sorry', 
                            'apologize', 'how are you', 'nice to meet you', 'have a good day', 
                            'goodbye', 'bye', 'see you'}
            words = set(re.findall(r'\b\w+\b', text_lower))
            if words.intersection(social_phrases):
                return True
            # Very short messages that are just pleasantries
            if len(text.strip()) <= 2 and text.strip() in {'hi', 'hello', 'thanks', 'thx', 'pls', 'sry'}:
                return True
            return False
            
        # Fallback (shouldn't reach here with the above logic)
        return support_score > 0
    
    # Test cases
    test_cases = [
        # Should be FALSE (non-support)
        ("What is the weather today?", False, "Weather query"),
        ("What is 2+2?", False, "Math query"),
        ("Who won the football match?", False, "Sports query"),
        ("Let's talk about movies", False, "Movie discussion"),
        ("Translate hello to Spanish", False, "Translation"),
        ("Recommend a good book", False, "Book recommendation"),
        ("Explain quantum physics", False, "Physics explanation"),
        ("Tell me a joke", False, "Joke request"),
        ("Write a poem about love", False, "Poem request"),
        ("", False, "Empty string"),
        ("   ", False, "Whitespace only"),
        
        # Should be TRUE (support)
        ("What is the price of iPhone?", True, "Product inquiry"),
        ("How do I track my order?", True, "Order tracking"),
        ("How to return a product?", True, "Return process"),
        ("Can I exchange this item?", True, "Exchange inquiry"),
        ("I need help with my account", True, "Account help"),
        ("Where is your store located?", True, "Store location"),
        ("Hello", True, "Social greeting"),
        ("Thanks for your help", True, "Thanks"),
        ("Hi there", True, "Greeting"),
        ("Please help me", True, "Please request"),
        ("Sorry for the trouble", True, "Apology"),
        
        # Edge cases
        ("product", True, "Single support keyword"),
        ("joke", False, "Single non-support keyword"),
        ("product joke", True, "Mixed: has support keyword"),
        ("joke product", True, "Mixed: has support keyword"),
    ]
    
    print("Testing improved logic:")
    print("-" * 80)
    
    passed = 0
    failed = 0
    
    for text, expected, description in test_cases:
        result = is_technical_support_related_improved(text)
        if result == expected:
            print(f"✓ PASS: {description}")
            passed += 1
        else:
            print(f"✗ FAIL: {description}")
            print(f"   Text: {text!r}")
            print(f"   Expected: {expected}, Got: {result}")
            failed += 1
        print()
    
    print(f"Results: {passed} passed, {failed} failed")
    return failed == 0

if __name__ == "__main__":
    print("Guardrail Analysis and Testing")
    print("=" * 50)
    
    # Analyze current logic
    current_ok = analyze_current_logic()
    
    # Test improved logic
    improved_ok = test_improved_logic()
    
    print("\n" + "=" * 50)
    print("SUMMARY:")
    print(f"Current logic analysis: {'PASS' if current_ok else 'FAIL (issues found)'}")
    print(f"Improved logic test: {'PASS' if improved_ok else 'FAIL'}")
    
    if not current_ok:
        print("\nThe current guardrail logic has issues and needs to be fixed.")
    else:
        print("\nThe current guardrail logic appears to be working correctly.")