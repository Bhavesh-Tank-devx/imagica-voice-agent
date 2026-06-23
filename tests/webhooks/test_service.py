"""Tests for lead-intake gating (DND + calling hours) and enqueue."""
import json

from src.persistence import dequeue_next_call
from src.webhooks import service


def _enqueue(cart_id="CART-1", phone="+919913874598"):
    return service.gate_and_enqueue(
        cart_id=cart_id,
        customer_name="Bhavesh",
        customer_phone=phone,
        cart_value=2998,
        cart_data_json=json.dumps({"cart_id": cart_id}),
        attempt_number=1,
    )


def test_dnd_number_is_suppressed_without_enqueue():
    dnd = next(iter(service.DND_LIST))
    result = _enqueue(phone=dnd)
    assert result["status"] == "suppressed"
    assert result["reason"] == "DND list"
    assert dequeue_next_call() is None  # nothing was queued


def test_queued_immediately_during_calling_hours(monkeypatch):
    monkeypatch.setattr(service, "is_calling_hours", lambda: True)
    result = _enqueue(cart_id="CART-NOW")
    assert result["status"] == "queued"
    assert result["scheduled_at"] == "immediate"
    assert dequeue_next_call()["cart_id"] == "CART-NOW"


def test_scheduled_for_next_window_outside_calling_hours(monkeypatch):
    monkeypatch.setattr(service, "is_calling_hours", lambda: False)
    result = _enqueue(cart_id="CART-LATER")
    assert result["status"] == "queued"
    assert result["scheduled_at"] != "immediate"
    # A future-scheduled call is not immediately dispatchable.
    assert dequeue_next_call() is None


def test_next_calling_window_format():
    window = service.next_calling_window()
    # "YYYY-MM-DD HH:MM:SS"
    assert len(window) == 19
    assert window[4] == "-" and window[13] == ":"


def test_is_calling_hours_returns_bool():
    assert isinstance(service.is_calling_hours(), bool)
