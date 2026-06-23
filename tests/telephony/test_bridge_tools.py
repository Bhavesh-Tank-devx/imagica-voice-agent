"""Tests for the ElevenLabs tool-call dispatch (``execute_tool``).

All side effects (SMS send, Kaya booking write) are stubbed so the tests assert
disposition transitions and bookkeeping only — no network, no DB.
"""
import pytest

from src.constants import Disposition
from src.telephony import bridge
from tests.conftest import fresh_state

CART = {
    "cart_id": "CART-X",
    "customer_phone": "+91999",
    "customer_name": "Bhavesh",
    "total_amount": 1000,
    "booking_link": "https://imagicaa.com/book?cart=CART-X",
}


@pytest.fixture(autouse=True)
def stub_side_effects(monkeypatch):
    """Stub the SMS send and Kaya booking write used by tool handlers."""
    sent: list[tuple] = []

    async def fake_sms(phone, name, link):
        sent.append((phone, name, link))
        return True

    monkeypatch.setattr(bridge, "send_booking_sms", fake_sms)
    monkeypatch.setattr(bridge, "log_kaya_booking", lambda **kwargs: 4242)
    return sent


async def test_send_booking_link_sets_disposition_and_sends_once(stub_side_effects):
    state = fresh_state()
    result = await bridge.execute_tool("send_booking_link", {}, CART, state)

    assert state["disposition"] == Disposition.INTERESTED_LINK_SENT
    assert state["sms_sent"] is True
    assert len(stub_side_effects) == 1
    assert len(state["tool_calls"]) == 1
    assert state["tool_calls"][0]["tool"] == "send_booking_link"
    assert "CART-X" in result


async def test_apply_discount_clamps_above_ten():
    state = fresh_state()
    await bridge.execute_tool("apply_discount", {"discount_percent": 20}, CART, state)
    assert state["discount"] == 10
    assert state["disposition"] == Disposition.INTERESTED_LINK_SENT


async def test_apply_discount_floors_below_five():
    state = fresh_state()
    await bridge.execute_tool("apply_discount", {"discount_percent": 1}, CART, state)
    assert state["discount"] == 5


@pytest.mark.parametrize(
    "tool, disposition",
    [
        ("schedule_callback", Disposition.CALLBACK_SCHEDULED),
        ("transfer_to_human", Disposition.TRANSFERRED),
        ("mark_not_interested", Disposition.NOT_INTERESTED),
    ],
)
async def test_simple_tools_set_disposition_and_record_once(tool, disposition):
    state = fresh_state()
    await bridge.execute_tool(tool, {}, CART, state)
    assert state["disposition"] == disposition
    assert len(state["tool_calls"]) == 1


async def test_book_appointment_converts_and_corrects_email():
    state = fresh_state()
    result = await bridge.execute_tool(
        "book_appointment",
        {
            "first_name": "Bhavesh",
            "last_name": "Tank",
            "email": "bhaveshreank@gmail.com",  # ASR error -> corrected to bhaveshtank
            "branch_name": "Vesu",
            "appointment_date": "2026-07-01",
            "appointment_time": "10:00",
        },
        CART,
        state,
    )
    assert state["disposition"] == Disposition.CONVERTED
    assert "4242" in result


async def test_get_closest_branches_returns_agent_message():
    state = fresh_state()
    result = await bridge.execute_tool("get_closest_branches", {"city": "Surat"}, CART, state)
    assert "Vesu" in result


async def test_unknown_tool_is_not_recorded():
    state = fresh_state()
    result = await bridge.execute_tool("does_not_exist", {}, CART, state)
    assert state["tool_calls"] == []
    assert "Unknown tool" in result


async def test_sms_deduped_across_two_tools(stub_side_effects):
    state = fresh_state()
    await bridge.execute_tool("send_booking_link", {}, CART, state)
    await bridge.execute_tool("apply_discount", {"discount_percent": 5}, CART, state)
    # sms_sent guard: only one SMS despite two link-sending tools.
    assert len(stub_side_effects) == 1
