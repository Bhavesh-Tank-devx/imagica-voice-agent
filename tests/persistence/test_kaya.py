"""Tests for Kaya booking writes and dashboard reads."""
from src.constants import Disposition
from src.persistence import (
    get_kaya_appointments,
    get_kaya_transcript_detail,
    get_kaya_transcripts,
    log_call,
    log_kaya_booking,
)


def _book(cart_id="KAYA-1", **kw):
    return log_kaya_booking(
        cart_id=cart_id,
        customer_phone="+91888",
        first_name=kw.get("first_name", "Priya"),
        last_name=kw.get("last_name", "Sharma"),
        email=kw.get("email", "priya@gmail.com"),
        pincode=kw.get("pincode", "395007"),
        branch_name=kw.get("branch_name", "Vesu"),
        appointment_date=kw.get("appointment_date", "2026-07-01"),
        appointment_time=kw.get("appointment_time", "10:00"),
        city=kw.get("city", "Surat"),
    )


def test_booking_is_written_and_listed():
    booking_id = _book(cart_id="KAYA-A")
    assert isinstance(booking_id, int)
    appts = get_kaya_appointments()
    assert any(a["cart_id"] == "KAYA-A" and a["branch_name"] == "Vesu" for a in appts)


def test_appointments_ordered_by_date_then_time():
    _book(cart_id="K2", appointment_date="2026-07-02", appointment_time="09:00")
    _book(cart_id="K1", appointment_date="2026-07-01", appointment_time="15:00")
    appts = get_kaya_appointments()
    dates = [a["appointment_date"] for a in appts]
    assert dates == sorted(dates)


def test_kaya_transcripts_only_returns_kaya_agent_rows():
    log_call(
        cart={"cart_id": "K-CALL", "customer_name": "Priya", "customer_phone": "+91",
              "agent_type": "kaya"},
        disposition=Disposition.CONVERTED,
        transcript=[{"role": "user", "text": "haan"}],
        summary="ok",
        agent_type="kaya",
    )
    log_call(
        cart={"cart_id": "I-CALL", "customer_name": "Bhavesh", "customer_phone": "+92",
              "agent_type": "imagica"},
        disposition=Disposition.CONVERTED,
        transcript=[{"role": "user", "text": "yes"}],
        summary="ok",
        agent_type="imagica",
    )
    transcripts = get_kaya_transcripts()
    cart_ids = {t["cart_id"] for t in transcripts}
    assert "K-CALL" in cart_ids
    assert "I-CALL" not in cart_ids


def test_kaya_transcript_detail_parses_transcript():
    log_call(
        cart={"cart_id": "K-DETAIL", "customer_name": "Priya", "customer_phone": "+91",
              "agent_type": "kaya"},
        disposition=Disposition.CONVERTED,
        transcript=[{"role": "agent", "text": "hi"}, {"role": "user", "text": "haan"}],
        summary="ok",
        agent_type="kaya",
    )
    call_id = get_kaya_transcripts()[0]["id"]
    detail = get_kaya_transcript_detail(call_id)
    assert isinstance(detail["transcript"], list)
    assert detail["transcript"][0]["text"] == "hi"
    assert get_kaya_transcript_detail(999999) is None
