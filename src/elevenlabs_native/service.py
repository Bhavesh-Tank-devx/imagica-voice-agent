"""Business logic for the ElevenLabs-native server.

Triggers hosted outbound calls, infers dispositions from fired tools, and
reconstructs/logs the call from the post-call webhook payload.
"""
import logging
from datetime import datetime

import httpx
from fastapi import HTTPException

from src.config import elevenlabs_settings
from src.constants import Disposition
from src.conversation import detect_language_from_text
from src.persistence import delete_session, get_session, log_call, save_session
from src.webhooks.schemas import CartAbandonedPayload

logger = logging.getLogger("imagica-elevenlabs")

_OUTBOUND_CALL_URL = "https://api.elevenlabs.io/v1/convai/twilio/outbound-call"
_HTTP_TIMEOUT = 15
# Cap noise when deriving per-turn latency from transcript timestamps.
_MIN_GAP_MS, _MAX_GAP_MS = 100, 15_000


def booking_link(cart_id: str) -> str:
    """Return the booking link for a cart id."""
    return f"https://imagicaa.com/book?cart={cart_id}"


def infer_disposition(tool_calls: list[str]) -> str:
    """Pick the most significant disposition from the tools that fired."""
    if "mark_not_interested" in tool_calls:
        return Disposition.NOT_INTERESTED
    if "transfer_to_human" in tool_calls:
        return Disposition.TRANSFERRED
    if "apply_discount" in tool_calls or "send_booking_link" in tool_calls:
        return Disposition.INTERESTED_LINK_SENT
    if "schedule_callback" in tool_calls:
        return Disposition.CALLBACK_SCHEDULED
    return Disposition.TECHNICAL_FAILURE


async def trigger_outbound_call(payload: CartAbandonedPayload) -> dict:
    """Ask ElevenLabs to place an outbound call and persist the session.

    Raises:
        HTTPException: If credentials are missing or the ElevenLabs API errors.
    """
    if not (
        elevenlabs_settings.ELEVENLABS_API_KEY
        and elevenlabs_settings.ELEVENLABS_AGENT_ID
        and elevenlabs_settings.ELEVENLABS_PHONE_NUMBER_ID
    ):
        raise HTTPException(
            status_code=500,
            detail="ELEVENLABS_API_KEY / ELEVENLABS_AGENT_ID / "
                   "ELEVENLABS_PHONE_NUMBER_ID not set in .env",
        )

    link = booking_link(payload.cart_id)
    tickets_summary = ", ".join(f"{t.quantity}x {t.type}" for t in payload.tickets)

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.post(
            _OUTBOUND_CALL_URL,
            headers={
                "xi-api-key": elevenlabs_settings.ELEVENLABS_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "agent_id": elevenlabs_settings.ELEVENLABS_AGENT_ID,
                "agent_phone_number_id": elevenlabs_settings.ELEVENLABS_PHONE_NUMBER_ID,
                "to_number": payload.customer_phone,
                "conversation_initiation_client_data": {
                    "dynamic_variables": {
                        "customer_name": payload.customer_name,
                        "customer_phone": payload.customer_phone,
                        "cart_id": payload.cart_id,
                        "cart_total": str(payload.total_amount),
                        "cart_items": tickets_summary,
                        "visit_date": payload.visit_date,
                        "booking_link": link,
                        "attempt_number": str(payload.attempt_number),
                    }
                },
            },
        )

    if resp.status_code not in (200, 201):
        logger.error("ElevenLabs API error: %s %s", resp.status_code, resp.text)
        raise HTTPException(status_code=502, detail=f"ElevenLabs API error: {resp.text}")

    data = resp.json()
    conversation_id = data.get("conversation_id") or data.get("callSid", "unknown")
    logger.info("Call initiated: conversation_id=%s cart_id=%s", conversation_id, payload.cart_id)

    save_session(
        conversation_id=conversation_id,
        cart={
            "cart_id": payload.cart_id,
            "customer_name": payload.customer_name,
            "customer_phone": payload.customer_phone,
            "total_amount": payload.total_amount,
            "visit_date": payload.visit_date,
            "attempt_number": payload.attempt_number,
        },
        initiated_at=datetime.now().isoformat(),
    )
    return {
        "status": "call_initiated",
        "conversation_id": conversation_id,
        "call_sid": data.get("callSid"),
        "cart_id": payload.cart_id,
        "customer": payload.customer_name,
    }


def _per_turn_latency(raw_transcript: list[dict]) -> list[int]:
    """Derive per-turn latency (ms) from transcript ``time_in_call_secs`` gaps."""
    latency: list[int] = []
    prev_user_end: float | None = None
    for turn in raw_transcript:
        if turn.get("role") == "agent" and prev_user_end is not None:
            gap_ms = int((turn.get("time_in_call_secs", 0) - prev_user_end) * 1000)
            if _MIN_GAP_MS < gap_ms < _MAX_GAP_MS:
                latency.append(gap_ms)
        if turn.get("role") == "user":
            prev_user_end = turn.get("time_in_call_secs")
    return latency


def log_post_call(data: dict) -> dict:
    """Log a completed ElevenLabs call from its post-call payload.

    Returns:
        A status dict for the webhook response.
    """
    conversation_id = data.get("conversation_id", "unknown")
    raw_transcript = data.get("transcript", [])
    metadata = data.get("metadata", {})
    analysis = data.get("analysis", {})

    duration_sec = int(metadata.get("call_duration_secs", 0))
    summary = analysis.get("transcript_summary", "")
    transcript = [
        {"role": t.get("role", "unknown"), "text": t.get("message", "")}
        for t in raw_transcript
        if t.get("message")
    ]
    latency_per_turn = _per_turn_latency(raw_transcript)
    user_text = " ".join(
        t.get("message", "") for t in raw_transcript if t.get("role") == "user"
    )
    language = detect_language_from_text(user_text)

    session = get_session(conversation_id)
    if not session:
        logger.warning("[POST-CALL] No session for conversation_id=%s — skipping", conversation_id)
        return {"status": "ignored", "reason": "no session"}

    cart = session["cart"]
    tool_names = [t["tool"] for t in session["tool_calls"]]
    disposition = infer_disposition(tool_names)
    discount = session.get("discount", 0)

    log_call(
        cart=cart,
        disposition=disposition,
        transcript=transcript,
        summary=summary or f"{disposition} | {len(transcript)} turns",
        discount=int(discount),
        called_at=session.get("initiated_at"),
        first_response_ms=latency_per_turn[0] if latency_per_turn else None,
        tool_calls=session["tool_calls"],
        language_detected=language,
        latency_per_turn=latency_per_turn,
        duration_sec=duration_sec,
    )
    delete_session(conversation_id)
    logger.info(
        "[POST-CALL] Logged: conversation_id=%s disposition=%s turns=%s duration=%ss",
        conversation_id, disposition, len(transcript), duration_sec,
    )
    return {"status": "logged", "disposition": disposition}
