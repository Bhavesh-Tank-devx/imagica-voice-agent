"""Tests for cross-cutting enums and disposition summaries."""
from src.constants import (
    DISPOSITION_CONVERTED,
    DISPOSITION_TRANSFERRED,
    AgentType,
    Disposition,
    disposition_summary,
)


def test_disposition_values_are_the_persisted_contract():
    # The string value is what lands in the CRM — it must not drift.
    assert Disposition.TRANSFERRED == "TRANSFERRED_TO_HUMAN"
    assert Disposition.CONVERTED == "CONVERTED"
    assert Disposition.NO_ANSWER == "NO_ANSWER"


def test_legacy_aliases_are_the_enum_members():
    assert DISPOSITION_CONVERTED is Disposition.CONVERTED
    assert DISPOSITION_TRANSFERRED is Disposition.TRANSFERRED


def test_disposition_is_usable_as_a_plain_string_key():
    mapping = {"CONVERTED": "ok"}
    assert mapping[Disposition.CONVERTED] == "ok"


def test_agent_type_values():
    assert AgentType.IMAGICA == "imagica"
    assert AgentType.KAYA == "kaya"


def test_disposition_summary_known_and_fallback():
    assert disposition_summary(Disposition.CONVERTED) == "Booking confirmed by customer."
    assert disposition_summary("SOMETHING_UNKNOWN") == "Call ended."
