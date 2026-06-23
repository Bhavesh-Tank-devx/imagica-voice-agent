"""Tests for the in-memory cart/call session stores."""
import pytest

from src.telephony import sessions


@pytest.fixture(autouse=True)
def clear_stores():
    """Reset the process-global session dicts before and after each test."""
    sessions.cart_sessions.clear()
    sessions.call_sessions.clear()
    yield
    sessions.cart_sessions.clear()
    sessions.call_sessions.clear()


def test_resolve_cart_through_call_binding(imagica_cart):
    sessions.register_cart("CART-1", imagica_cart)
    sessions.bind_call("CA-sid-1", "CART-1")
    assert sessions.cart_for_call("CA-sid-1") == imagica_cart


def test_cart_for_unknown_call_is_none():
    assert sessions.cart_for_call("missing") is None


def test_end_call_pops_both_stores_and_returns_cart(imagica_cart):
    sessions.register_cart("CART-1", imagica_cart)
    sessions.bind_call("CA-sid-1", "CART-1")

    popped = sessions.end_call("CA-sid-1")

    assert popped == imagica_cart
    assert "CA-sid-1" not in sessions.call_sessions
    assert "CART-1" not in sessions.cart_sessions


def test_end_call_on_unknown_sid_returns_none():
    assert sessions.end_call("nope") is None
