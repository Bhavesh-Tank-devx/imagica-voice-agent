"""Inbound HTTP webhooks: cart abandonment, Kaya leads, post-call, retry."""
import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import Response

from src.constants import AgentType
from src.queue_worker import delayed_retry
from src.retry import RETRY_DELAY_SECONDS
from src.webhooks.schemas import CartAbandonedPayload, KayaLeadPayload
from src.webhooks.service import gate_and_enqueue

logger = logging.getLogger("imagica-webhook")

router = APIRouter()


@router.post("/webhook/cart-abandoned")
async def cart_abandoned(payload: CartAbandonedPayload) -> dict:
    """Receive an Imagicaa cart-abandonment event and enqueue the call."""
    logger.info(
        "Received cart-abandoned: cart_id=%s customer=%s phone=%s value=Rs.%s",
        payload.cart_id, payload.customer_name, payload.customer_phone, payload.total_amount,
    )
    result = gate_and_enqueue(
        cart_id=payload.cart_id,
        customer_name=payload.customer_name,
        customer_phone=payload.customer_phone,
        cart_value=payload.total_amount,
        cart_data_json=payload.model_dump_json(),
        attempt_number=payload.attempt_number,
        agent_type=AgentType.IMAGICA,
    )
    if result["status"] == "queued":
        result.update(customer=payload.customer_name, cart_value=payload.total_amount)
    return result


@router.post("/webhook/kaya-lead")
async def kaya_lead(payload: KayaLeadPayload) -> dict:
    """Receive a Kaya Clinic lead event and enqueue the call."""
    logger.info(
        "Received kaya-lead: cart_id=%s customer=%s phone=%s",
        payload.cart_id, payload.customer_name, payload.customer_phone,
    )
    result = gate_and_enqueue(
        cart_id=payload.cart_id,
        customer_name=payload.customer_name,
        customer_phone=payload.customer_phone,
        cart_value=0,
        cart_data_json=payload.model_dump_json(),
        attempt_number=payload.attempt_number,
        agent_type=AgentType.KAYA,
    )
    if result["status"] == "queued":
        result.update(customer=payload.customer_name, agent_type=AgentType.KAYA)
    return result


@router.post("/webhook/call-ended")
async def call_ended(request: Request) -> Response:
    """Receive ElevenLabs post-call notification (logged only on this path)."""
    try:
        body = await request.json()
    except ValueError:
        body = {}
    logger.info(
        "[EL WEBHOOK] call-ended: conversation_id=%s status=%s",
        body.get("conversation_id", "unknown"), body.get("status", "unknown"),
    )
    return Response(content="", status_code=204)


@router.post("/internal/schedule-retry")
async def internal_schedule_retry(cart: dict) -> dict:
    """Hold a retry timer and re-fire the cart-abandoned webhook (loopback)."""
    asyncio.create_task(delayed_retry(cart))
    logger.info(
        "[RETRY] Scheduled attempt #%s for cart_id=%s in %ss",
        cart["attempt_number"], cart["cart_id"], RETRY_DELAY_SECONDS,
    )
    return {"status": "retry_scheduled", "attempt_number": cart["attempt_number"]}
