"""Outbound SMS with provider fallback: MSG91 -> Twilio -> mock log.

MSG91 is the production path (requires a DLT-approved template); Twilio is the
demo path (works immediately with the existing account); if neither is
configured the message is logged only.
"""
import logging

import httpx

from src.config import sms_settings

logger = logging.getLogger("imagica-sms")

_MSG91_URL = "https://api.msg91.com/api/v5/flow/"
_HTTP_TIMEOUT = 10


def _normalise_e164(phone: str) -> str:
    """Ensure ``phone`` is in E.164 format (``+<country><number>``)."""
    return phone if phone.startswith("+") else f"+{phone}"


def _msg91_phone(phone: str) -> str:
    """Format a number for MSG91 (digits only, with country code, no '+')."""
    return phone.lstrip("+") if phone.startswith("+") else f"91{phone}"


async def _send_via_msg91(phone: str, name: str, link: str) -> bool:
    """Send the booking SMS via MSG91 Flow. Raises on HTTP error."""
    payload = {
        "template_id": sms_settings.MSG91_TEMPLATE_ID,
        "recipients": [{"mobiles": _msg91_phone(phone), "name": name, "link": link}],
    }
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.post(
            _MSG91_URL,
            json=payload,
            headers={
                "authkey": sms_settings.MSG91_AUTH_KEY,
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
    logger.info("[SMS/MSG91] Sent to %s | %s", phone, resp.status_code)
    return True


async def _send_via_twilio(phone: str, name: str, link: str) -> bool:
    """Send the booking SMS via the Twilio REST API. Raises on HTTP error."""
    url = (
        f"https://api.twilio.com/2010-04-01/Accounts/"
        f"{sms_settings.TWILIO_ACCOUNT_SID}/Messages.json"
    )
    body = (
        f"Hi {name}! Here's your Imagicaa booking link: {link}\n"
        f"Valid for 24 hrs. – Team Imagicaa"
    )
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.post(
            url,
            data={
                "From": sms_settings.TWILIO_FROM_NUMBER,
                "To": _normalise_e164(phone),
                "Body": body,
            },
            auth=(sms_settings.TWILIO_ACCOUNT_SID, sms_settings.TWILIO_AUTH_TOKEN),
        )
        resp.raise_for_status()
    logger.info("[SMS/Twilio] Sent to %s | %s", phone, resp.status_code)
    return True


async def send_booking_sms(phone: str, name: str, link: str) -> bool:
    """Send the booking link via SMS, trying MSG91 then Twilio then mock.

    Returns:
        True if an SMS was actually sent, False if it was only logged.
    """
    if sms_settings.MSG91_AUTH_KEY and sms_settings.MSG91_TEMPLATE_ID:
        try:
            return await _send_via_msg91(phone, name, link)
        except httpx.HTTPError as exc:
            logger.error("[SMS/MSG91] Failed: %s", exc)

    if sms_settings.TWILIO_ACCOUNT_SID and sms_settings.TWILIO_AUTH_TOKEN:
        try:
            return await _send_via_twilio(phone, name, link)
        except httpx.HTTPError as exc:
            logger.error("[SMS/Twilio] Failed: %s", exc)

    logger.info("[SMS MOCK] Would send to %s -> %s", phone, link)
    return False
