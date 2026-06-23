"""Conversation logic: prompts, language detection, and email cleanup."""
from src.conversation.email_cleanup import (
    fuzzy_correct_email,
    levenshtein,
    normalize_email,
)
from src.conversation.imagica_prompt import build_system_prompt
from src.conversation.kaya_branches import get_closest_branches
from src.conversation.kaya_prompt import build_kaya_system_prompt
from src.conversation.language import detect_language, detect_language_from_text

__all__ = [
    "build_system_prompt",
    "build_kaya_system_prompt",
    "get_closest_branches",
    "detect_language",
    "detect_language_from_text",
    "normalize_email",
    "fuzzy_correct_email",
    "levenshtein",
]
