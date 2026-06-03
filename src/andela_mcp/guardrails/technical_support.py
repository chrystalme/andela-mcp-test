from __future__ import annotations

import re
from typing import Any

from agents import input_guardrail, output_guardrail, GuardrailFunctionOutput
from agents.run_context import RunContextWrapper


# Define technical support/customer service topics
TECHNICAL_SUPPORT_TOPICS = {
    # Product inquiries
    'product', 'products', 'catalog', 'item', 'items', 'price', 'pricing', 'cost',
    'specification', 'specifications', 'feature', 'features', 'availability', 'stock',
    'brand', 'model', 'size', 'color', 'colors', 'dimension', 'weight',
    
    # Order management (customer service)
    'order', 'orders', 'purchase', 'buy', 'buying', 'cart', 'basket', 'checkout',
    'payment', 'pay', 'shipping', 'delivery', 'track', 'tracking', 'status',
    'return', 'returns', 'refund', 'refunds', 'exchange', 'exchange', 'warranty',
    'guarantee', 'support', 'help', 'issue', 'problem', 'complaint',
    
    # Account/service (customer service)
    'account', 'login', 'signin', 'sign in', 'pin', 'password', 'verify', 'verification',
    'profile', 'information', 'info', 'details', 'contact', 'email', 'phone',
    
    # General store inquiries
    'store', 'shop', 'shopping', 'store hours', 'location', 'locations', 'open',
    'closed', 'holiday', 'promotion', 'sale', 'discount', 'coupon', 'offer'
}

# Patterns that indicate non-technical support/generalization tasks
NON_SUPPORT_PATTERNS = [
    # Creative writing/general knowledge
    r'\b(story|poem|poetry|novel|book|write|writing|essay|article|blog)\b',
    r'\b(joke|funny|humor|comedy)\b',
    r'\b(translate|translation|language|spanish|french|german|chinese|japanese)\b',
    r'\b(calculate|math|mathematics|algebra|calculus|statistics|physics|chemistry)\b',
    r'\b(code|program|programming|software|algorithm|python|javascript|java)\b',
    r'\b(analyze|analysis|data|statistics|chart|graph|plot|visualize)\b',
    r'\b(recipe|cook|cooking|food|meal|restaurant)\b',
    r'\b(travel|trip|vacation|flight|hotel|destination)\b',
    r'\b(health|medical|doctor|medicine|diet|fitness|exercise|workout)\b',
    r'\b(news|politics|election|government|law|legal|court)\b',
    r'\b(sport|sports|game|team|player|match|tournament)\b',
    r'\b(music|song|album|artist|band|concert|listen)\b',
    r'\b(movie|film|video|tv|television|show|netflix|youtube)\b'
]


def is_technical_support_related(text: str) -> bool:
    """Check if text is related to technical support/customer service."""
    if not text or not text.strip():
        # For empty or whitespace-only strings, check if there are no non-support patterns
        # According to the original logic and tests, empty string should return True
        # when there are no non-support pattern matches
        text_lower = text.lower()
        non_support_matches = sum(1 for pattern in NON_SUPPORT_PATTERNS 
                                if re.search(pattern, text_lower))
        return non_support_matches == 0

    text_lower = text.lower()
    
    # Check for support-related keywords
    support_score = sum(1 for topic in TECHNICAL_SUPPORT_TOPICS if topic in text_lower)
    
    # Check for non-support patterns
    non_support_matches = sum(1 for pattern in NON_SUPPORT_PATTERNS 
                            if re.search(pattern, text_lower))
    
    # If there are non-support patterns, it's likely not support-related
    # Unless there are very strong support indicators (multiple support keywords)
    if non_support_matches > 0:
        # Only allow through if there are multiple strong support signals
        # and the non-support pattern is weak (like "sale" in a support context)
        # But for clear non-support patterns like "analyze", "side effects", etc., block
        # Let's check if the non-support pattern is a strong indicator
        strong_non_support_patterns = [
            r'\b(analyze|analysis)\b',
            r'\b(side effects)\b',
            r'\b(medication|medicine)\b',
            r'\b(translate|translation)\b',
            r'\b(calculate|math|mathematics)\b',
            r'\b(code|program|programming)\b',
            r'\b(story|poem|write)\b',
        ]
        strong_match = False
        for pattern in strong_non_support_patterns:
            if re.search(pattern, text_lower):
                strong_match = True
                break
        
        if strong_match:
            return False
        # For weak non-support matches (like "sale" in isolation), check support score
        if support_score >= 2:  # Require multiple support keywords to override weak non-support
            return True
        return False

    # If it has support keywords, it's support-related
    if support_score > 0:
        return True
    
    # If no support keywords and no non-support matches, 
    # check if it's a social pleasantry or very short greeting
    # But be more careful about social phrase matching
    social_phrases = {
        'hello', 'hi', 'hey', 'greetings', 'good morning', 'good afternoon', 
        'good evening', 'thank you', 'thanks', 'welcome', 'please', 'sorry', 
        'apologize', 'how are you', 'nice to meet you', 'have a good day', 
        'goodbye', 'bye', 'see you'
    }
    
    # Check for exact phrase matches or word boundary matches
    words_in_text = set(re.findall(r'\b\w+\b', text_lower))
    social_word_count = len(words_in_text.intersection(social_phrases))
    
    # If it's mostly social words, allow it
    total_words = len(words_in_text)
    if total_words > 0 and social_word_count / total_words > 0.5:  # At least half social words
        return True
    
    # Very short messages that are just pleasantries
    if len(text.strip()) <= 2 and text.strip() in {'hi', 'hello', 'thanks', 'thx', 'pls', 'sry'}:
        return True
        
    return False


@input_guardrail
async def technical_support_input_guardrail(
    ctx: RunContextWrapper[Any], 
    agent, 
    input: str
) -> GuardrailFunctionOutput:
    """
    Input guardrail that ensures user queries are related to technical support/customer service.
    """
    if not is_technical_support_related(input):
        return GuardrailFunctionOutput(
            output_info={
                "reason": "Input is not related to technical support or customer service"
            },
            tripwire_triggered=True
        )
    
    return GuardrailFunctionOutput(
        output_info={
            "reason": "Input is related to technical support/customer service"
        },
        tripwire_triggered=False
    )


@output_guardrail
async def technical_support_output_guardrail(
    ctx: RunContextWrapper[Any], 
    agent, 
    output: str
) -> GuardrailFunctionOutput:
    """
    Output guardrail that ensures agent responses stay within technical support bounds.
    """
    # Allow brief social pleasantries and clarifying questions
    social_phrases = {
        'hello', 'hi', 'hey', 'greetings', 'good morning', 'good afternoon', 'good evening',
        'thank you', 'thanks', 'welcome', 'please', 'sorry', 'apologize', 'how are you',
        'nice to meet you', 'have a good day', 'goodbye', 'bye', 'see you'
    }
    
    output_lower = output.lower().strip()
    
    # If it's mostly social pleasantries, allow it
    words = set(re.findall(r'\b\w+\b', output_lower))
    social_word_count = len(words.intersection(social_phrases))
    total_words = len(words)
    
    if total_words > 0 and social_word_count / total_words > 0.7:  # Mostly social
        return GuardrailFunctionOutput(
            output_info={
                "reason": "Output is social pleasantry"
            },
            tripwire_triggered=False
        )
    
    # Check if output contains non-support content
    if not is_technical_support_related(output):
        return GuardrailFunctionOutput(
            output_info={
                "reason": "Output contains non-technical support content"
            },
            tripwire_triggered=True
        )
    
    return GuardrailFunctionOutput(
        output_info={
            "reason": "Output is within technical support bounds"
        },
        tripwire_triggered=False
    )