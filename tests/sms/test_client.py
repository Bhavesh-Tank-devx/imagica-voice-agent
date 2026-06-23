"""Tests for the SMS provider-fallback chain (MSG91 -> Twilio -> mock)."""
import httpx
import pytest

from src.sms import client


@pytest.fixture
def no_credentials(monkeypatch):
    """Clear all provider credentials so only the mock path is reachable."""
    for attr in ("MSG91_AUTH_KEY", "MSG91_TEMPLATE_ID", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"):
        monkeypatch.setattr(client.sms_settings, attr, "")


async def test_no_credentials_uses_mock_and_returns_false(no_credentials):
    assert await client.send_booking_sms("+91999", "Bhavesh", "http://link") is False


async def test_msg91_used_first_when_configured(monkeypatch, no_credentials):
    calls = []
    monkeypatch.setattr(client.sms_settings, "MSG91_AUTH_KEY", "key")
    monkeypatch.setattr(client.sms_settings, "MSG91_TEMPLATE_ID", "tmpl")

    async def fake_msg91(phone, name, link):
        calls.append("msg91")
        return True

    async def fail_twilio(*a):
        calls.append("twilio")
        return True

    monkeypatch.setattr(client, "_send_via_msg91", fake_msg91)
    monkeypatch.setattr(client, "_send_via_twilio", fail_twilio)

    assert await client.send_booking_sms("+91999", "B", "l") is True
    assert calls == ["msg91"]  # twilio never reached


async def test_falls_back_to_twilio_when_msg91_errors(monkeypatch, no_credentials):
    monkeypatch.setattr(client.sms_settings, "MSG91_AUTH_KEY", "key")
    monkeypatch.setattr(client.sms_settings, "MSG91_TEMPLATE_ID", "tmpl")
    monkeypatch.setattr(client.sms_settings, "TWILIO_ACCOUNT_SID", "sid")
    monkeypatch.setattr(client.sms_settings, "TWILIO_AUTH_TOKEN", "tok")

    async def broken_msg91(*a):
        raise httpx.ConnectError("boom")

    async def ok_twilio(phone, name, link):
        return True

    monkeypatch.setattr(client, "_send_via_msg91", broken_msg91)
    monkeypatch.setattr(client, "_send_via_twilio", ok_twilio)

    assert await client.send_booking_sms("+91999", "B", "l") is True


def test_e164_normalisation():
    assert client._normalise_e164("919999999999") == "+919999999999"
    assert client._normalise_e164("+919999999999") == "+919999999999"
