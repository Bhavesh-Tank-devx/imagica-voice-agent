"""
sms.py — Outbound SMS with two providers, priority order:
  1. MSG91 (production — needs DLT-approved template)
  2. Twilio (demo/testing — works immediately with existing Twilio account)
  3. Mock log (if neither is configured)

MSG91 setup (before going live):
  1. Create a Flow template with {{name}} and {{link}} variables
  2. Get DLT approval (mandatory India, 1–2 days)
  3. Set MSG91_AUTH_KEY and MSG91_TEMPLATE_ID in .env

Twilio setup (for demo right now):
  - TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN from console.twilio.com
  - TWILIO_FROM_NUMBER=+13185043576 (your existing SIP number)
  - Twilio free trial can only SMS verified numbers — verify yours at console.twilio.com
"""
import logging
import os

import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("imagica-sms")

MSG91_AUTH_KEY = os.getenv("MSG91_AUTH_KEY")
MSG91_TEMPLATE_ID = os.getenv("MSG91_TEMPLATE_ID")
_MSG91_URL = "https://api.msg91.com/api/v5/flow/"

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "+13185043576")


def _normalise_e164(phone: str) -> str:
    """Ensure phone is in E.164 format (+<country><number>)."""
    if not phone.startswith("+"):
        phone = "+" + phone
    return phone


def _msg91_phone(phone: str) -> str:
    """MSG91 wants digits only with country code, no leading +."""
    return phone.lstrip("+") if phone.startswith("+") else ("91" + phone)


async def _send_via_msg91(phone: str, name: str, link: str) -> bool:
    payload = {
        "template_id": MSG91_TEMPLATE_ID,
        "recipients": [{"mobiles": _msg91_phone(phone), "name": name, "link": link}],
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            _MSG91_URL,
            json=payload,
            headers={"authkey": MSG91_AUTH_KEY, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
    logger.info(f"[SMS/MSG91] Sent to {phone} | {resp.status_code}")
    return True


async def _send_via_twilio(phone: str, name: str, link: str) -> bool:
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    body = (
        f"Hi {name}! Here's your Imagicaa booking link: {link}\n"
        f"Valid for 24 hrs. – Team Imagicaa"
    )
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            url,
            data={"From": TWILIO_FROM_NUMBER, "To": _normalise_e164(phone), "Body": body},
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
        )
        resp.raise_for_status()
    logger.info(f"[SMS/Twilio] Sent to {phone} | {resp.status_code}")
    return True


async def send_booking_sms(phone: str, name: str, link: str) -> bool:
    """
    Send the booking link to the customer via SMS.
    Tries MSG91 first, falls back to Twilio, then mock.
    Returns True if an SMS was actually sent.
    """
    if MSG91_AUTH_KEY and MSG91_TEMPLATE_ID:
        try:
            return await _send_via_msg91(phone, name, link)
        except Exception as exc:
            logger.error(f"[SMS/MSG91] Failed: {exc}")

    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
        try:
            return await _send_via_twilio(phone, name, link)
        except Exception as exc:
            logger.error(f"[SMS/Twilio] Failed: {exc}")

    logger.info(f"[SMS MOCK] Would send to {phone} → {link}")
    return False
