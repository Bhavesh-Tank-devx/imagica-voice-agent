"""Shared pytest fixtures.

Every test runs against an isolated temporary SQLite database (the production
``post_call.db`` is never touched), and the FastAPI ``TestClient`` is created
*without* its lifespan so the background queue worker never starts and no real
Twilio dial is ever placed.
"""
import pytest
from fastapi.testclient import TestClient

from src.persistence import db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Point all persistence at a fresh temp DB for the duration of one test."""
    test_db = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", str(test_db))
    db.init_db()
    return str(test_db)


@pytest.fixture
def client() -> TestClient:
    """A TestClient with no lifespan — routes work, the queue worker does not run.

    Created lazily so the ``temp_db`` autouse fixture has already repointed the
    database before the app handles any request.
    """
    from src.main import app

    return TestClient(app)


@pytest.fixture
def imagica_cart() -> dict:
    """A representative Imagicaa in-call cart dict."""
    return {
        "agent_type": "imagica",
        "cart_id": "CART-TEST-001",
        "customer_name": "Bhavesh",
        "customer_phone": "+919913874598",
        "visit_date": "12 April 2026",
        "tickets": [{"type": "Adult", "quantity": 2, "price_per_unit": 1499}],
        "total_amount": 2998,
        "park_name": "Imagicaa Theme Park, Khopoli",
        "booking_link": "https://imagicaa.com/book?cart=CART-TEST-001",
        "attempt_number": 1,
    }


@pytest.fixture
def kaya_cart() -> dict:
    """A representative Kaya in-call cart dict."""
    return {
        "agent_type": "kaya",
        "cart_id": "KAYA-TEST-001",
        "customer_name": "Priya",
        "customer_phone": "+919876543210",
        "city": "Surat",
        "call_type": "OUTBOUND",
        "attempt_number": 1,
    }


def fresh_state() -> dict:
    """A fresh per-call mutable bridge state (mirrors media_stream_handler)."""
    from src.constants import Disposition

    return {
        "disposition": Disposition.NO_ANSWER,
        "discount": 0,
        "sms_sent": False,
        "tool_calls": [],
        "transcript": [],
        "latency_per_turn": [],
        "first_response_ms": None,
        "call_connected_at": "2026-06-23T10:00:00",
        "call_start": 0.0,
        "agent_type": "imagica",
        "_user_stopped_at": 0.0,
    }
