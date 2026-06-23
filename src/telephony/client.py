"""Twilio client and outbound dialing."""
import logging
from functools import lru_cache

from twilio.rest import Client as TwilioClient

from src.config import app_settings, twilio_settings
from src.constants import AgentType
from src.telephony.sessions import register_cart

logger = logging.getLogger("imagica-webhook")

# Twilio call lifecycle events we subscribe to via status callback.
_STATUS_EVENTS = ["answered", "completed", "no-answer", "busy", "failed"]


@lru_cache
def get_twilio_client() -> TwilioClient:
    """Return a cached Twilio REST client built from settings."""
    return TwilioClient(
        twilio_settings.TWILIO_ACCOUNT_SID,
        twilio_settings.TWILIO_AUTH_TOKEN,
    )


async def dial_customer(cart: dict) -> str:
    """Place an outbound call to the customer via Twilio.

    ``machine_detection="Enable"`` activates AMD — Twilio posts ``AnsweredBy`` to
    ``/twilio/answer`` so the handler can hang up automatically on voicemail.

    Args:
        cart: Cart dict; must include ``cart_id`` and ``customer_phone``.

    Returns:
        The Twilio call SID.

    Raises:
        twilio.base.exceptions.TwilioRestException: On Twilio API error.
    """
    cart_id = cart["cart_id"]
    phone = cart["customer_phone"]
    agent_type = cart.get("agent_type", AgentType.IMAGICA)

    # Register cart before dialing so the WebSocket handler can find it.
    register_cart(cart_id, cart)

    base_url = app_settings.BASE_URL
    answer_url = f"{base_url}/twilio/answer?cart_id={cart_id}&agent_type={agent_type}"
    status_url = f"{base_url}/twilio/status"

    call = get_twilio_client().calls.create(
        to=phone,
        from_=twilio_settings.TWILIO_FROM_NUMBER,
        url=answer_url,
        status_callback=status_url,
        status_callback_event=_STATUS_EVENTS,
        machine_detection="Enable",
    )
    logger.info(
        "[TWILIO] Outbound call placed: call_sid=%s to=%s cart_id=%s",
        call.sid, phone, cart_id,
    )
    return call.sid
