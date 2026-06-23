"""Lead-intake business logic: DND suppression, calling-hours gating, enqueue."""
import logging
from datetime import datetime, timedelta

import pytz

from src.config import app_settings
from src.constants import AgentType
from src.persistence import enqueue_call

logger = logging.getLogger("imagica-webhook")

IST = pytz.timezone(app_settings.TIMEZONE)

# DND suppression list — hardcoded for the POC.
# In production: fetch from CRM / DND registry API.
DND_LIST: set[str] = {
    "+919999999999",
    "+910000000000",
    "+911234567890",
}


def is_calling_hours() -> bool:
    """Return True if the current local time is within the calling window."""
    hour = datetime.now(IST).hour
    return app_settings.CALLING_HOURS_START <= hour < app_settings.CALLING_HOURS_END


def next_calling_window() -> str:
    """Return the next calling-window start as a UTC timestamp for SQLite.

    The string is comparable to SQLite's ``datetime('now')`` (UTC).
    """
    now_ist = datetime.now(IST)
    start_hour = app_settings.CALLING_HOURS_START
    if now_ist.hour < start_hour:
        target = now_ist.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    else:
        target = (now_ist + timedelta(days=1)).replace(
            hour=start_hour, minute=0, second=0, microsecond=0
        )
    return target.astimezone(pytz.utc).strftime("%Y-%m-%d %H:%M:%S")


def gate_and_enqueue(
    *,
    cart_id: str,
    customer_name: str,
    customer_phone: str,
    cart_value: float,
    cart_data_json: str,
    attempt_number: int,
    agent_type: str = AgentType.IMAGICA,
) -> dict:
    """Apply DND + calling-hours gating, then enqueue the call.

    Returns:
        ``{"status": "suppressed", ...}`` if on the DND list, otherwise
        ``{"status": "queued", "scheduled_at": ...}``.
    """
    if customer_phone in DND_LIST:
        logger.info("DND suppressed: %s", customer_phone)
        return {"status": "suppressed", "reason": "DND list", "cart_id": cart_id}

    scheduled_at = None
    if not is_calling_hours():
        scheduled_at = next_calling_window()
        now_ist = datetime.now(IST).strftime("%H:%M IST")
        logger.info(
            "Outside calling hours (%s) — cart_id=%s queued for next window at %s UTC",
            now_ist, cart_id, scheduled_at,
        )

    enqueue_call(
        cart_id=cart_id,
        customer_name=customer_name,
        customer_phone=customer_phone,
        cart_value=cart_value,
        cart_data_json=cart_data_json,
        attempt_number=attempt_number,
        scheduled_at=scheduled_at,
        agent_type=agent_type,
    )
    return {"status": "queued", "cart_id": cart_id, "scheduled_at": scheduled_at or "immediate"}
