"""Tests for the priority call queue."""
import json

from src.persistence import (
    dequeue_next_call,
    enqueue_call,
    mark_queue_done,
    mark_queue_failed,
)
from src.persistence.db import get_connection


def _enqueue(cart_id, value, **kw):
    return enqueue_call(
        cart_id=cart_id,
        customer_name="Cust",
        customer_phone="+91999",
        cart_value=value,
        cart_data_json=json.dumps({"cart_id": cart_id}),
        **kw,
    )


def test_dequeue_returns_highest_value_first():
    _enqueue("LOW", 1000)
    _enqueue("HIGH", 9000)
    _enqueue("MID", 5000)
    assert dequeue_next_call()["cart_id"] == "HIGH"
    assert dequeue_next_call()["cart_id"] == "MID"
    assert dequeue_next_call()["cart_id"] == "LOW"


def test_dequeue_claims_row_so_it_is_not_returned_twice():
    _enqueue("ONLY", 100)
    first = dequeue_next_call()
    assert first["cart_id"] == "ONLY"
    assert dequeue_next_call() is None  # already in_progress


def test_scheduled_in_future_is_not_dispatched_yet():
    _enqueue("LATER", 100, scheduled_at="2999-01-01 00:00:00")
    assert dequeue_next_call() is None


def test_enqueue_upsert_resets_status_to_pending():
    _enqueue("DUP", 100)
    dequeue_next_call()  # -> in_progress
    _enqueue("DUP", 200, attempt_number=2)  # retry upsert
    row = dequeue_next_call()
    assert row["cart_id"] == "DUP"
    assert row["attempt_number"] == 2
    assert row["cart_value"] == 200


def test_mark_done_and_failed_update_status():
    qid = _enqueue("Q1", 100)
    mark_queue_done(qid)
    with get_connection(row_factory=True) as conn:
        status = conn.execute("SELECT status FROM call_queue WHERE id=?", (qid,)).fetchone()["status"]
    assert status == "done"

    qid2 = _enqueue("Q2", 100)
    mark_queue_failed(qid2)
    with get_connection(row_factory=True) as conn:
        status = conn.execute("SELECT status FROM call_queue WHERE id=?", (qid2,)).fetchone()["status"]
    assert status == "failed"
