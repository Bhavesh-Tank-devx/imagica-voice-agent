"""Tests for the two language-detection heuristics."""
from src.conversation.language import detect_language, detect_language_from_text


def test_detect_language_unknown_when_no_user_turns():
    assert detect_language([]) == "unknown"
    assert detect_language([{"role": "agent", "text": "hello"}]) == "unknown"


def test_detect_language_english_when_no_markers():
    transcript = [{"role": "user", "text": "yes please send it now"}]
    assert detect_language(transcript) == "english"


def test_detect_language_hindi_when_every_turn_has_markers():
    transcript = [
        {"role": "user", "text": "haan theek hai"},
        {"role": "user", "text": "main aata hoon"},
    ]
    assert detect_language(transcript) == "hindi"


def test_detect_language_hinglish_when_mixed():
    transcript = [
        {"role": "user", "text": "haan send it"},     # has marker
        {"role": "user", "text": "okay sounds good"},  # no marker
    ]
    assert detect_language(transcript) == "hinglish"


def test_detect_language_from_text_thresholds():
    assert detect_language_from_text("haan nahi kya theek bilkul") == "hindi"
    assert detect_language_from_text("please send the booking haan") == "hinglish"
    assert detect_language_from_text("please send the booking link now") == "english"
