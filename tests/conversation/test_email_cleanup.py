"""Tests for spoken-email normalisation and fuzzy name correction."""
import pytest

from src.conversation.email_cleanup import (
    fuzzy_correct_email,
    levenshtein,
    normalize_email,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("test at gmail dot com", "test@gmail.com"),
        ("BHAVESH at Gmail dot COM", "bhavesh@gmail.com"),
        ("user@domain.com", "user@domain.com"),
        ("  spaced @ gmail dot com ", "spaced@gmail.com"),
    ],
)
def test_normalize_email_fixes_spoken_forms(raw, expected):
    assert normalize_email(raw) == expected


@pytest.mark.parametrize("broken", ["justtext", "no-at-sign", "two@@signs.com"])
def test_normalize_email_returns_original_when_unfixable(broken):
    assert normalize_email(broken) == broken


def test_levenshtein_distance():
    assert levenshtein("kitten", "kitten") == 0
    assert levenshtein("kitten", "sitten") == 1
    assert levenshtein("kitten", "sitting") == 3


def test_fuzzy_correct_snaps_localpart_to_name():
    # ASR substitution 'sht' -> 'shre'; within 2 edits of "bhaveshtank".
    out = fuzzy_correct_email("bhaveshreank@gmail.com", "Bhavesh", "Tank")
    assert out == "bhaveshtank@gmail.com"


def test_fuzzy_correct_leaves_distant_localpart_untouched():
    out = fuzzy_correct_email("completelydifferent@gmail.com", "Bhavesh", "Tank")
    assert out == "completelydifferent@gmail.com"


def test_fuzzy_correct_passes_through_without_at_sign():
    assert fuzzy_correct_email("notanemail", "A", "B") == "notanemail"
