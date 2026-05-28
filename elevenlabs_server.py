"""
elevenlabs_server.py — FastAPI server for ElevenLabs Conversational AI integration.

Three responsibilities:
  1. /webhook/cart-abandoned  — receives cart events, triggers ElevenLabs outbound call
  2. /tools/<name>            — receives tool callbacks from ElevenLabs agent during a call
  3. /webhook/call-ended      — receives post-call data from ElevenLabs, logs to SQLite

Reuses sms.py for SMS delivery and post_call.py for call logging.

Run:
    uvicorn elevenlabs_server:app --host 0.0.0.0 --port 8001 --reload

Expose locally with ngrok for testing:
    ngrok http 8001
    → copy the https://xxxx.ngrok.io URL and update tool URLs + post-call webhook in ElevenLabs dashboard
"""
import hashlib
import hmac
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from post_call import (
    init_db, log_call, get_call_logs, get_call_detail, get_metrics,
    save_session, get_session, append_tool_call, delete_session,
    DISPOSITION_INTERESTED_LINK_SENT, DISPOSITION_NOT_INTERESTED,
    DISPOSITION_TRANSFERRED, DISPOSITION_CALLBACK_SCHEDULED,
    DISPOSITION_TECHNICAL_FAILURE, DISPOSITION_WRONG_NUMBER,
)
from sms import send_booking_sms

load_dotenv()
logger = logging.getLogger("imagica-elevenlabs")
logging.basicConfig(level=logging.INFO)

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID", "")
ELEVENLABS_PHONE_NUMBER_ID = os.getenv("ELEVENLABS_PHONE_NUMBER_ID", "")
ELEVENLABS_WEBHOOK_SECRET = os.getenv("ELEVENLABS_WEBHOOK_SECRET", "")

app = FastAPI(title="Imagica ElevenLabs Webhook Server")

_HINDI_MARKERS = {
    "haan", "nahi", "kya", "main", "aap", "theek", "bilkul",
    "zaroor", "accha", "baat", "kar", "rahi", "hun", "hai",
    "mujhe", "karo", "ho", "chahiye", "abhi", "baad",
}

def _detect_language(text: str) -> str:
    words = set(re.findall(r"[a-z]+", text.lower()))
    hindi_hits = words & _HINDI_MARKERS
    ratio = len(hindi_hits) / max(len(words), 1)
    if ratio >= 0.6:
        return "hindi"
    if ratio >= 0.2:
        return "hinglish"
    return "english"

def _verify_elevenlabs_signature(raw_body: bytes, signature_header: str) -> bool:
    """
    Verify ElevenLabs webhook HMAC-SHA256 signature.
    Header format: "t=<unix_timestamp>,v0=<hex_digest>"
    Signed payload: "<timestamp>.<raw_body>"
    Rejects requests older than 5 minutes to prevent replay attacks.
    """
    if not ELEVENLABS_WEBHOOK_SECRET:
        return True  # secret not configured — skip verification (dev only)
    try:
        parts = dict(p.split("=", 1) for p in signature_header.split(","))
        timestamp = parts["t"]
        expected_sig = parts["v0"]
    except (KeyError, ValueError):
        return False

    # Reject stale requests (> 5 minutes old)
    if abs(time.time() - int(timestamp)) > 300:
        return False

    signed_payload = f"{timestamp}.".encode() + raw_body
    computed = hmac.new(
        ELEVENLABS_WEBHOOK_SECRET.encode(),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()  # hmac.new is the correct stdlib call (alias for hmac.HMAC)
    return hmac.compare_digest(computed, expected_sig)


def _infer_disposition(tool_calls: list[str]) -> str:
    """Pick the most significant disposition based on which tools fired."""
    if "mark_not_interested" in tool_calls:
        return DISPOSITION_NOT_INTERESTED
    if "transfer_to_human" in tool_calls:
        return DISPOSITION_TRANSFERRED
    if "apply_discount" in tool_calls or "send_booking_link" in tool_calls:
        return DISPOSITION_INTERESTED_LINK_SENT
    if "schedule_callback" in tool_calls:
        return DISPOSITION_CALLBACK_SCHEDULED
    return DISPOSITION_TECHNICAL_FAILURE


@app.on_event("startup")
async def startup():
    init_db()
    logger.info("ElevenLabs server started")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class TicketItem(BaseModel):
    type: str
    quantity: int
    price_per_unit: int


class CartAbandonedPayload(BaseModel):
    customer_name: str
    customer_phone: str
    cart_id: str
    visit_date: str
    tickets: list[TicketItem]
    total_amount: int
    attempt_number: int = 1


# ---------------------------------------------------------------------------
# 1. Cart abandonment webhook → trigger ElevenLabs outbound call
# ---------------------------------------------------------------------------

@app.post("/webhook/cart-abandoned")
async def cart_abandoned(payload: CartAbandonedPayload):
    """
    Receive a cart abandonment event and instruct ElevenLabs to call the customer.
    ElevenLabs injects dynamic_variables into the agent's system prompt and first message
    at call start — no mid-call lookup needed.
    """
    logger.info(
        f"Cart abandoned: cart_id={payload.cart_id} "
        f"customer={payload.customer_name} phone={payload.customer_phone}"
    )

    if not ELEVENLABS_API_KEY or not ELEVENLABS_AGENT_ID or not ELEVENLABS_PHONE_NUMBER_ID:
        raise HTTPException(
            status_code=500,
            detail="ELEVENLABS_API_KEY / ELEVENLABS_AGENT_ID / ELEVENLABS_PHONE_NUMBER_ID not set in .env"
        )

    booking_link = f"https://imagicaa.com/book?cart={payload.cart_id}"
    tickets_summary = ", ".join(
        f"{t.quantity}x {t.type}" for t in payload.tickets
    )

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://api.elevenlabs.io/v1/convai/twilio/outbound-call",
            headers={
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "agent_id": ELEVENLABS_AGENT_ID,
                "agent_phone_number_id": ELEVENLABS_PHONE_NUMBER_ID,
                "to_number": payload.customer_phone,
                "conversation_initiation_client_data": {
                    "dynamic_variables": {
                        "customer_name": payload.customer_name,
                        "customer_phone": payload.customer_phone,
                        "cart_id": payload.cart_id,
                        "cart_total": str(payload.total_amount),
                        "cart_items": tickets_summary,
                        "visit_date": payload.visit_date,
                        "booking_link": booking_link,
                        "attempt_number": str(payload.attempt_number),
                    }
                },
            },
        )

    if resp.status_code not in (200, 201):
        logger.error(f"ElevenLabs API error: {resp.status_code} {resp.text}")
        raise HTTPException(status_code=502, detail=f"ElevenLabs API error: {resp.text}")

    data = resp.json()
    conversation_id = data.get("conversation_id") or data.get("callSid", "unknown")
    logger.info(f"Call initiated: conversation_id={conversation_id} cart_id={payload.cart_id}")

    # Persist session to SQLite so it survives server restarts
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


# ---------------------------------------------------------------------------
# 2. Tool webhook endpoints — ElevenLabs POSTs here when agent calls a tool
#
# ElevenLabs sends the parameter values defined in the tool schema.
# The response JSON is read back to the agent as the tool result.
# ---------------------------------------------------------------------------

def _booking_link(cart_id: str) -> str:
    return f"https://imagicaa.com/book?cart={cart_id}"


def _track_tool(body: dict, tool_name: str, extra: dict | None = None) -> None:
    """Append this tool call to the persisted session."""
    conv_id = body.get("conversation_id", "")
    if conv_id:
        append_tool_call(conv_id, {
            "tool": tool_name,
            "ts": datetime.now().isoformat(),
            **(extra or {}),
        })


@app.post("/tools/send_booking_link")
async def tool_send_booking_link(request: Request):
    """Agent calls this to send the booking SMS without a discount."""
    body: dict[str, Any] = await request.json()
    logger.info(f"[TOOL] send_booking_link called: {body}")

    phone = body.get("phone_number", "")
    cart_id = body.get("cart_id", "unknown")
    customer_name = body.get("customer_name", "Customer")
    link = _booking_link(cart_id)

    _track_tool(body, "send_booking_link")
    sms_sent = await send_booking_sms(phone, customer_name, link)

    result = (
        "Booking link sent via SMS successfully."
        if sms_sent
        else "Booking link logged (SMS not configured)."
    )
    logger.info(f"[TOOL] send_booking_link → {result} | phone={phone}")
    return JSONResponse({"result": result})


@app.post("/tools/apply_discount")
async def tool_apply_discount(request: Request):
    """Agent calls this with a discount_percent it chose (must be 5–10)."""
    body: dict[str, Any] = await request.json()
    logger.info(f"[TOOL] apply_discount called: {body}")

    phone = body.get("phone_number", "")
    cart_id = body.get("cart_id", "unknown")
    customer_name = body.get("customer_name", "Customer")
    discount = float(body.get("discount_percent", 5))

    # Clamp to 5–10 range — never exceed 10%
    discount = max(5.0, min(10.0, discount))
    discount = int(discount) if discount == int(discount) else discount

    _track_tool(body, "apply_discount", {"discount_percent": discount})

    link = f"{_booking_link(cart_id)}&discount={discount}"
    sms_sent = await send_booking_sms(phone, customer_name, link)

    result = (
        f"{discount}% discount applied and booking link sent via SMS."
        if sms_sent
        else f"{discount}% discount applied. Link logged (SMS not configured)."
    )
    logger.info(f"[TOOL] apply_discount → {result} | phone={phone} discount={discount}%")
    return JSONResponse({"result": result})


@app.post("/tools/schedule_callback")
async def tool_schedule_callback(request: Request):
    """Agent calls this when customer asks to be called back later."""
    body: dict[str, Any] = await request.json()
    logger.info(f"[TOOL] schedule_callback called: {body}")

    phone = body.get("phone_number", "")
    callback_time = body.get("callback_time", "later")

    _track_tool(body, "schedule_callback", {"callback_time": callback_time})
    # TODO: write to CRM / scheduling system
    logger.info(f"[CRM] Callback scheduled: phone={phone} time={callback_time}")

    result = f"Callback scheduled for {callback_time}. We will call you then."
    return JSONResponse({"result": result})


@app.post("/tools/transfer_to_human")
async def tool_transfer_to_human(request: Request):
    """Agent calls this to escalate to a human agent."""
    body: dict[str, Any] = await request.json()
    logger.info(f"[TOOL] transfer_to_human called: {body}")

    phone = body.get("phone_number", "")

    _track_tool(body, "transfer_to_human")
    # TODO: integrate with your contact centre / SIP transfer
    logger.info(f"[CRM] Transfer requested: phone={phone}")

    result = "Transferring you to a human agent now. Please hold."
    return JSONResponse({"result": result})


@app.post("/tools/mark_not_interested")
async def tool_mark_not_interested(request: Request):
    """Agent calls this when customer explicitly declines."""
    body: dict[str, Any] = await request.json()
    logger.info(f"[TOOL] mark_not_interested called: {body}")

    phone = body.get("phone_number", "")

    _track_tool(body, "mark_not_interested")
    # TODO: update CRM opt-out flag
    logger.info(f"[CRM] Marked not interested: phone={phone}")

    result = "Understood. We will not call you again. Have a great day!"
    return JSONResponse({"result": result})


# ---------------------------------------------------------------------------
# 3. Post-call webhook — ElevenLabs POSTs here when a conversation ends.
#    Configure this URL in ElevenLabs dashboard → Agent → Post-call webhook.
# ---------------------------------------------------------------------------

@app.post("/webhook/call-ended")
async def call_ended(request: Request):
    """
    Receives post-call data from ElevenLabs and logs the call to SQLite.

    ElevenLabs payload shape (v1):
      {
        "type": "post_call_transcription",
        "data": {
          "conversation_id": "...",
          "status": "done",
          "transcript": [{"role": "agent"/"user", "message": "...", "time_in_call_secs": 0}, ...],
          "metadata": {"start_time_unix_secs": ..., "call_duration_secs": ...},
          "analysis": {"transcript_summary": "..."}
        }
      }
    """
    raw_body = await request.body()
    sig_header = request.headers.get("ElevenLabs-Signature", "")
    if not _verify_elevenlabs_signature(raw_body, sig_header):
        logger.warning("[POST-CALL] Signature verification failed — rejected")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    body: dict[str, Any] = json.loads(raw_body)
    logger.info(f"[POST-CALL] Received: {list(body.keys())}")

    # Support both wrapped {"type": ..., "data": {...}} and flat payloads
    data = body.get("data", body)
    conversation_id = data.get("conversation_id", "unknown")
    raw_transcript = data.get("transcript", [])
    metadata = data.get("metadata", {})
    analysis = data.get("analysis", {})

    duration_sec = int(metadata.get("call_duration_secs", 0))
    summary = analysis.get("transcript_summary", "")

    # Normalise transcript to {"role", "text"} dicts
    transcript = [
        {"role": t.get("role", "unknown"), "text": t.get("message", "")}
        for t in raw_transcript
        if t.get("message")
    ]

    # Compute per-turn latency from time_in_call_secs differences
    latency_per_turn: list[int] = []
    prev_agent_end: float | None = None
    for i, turn in enumerate(raw_transcript):
        if turn.get("role") == "agent" and prev_agent_end is not None:
            gap_ms = int((turn.get("time_in_call_secs", 0) - prev_agent_end) * 1000)
            if 100 < gap_ms < 15_000:   # cap noise
                latency_per_turn.append(gap_ms)
        if turn.get("role") == "user":
            prev_agent_end = turn.get("time_in_call_secs")

    # Detect language from user turns
    user_text = " ".join(t.get("message", "") for t in raw_transcript if t.get("role") == "user")
    language = _detect_language(user_text)

    # Load session from SQLite (survives server restarts)
    session = get_session(conversation_id)
    if not session:
        logger.warning(f"[POST-CALL] No session for conversation_id={conversation_id} — skipping log")
        return JSONResponse({"status": "ignored", "reason": "no session"})

    cart = session["cart"]
    tool_names = [t["tool"] for t in session["tool_calls"]]
    disposition = _infer_disposition(tool_names)
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

    # Remove session after logging (second webhook firing will be safely ignored)
    delete_session(conversation_id)

    logger.info(
        f"[POST-CALL] Logged: conversation_id={conversation_id} "
        f"disposition={disposition} turns={len(transcript)} duration={duration_sec}s"
    )
    return JSONResponse({"status": "logged", "disposition": disposition})


# ---------------------------------------------------------------------------
# 4. Observability endpoints
# ---------------------------------------------------------------------------

@app.get("/calls")
async def list_calls(cart_id: str | None = None, limit: int = 20):
    """List recent call logs. Pass ?cart_id=X to filter."""
    rows = get_call_logs(cart_id=cart_id)
    return rows[:limit]


@app.get("/calls/{call_id}")
async def get_call(call_id: int):
    row = get_call_detail(call_id)
    if not row:
        raise HTTPException(status_code=404, detail="Call not found")
    return row


@app.get("/metrics")
async def metrics():
    """Aggregated stats: disposition distribution, latency, language, retry effectiveness."""
    return get_metrics()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "service": "elevenlabs-server"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001, reload=False)
