"""Heuristic language detection for customer speech (hindi / english / hinglish).

Two callers with different inputs: the realtime/bridge path classifies a full
transcript (turn-based), while the ElevenLabs-native post-call webhook classifies
a single concatenated text blob (word-ratio). Both are kept here.
"""
import re

# Markers for the turn-based transcript classifier (realtime + bridge path).
_TRANSCRIPT_HINDI_MARKERS = {
    "haan", "nahi", "theek", "aap", "kya", "main", "hai", "ho", "ji",
    "karo", "kal", "abhi", "bahut", "accha", "ek", "do", "teen", "baat",
    "kar", "mein", "se", "ko", "ka", "ki", "ke", "yeh", "woh", "toh",
    "phir", "hoon", "tha", "thi", "chahiye", "milega", "raha", "rahi",
    "suno", "dekho", "lena", "dena", "soch", "bilkul", "zaroor",
}

# Markers for the word-ratio text classifier (ElevenLabs-native path).
_TEXT_HINDI_MARKERS = {
    "haan", "nahi", "kya", "main", "aap", "theek", "bilkul",
    "zaroor", "accha", "baat", "kar", "rahi", "hun", "hai",
    "mujhe", "karo", "ho", "chahiye", "abhi", "baad",
}


def detect_language(transcript: list[dict]) -> str:
    """Classify the customer's language across a transcript of turns.

    Args:
        transcript: List of ``{"role", "text"}`` dicts.

    Returns:
        ``"english"`` (no Hindi markers), ``"hindi"`` (every turn has them),
        ``"hinglish"`` (mixed), or ``"unknown"`` (no user turns).
    """
    user_turns = [t["text"].lower() for t in transcript if t.get("role") == "user"]
    if not user_turns:
        return "unknown"

    turns_with_hindi = sum(
        1 for turn in user_turns if _TRANSCRIPT_HINDI_MARKERS.intersection(turn.split())
    )
    ratio = turns_with_hindi / len(user_turns)
    if ratio == 0.0:
        return "english"
    if ratio == 1.0:
        return "hindi"
    return "hinglish"


def detect_language_from_text(text: str) -> str:
    """Classify language from a single text blob by Hindi-marker word ratio.

    Args:
        text: Concatenated customer speech.

    Returns:
        ``"hindi"`` (ratio >= 0.6), ``"hinglish"`` (>= 0.2), else ``"english"``.
    """
    words = set(re.findall(r"[a-z]+", text.lower()))
    hindi_hits = words & _TEXT_HINDI_MARKERS
    ratio = len(hindi_hits) / max(len(words), 1)
    if ratio >= 0.6:
        return "hindi"
    if ratio >= 0.2:
        return "hinglish"
    return "english"
