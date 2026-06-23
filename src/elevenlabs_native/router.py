"""Routes for the ElevenLabs-native server: outbound trigger, tools, post-call."""
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from src.elevenlabs_native.security import verify_elevenlabs_signature
from src.elevenlabs_native.service import (
    booking_link,
    log_post_call,
    trigger_outbound_call,
)
from src.persistence import (
    append_tool_call,
    get_call_detail,
    get_call_logs,
    get_metrics,
)
from src.sms import send_booking_sms
from src.webhooks.schemas import CartAbandonedPayload

logger = logging.getLogger("imagica-elevenlabs")

router = APIRouter()

# Discount bounds for the apply_discount tool.
_MIN_DISCOUNT_PCT, _MAX_DISCOUNT_PCT = 5.0, 10.0


def _track_tool(body: dict, tool_name: str, extra: dict | None = None) -> None:
    """Append a tool call to the persisted session, if one exists."""
    conv_id = body.get("conversation_id", "")
    if conv_id:
        append_tool_call(
            conv_id,
            {"tool": tool_name, "ts": datetime.now().isoformat(), **(extra or {})},
        )


@router.post("/webhook/cart-abandoned")
async def cart_abandoned(payload: CartAbandonedPayload) -> dict:
    """Receive a cart event and instruct ElevenLabs to call the customer."""
    logger.info(
        "Cart abandoned: cart_id=%s customer=%s phone=%s",
        payload.cart_id, payload.customer_name, payload.customer_phone,
    )
    return await trigger_outbound_call(payload)


@router.post("/tools/send_booking_link")
async def tool_send_booking_link(request: Request) -> JSONResponse:
    """Send the booking SMS (no discount)."""
    body: dict[str, Any] = await request.json()
    logger.info("[TOOL] send_booking_link called: %s", body)
    cart_id = body.get("cart_id", "unknown")
    link = booking_link(cart_id)

    _track_tool(body, "send_booking_link")
    sms_sent = await send_booking_sms(body.get("phone_number", ""), body.get("customer_name", "Customer"), link)

    result = (
        "Booking link sent via SMS successfully."
        if sms_sent else "Booking link logged (SMS not configured)."
    )
    logger.info("[TOOL] send_booking_link -> %s | phone=%s", result, body.get("phone_number", ""))
    return JSONResponse({"result": result})


@router.post("/tools/apply_discount")
async def tool_apply_discount(request: Request) -> JSONResponse:
    """Apply a 5-10% discount and send the updated link."""
    body: dict[str, Any] = await request.json()
    logger.info("[TOOL] apply_discount called: %s", body)

    discount = max(_MIN_DISCOUNT_PCT, min(_MAX_DISCOUNT_PCT, float(body.get("discount_percent", 5))))
    discount = int(discount) if discount == int(discount) else discount
    cart_id = body.get("cart_id", "unknown")

    _track_tool(body, "apply_discount", {"discount_percent": discount})
    link = f"{booking_link(cart_id)}&discount={discount}"
    sms_sent = await send_booking_sms(body.get("phone_number", ""), body.get("customer_name", "Customer"), link)

    result = (
        f"{discount}% discount applied and booking link sent via SMS."
        if sms_sent else f"{discount}% discount applied. Link logged (SMS not configured)."
    )
    logger.info("[TOOL] apply_discount -> %s", result)
    return JSONResponse({"result": result})


@router.post("/tools/schedule_callback")
async def tool_schedule_callback(request: Request) -> JSONResponse:
    """Record a callback request."""
    body: dict[str, Any] = await request.json()
    logger.info("[TOOL] schedule_callback called: %s", body)
    callback_time = body.get("callback_time", "later")
    _track_tool(body, "schedule_callback", {"callback_time": callback_time})
    logger.info("[CRM] Callback scheduled: phone=%s time=%s", body.get("phone_number", ""), callback_time)
    return JSONResponse({"result": f"Callback scheduled for {callback_time}. We will call you then."})


@router.post("/tools/transfer_to_human")
async def tool_transfer_to_human(request: Request) -> JSONResponse:
    """Record an escalation to a human agent."""
    body: dict[str, Any] = await request.json()
    logger.info("[TOOL] transfer_to_human called: %s", body)
    _track_tool(body, "transfer_to_human")
    logger.info("[CRM] Transfer requested: phone=%s", body.get("phone_number", ""))
    return JSONResponse({"result": "Transferring you to a human agent now. Please hold."})


@router.post("/tools/mark_not_interested")
async def tool_mark_not_interested(request: Request) -> JSONResponse:
    """Record an explicit decline."""
    body: dict[str, Any] = await request.json()
    logger.info("[TOOL] mark_not_interested called: %s", body)
    _track_tool(body, "mark_not_interested")
    logger.info("[CRM] Marked not interested: phone=%s", body.get("phone_number", ""))
    return JSONResponse({"result": "Understood. We will not call you again. Have a great day!"})


@router.post("/webhook/call-ended")
async def call_ended(request: Request) -> JSONResponse:
    """Receive post-call data from ElevenLabs (HMAC-verified) and log it."""
    raw_body = await request.body()
    sig_header = request.headers.get("ElevenLabs-Signature", "")
    if not verify_elevenlabs_signature(raw_body, sig_header):
        logger.warning("[POST-CALL] Signature verification failed — rejected")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    body: dict[str, Any] = json.loads(raw_body)
    logger.info("[POST-CALL] Received: %s", list(body.keys()))
    # Support both wrapped {"type", "data"} and flat payloads.
    return JSONResponse(log_post_call(body.get("data", body)))


@router.get("/calls")
async def list_calls(cart_id: str | None = None, limit: int = 20) -> list[dict]:
    """List recent call logs; pass ``?cart_id=X`` to filter."""
    return get_call_logs(cart_id=cart_id)[:limit]


@router.get("/calls/{call_id}")
async def get_call(call_id: int) -> dict:
    """Return a single call's full detail."""
    row = get_call_detail(call_id)
    if not row:
        raise HTTPException(status_code=404, detail="Call not found")
    return row


@router.get("/metrics")
async def metrics() -> dict:
    """Aggregated stats."""
    return get_metrics()


@router.get("/health")
async def health() -> dict:
    """Liveness probe."""
    return {"status": "ok", "service": "elevenlabs-server"}
