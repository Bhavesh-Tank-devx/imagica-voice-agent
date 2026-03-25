# retry.py — Retry logic for unanswered / busy calls
# The sleep lives in the FastAPI (uvicorn) process, not the short-lived agent subprocess.
import logging

import httpx

logger = logging.getLogger("imagica-retry")

RETRY_DELAY_SECONDS = 30  # 2 hours (7200 sec) in production

HANDOFF_URL = "http://localhost:8000/internal/schedule-retry"

RETRYABLE_DISPOSITIONS = {"NO_ANSWER", "BUSY"}
MAX_ATTEMPTS = 3


async def schedule_retry(cart: dict) -> None:
    """
    Hand the retry off to the FastAPI server immediately.
    The server holds the asyncio.sleep and re-fires the webhook from its own event loop,
    which stays alive as long as uvicorn runs (unlike the agent job subprocess).
    """
    next_attempt = cart["attempt_number"] + 1
    cart_id = cart["cart_id"]
    retry_cart = {**cart, "attempt_number": next_attempt}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(HANDOFF_URL, json=retry_cart)
            resp.raise_for_status()
        logger.info(
            f"[RETRY] Handed off attempt #{next_attempt} for cart_id={cart_id} "
            f"to server (delay={RETRY_DELAY_SECONDS}s)"
        )
    except Exception as exc:
        logger.error(f"[RETRY] Handoff failed for cart_id={cart_id}: {exc}")
