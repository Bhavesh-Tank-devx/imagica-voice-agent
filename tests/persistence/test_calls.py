"""Tests for call-log writes, reads, and metrics against a temp DB."""
from src.constants import Disposition
from src.persistence import (
    get_call_detail,
    get_call_logs,
    get_metrics,
    log_call,
)


def _log(cart_id="CART-1", disposition=Disposition.CONVERTED, **kw):
    cart = {
        "cart_id": cart_id,
        "customer_name": "Bhavesh",
        "customer_phone": "+91999",
        "attempt_number": kw.pop("attempt_number", 1),
        "agent_type": "imagica",
    }
    log_call(
        cart=cart,
        disposition=disposition,
        transcript=kw.pop("transcript", [{"role": "user", "text": "haan"}]),
        summary="ok",
        latency_per_turn=kw.pop("latency_per_turn", [500, 700]),
        first_response_ms=kw.pop("first_response_ms", 500),
        call_connected_at="2026-06-23T10:00:00",
        **kw,
    )


def test_log_call_is_readable_back():
    _log(cart_id="CART-A")
    rows = get_call_logs(cart_id="CART-A")
    assert len(rows) == 1
    assert rows[0]["disposition"] == "CONVERTED"
    assert rows[0]["customer_name"] == "Bhavesh"


def test_get_call_detail_parses_json_and_summarises_latency():
    _log(cart_id="CART-B", latency_per_turn=[400, 600, 800])
    row = get_call_logs(cart_id="CART-B")[0]
    detail = get_call_detail(row["id"])
    assert detail["transcript"] == [{"role": "user", "text": "haan"}]
    assert detail["latency_summary"]["avg_ms"] == 600
    assert detail["latency_summary"]["turns"] == 3


def test_get_call_detail_missing_returns_none():
    assert get_call_detail(999999) is None


def test_metrics_empty_db():
    assert get_metrics() == {"total_calls": 0}


def test_metrics_aggregates_dispositions_and_latency():
    _log(cart_id="CART-C", disposition=Disposition.CONVERTED, latency_per_turn=[500])
    _log(cart_id="CART-D", disposition=Disposition.NOT_INTERESTED, latency_per_turn=[1500])
    metrics = get_metrics()
    assert metrics["total_calls"] == 2
    assert metrics["disposition_distribution"]["CONVERTED"] == 1
    assert metrics["disposition_distribution"]["NOT_INTERESTED"] == 1
    assert metrics["voice_latency"]["total_turns_measured"] == 2
