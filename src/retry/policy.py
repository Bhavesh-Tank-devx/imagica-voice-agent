"""Retry logic for unanswered / busy calls.

The retry delay is held in the long-lived FastAPI (uvicorn) process, not in the
short-lived agent subprocess. ``schedule_retry`` hands the retry off to the
server's ``/internal/schedule-retry`` endpoint, which owns the ``asyncio.sleep``.
"""
import logging

import httpx

from src.constants import Disposition

logger = logging.getLogger("imagica-retry")

RETRY_DELAY_SECONDS = 30  # 2 hours (7200s) in production
HANDOFF_URL = "http://localhost:8000/internal/schedule-retry"
RETRYABLE_DISPOSITIONS: set[str] = {Disposition.NO_ANSWER, Disposition.BUSY}
MAX_ATTEMPTS = 3
_HTTP_TIMEOUT = 10


async def schedule_retry(cart: dict) -> None:
    """Hand the retry off to the FastAPI server for the next attempt.

    The server holds the ``asyncio.sleep`` and re-fires the webhook from its own
    event loop (which outlives the agent job subprocess).
    """
    next_attempt = cart["attempt_number"] + 1
    cart_id = cart["cart_id"]
    retry_cart = {**cart, "attempt_number": next_attempt}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(HANDOFF_URL, json=retry_cart)
            resp.raise_for_status()
        logger.info(
            "[RETRY] Handed off attempt #%s for cart_id=%s to server (delay=%ss)",
            next_attempt, cart_id, RETRY_DELAY_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.error("[RETRY] Handoff failed for cart_id=%s: %s", cart_id, exc)
