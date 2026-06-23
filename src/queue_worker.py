"""Background queue worker: dequeue and dispatch the highest-value pending call."""
import asyncio
import json
import logging
import traceback
from datetime import datetime

import httpx

from src.constants import AgentType
from src.persistence import dequeue_next_call, mark_queue_done, mark_queue_failed
from src.retry import RETRY_DELAY_SECONDS
from src.telephony import dial_customer

logger = logging.getLogger("imagica-webhook")

_POLL_INTERVAL_SEC = 10
_RETRY_HTTP_TIMEOUT = 10


def build_dispatch_cart(queue_row: dict) -> dict:
    """Build the in-call cart dict from a dequeued queue row."""
    cart_id = queue_row["cart_id"]
    attempt_number = queue_row.get("attempt_number", 1)
    agent_type = queue_row.get("agent_type", AgentType.IMAGICA)
    payload = json.loads(queue_row["cart_data"])
    now = datetime.now().isoformat()

    if agent_type == AgentType.KAYA:
        return {
            "agent_type": AgentType.KAYA,
            "customer_name": payload["customer_name"],
            "customer_phone": payload["customer_phone"],
            "cart_id": cart_id,
            "city": payload.get("city", ""),
            "call_type": payload.get("call_type", "OUTBOUND"),
            "attempt_number": attempt_number,
            "call_placed_at": now,
        }
    return {
        "agent_type": AgentType.IMAGICA,
        "customer_name": payload["customer_name"],
        "customer_phone": payload["customer_phone"],
        "cart_id": cart_id,
        "visit_date": payload["visit_date"],
        "tickets": payload["tickets"],
        "total_amount": payload["total_amount"],
        "park_name": "Imagicaa Theme Park, Khopoli",
        "booking_link": f"https://imagicaa.com/book?cart={cart_id}",
        "attempt_number": attempt_number,
        "call_placed_at": now,
    }


async def dispatch_and_dial(queue_row: dict) -> None:
    """Build the cart for a dequeued call and dial it via Twilio."""
    queue_id = queue_row["id"]
    cart = build_dispatch_cart(queue_row)
    cart_id = cart["cart_id"]
    try:
        call_sid = await dial_customer(cart)
        mark_queue_done(queue_id)
        value = "n/a" if cart["agent_type"] == AgentType.KAYA else cart["total_amount"]
        logger.info(
            "[QUEUE] Call dispatched: call_sid=%s cart_id=%s customer=%s value=%s attempt=%s",
            call_sid, cart_id, cart["customer_name"], value, cart["attempt_number"],
        )
    except Exception as exc:  # noqa: BLE001 — one failed dial must not kill the worker
        logger.error("[QUEUE] Dispatch failed for cart_id=%s: %s\n%s", cart_id, exc, traceback.format_exc())
        mark_queue_failed(queue_id)


async def queue_worker() -> None:
    """Poll the queue every ``_POLL_INTERVAL_SEC`` and dispatch ready calls."""
    logger.info("[QUEUE] Worker started — polling every %ss", _POLL_INTERVAL_SEC)
    while True:
        await asyncio.sleep(_POLL_INTERVAL_SEC)
        try:
            row = dequeue_next_call()
            if row:
                logger.info(
                    "[QUEUE] Picked cart_id=%s value=%s attempt=%s",
                    row["cart_id"], row["cart_value"], row["attempt_number"],
                )
                asyncio.create_task(dispatch_and_dial(row))
        except Exception as exc:  # noqa: BLE001 — worker loop must stay alive
            logger.error("[QUEUE] Worker error: %s", exc)


async def delayed_retry(cart: dict) -> None:
    """After ``RETRY_DELAY_SECONDS``, re-fire the cart-abandoned webhook."""
    await asyncio.sleep(RETRY_DELAY_SECONDS)
    cart_id = cart["cart_id"]
    # Loopback to the server's own webhook so the retry timer stays in-process.
    url = "http://localhost:8000/webhook/cart-abandoned"
    try:
        async with httpx.AsyncClient(timeout=_RETRY_HTTP_TIMEOUT) as client:
            resp = await client.post(url, json=cart)
            resp.raise_for_status()
        logger.info("[RETRY] Attempt #%s dispatched for cart_id=%s", cart["attempt_number"], cart_id)
    except httpx.HTTPError as exc:
        logger.error("[RETRY] Failed to re-fire webhook for cart_id=%s: %s", cart_id, exc)
