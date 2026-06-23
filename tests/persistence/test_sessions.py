"""Tests for ElevenLabs-native call-session persistence."""
from src.persistence import (
    append_tool_call,
    delete_session,
    get_session,
    save_session,
)


def test_save_and_load_session():
    save_session("conv-1", {"cart_id": "C1", "customer_name": "A"}, "2026-06-23T10:00:00")
    session = get_session("conv-1")
    assert session["cart"]["cart_id"] == "C1"
    assert session["initiated_at"] == "2026-06-23T10:00:00"
    assert session["tool_calls"] == []
    assert session["discount"] == 0


def test_get_missing_session_returns_none():
    assert get_session("nope") is None


def test_append_tool_call_accumulates_and_tracks_discount():
    save_session("conv-2", {"cart_id": "C2"}, "2026-06-23T10:00:00")
    append_tool_call("conv-2", {"tool": "send_booking_link"})
    append_tool_call("conv-2", {"tool": "apply_discount", "discount_percent": 7})
    session = get_session("conv-2")
    assert [t["tool"] for t in session["tool_calls"]] == ["send_booking_link", "apply_discount"]
    assert session["discount"] == 7


def test_append_to_missing_session_is_noop():
    append_tool_call("ghost", {"tool": "x"})  # must not raise
    assert get_session("ghost") is None


def test_delete_session():
    save_session("conv-3", {"cart_id": "C3"}, "2026-06-23T10:00:00")
    delete_session("conv-3")
    assert get_session("conv-3") is None
