"""Integration tests for the live webhook server via FastAPI TestClient.

The ``client`` fixture has no lifespan, so the background queue worker never
runs and no Twilio dial is ever placed — these tests exercise routing,
validation, gating, and the read endpoints only.
"""

from src.constants import Disposition
from src.persistence import log_call
from src.webhooks import service

VALID_CART = {
    "customer_name": "Bhavesh",
    "customer_phone": "+919913874598",
    "cart_id": "CART-API-1",
    "visit_date": "12 April 2026",
    "tickets": [{"type": "Adult", "quantity": 2, "price_per_unit": 1499}],
    "total_amount": 2998,
}


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_metrics_empty(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.json() == {"total_calls": 0}


def test_cart_abandoned_validation_error(client):
    resp = client.post("/webhook/cart-abandoned", json={"customer_name": "x"})
    assert resp.status_code == 422


def test_cart_abandoned_dnd_suppressed(client):
    dnd = next(iter(service.DND_LIST))
    resp = client.post("/webhook/cart-abandoned", json={**VALID_CART, "customer_phone": dnd})
    assert resp.status_code == 200
    assert resp.json()["status"] == "suppressed"


def test_cart_abandoned_queued(client, monkeypatch):
    monkeypatch.setattr(service, "is_calling_hours", lambda: True)
    resp = client.post("/webhook/cart-abandoned", json=VALID_CART)
    body = resp.json()
    assert resp.status_code == 200
    assert body["status"] == "queued"
    assert body["customer"] == "Bhavesh"
    assert body["cart_value"] == 2998


def test_kaya_lead_queued(client, monkeypatch):
    monkeypatch.setattr(service, "is_calling_hours", lambda: True)
    resp = client.post(
        "/webhook/kaya-lead",
        json={"customer_name": "Priya", "customer_phone": "+919876543210", "cart_id": "KAYA-API-1"},
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["status"] == "queued"
    assert body["agent_type"] == "kaya"


def test_call_ended_returns_204(client):
    resp = client.post("/webhook/call-ended", json={"conversation_id": "x", "status": "done"})
    assert resp.status_code == 204


def test_calls_list_reflects_logged_call(client):
    log_call(
        cart={"cart_id": "CART-LIST", "customer_name": "Bhavesh", "customer_phone": "+91"},
        disposition=Disposition.CONVERTED,
        transcript=[{"role": "user", "text": "haan"}],
        summary="ok",
    )
    resp = client.get("/calls")
    assert resp.status_code == 200
    calls = resp.json()["calls"]
    assert any(c["cart_id"] == "CART-LIST" for c in calls)


def test_call_detail_404(client):
    assert client.get("/calls/999999").status_code == 404


def test_kaya_appointments_endpoint_empty(client):
    resp = client.get("/api/kaya/appointments")
    assert resp.status_code == 200
    assert resp.json() == {"appointments": []}


def test_kaya_transcript_detail_404(client):
    assert client.get("/api/kaya/transcripts/999999").status_code == 404
