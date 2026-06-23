"""Tests for building the in-call cart from a dequeued queue row."""
import json

from src.queue_worker import build_dispatch_cart


def _row(agent_type: str, payload: dict, attempt: int = 1) -> dict:
    return {
        "id": 1,
        "cart_id": "C-1",
        "attempt_number": attempt,
        "agent_type": agent_type,
        "cart_data": json.dumps(payload),
    }


def test_build_imagica_cart_has_booking_link_and_tickets():
    payload = {
        "customer_name": "Bhavesh",
        "customer_phone": "+91999",
        "visit_date": "1 May 2026",
        "tickets": [{"type": "Adult", "quantity": 2, "price_per_unit": 1499}],
        "total_amount": 2998,
    }
    cart = build_dispatch_cart(_row("imagica", payload, attempt=2))
    assert cart["agent_type"] == "imagica"
    assert cart["booking_link"].endswith("cart=C-1")
    assert cart["park_name"].startswith("Imagicaa")
    assert cart["tickets"] == payload["tickets"]
    assert cart["attempt_number"] == 2
    assert "call_placed_at" in cart


def test_build_kaya_cart_has_city_and_no_tickets():
    payload = {"customer_name": "Priya", "customer_phone": "+91888", "city": "Surat"}
    cart = build_dispatch_cart(_row("kaya", payload))
    assert cart["agent_type"] == "kaya"
    assert cart["city"] == "Surat"
    assert cart["call_type"] == "OUTBOUND"
    assert "tickets" not in cart
    assert "booking_link" not in cart
