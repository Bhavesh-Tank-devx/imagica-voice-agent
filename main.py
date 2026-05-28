"""
main.py — FastAPI webhook server for Imagica Voice Agent (ElevenLabs + Twilio)

Receives cart abandonment events, dials customers via Twilio, bridges audio
to ElevenLabs Conversational AI through a WebSocket media stream.
"""
import asyncio
import json
import logging
import os
import traceback
from datetime import datetime, timedelta
from typing import List

import pytz
import httpx
import uvicorn
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from twilio.rest import Client as TwilioClient

from log_setup import setup_logging
from post_call import (
    init_db, get_metrics, get_call_logs, get_call_detail,
    enqueue_call, dequeue_next_call, mark_queue_done, mark_queue_failed,
)
from retry import RETRY_DELAY_SECONDS
from voice_agent import media_stream_handler

load_dotenv()
logger = logging.getLogger("imagica-webhook")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
BASE_URL = os.getenv("BASE_URL", "https://redressable-spectrochemical-aarav.ngrok-free.dev")

twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

IST = pytz.timezone("Asia/Kolkata")
CALLING_HOURS_START = 9   # 9 AM IST (TRAI compliance)
CALLING_HOURS_END = 21    # 9 PM IST

# Session stores:
#   cart_sessions  — cart_id  → full cart dict (populated when call is dispatched)
#   call_sessions  — call_sid → cart_id        (populated when Twilio hits /twilio/answer)
cart_sessions: dict[str, dict] = {}
call_sessions: dict[str, str] = {}


def is_calling_hours() -> bool:
    now = datetime.now(IST)
    return CALLING_HOURS_START <= now.hour < CALLING_HOURS_END


def next_calling_window() -> str:
    """Return the next 9 AM IST window as a UTC timestamp string for SQLite comparison."""
    now_ist = datetime.now(IST)
    if now_ist.hour < CALLING_HOURS_START:
        target_ist = now_ist.replace(hour=CALLING_HOURS_START, minute=0, second=0, microsecond=0)
    else:
        target_ist = (now_ist + timedelta(days=1)).replace(
            hour=CALLING_HOURS_START, minute=0, second=0, microsecond=0
        )
    target_utc = target_ist.astimezone(pytz.utc)
    return target_utc.strftime("%Y-%m-%d %H:%M:%S")


# DND suppression list — hardcoded for POC
# In production: fetch from CRM / DND registry API
DND_LIST = {
    "+919999999999",
    "+910000000000",
    "+911234567890",
}


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
    tickets: List[TicketItem]
    total_amount: int
    attempt_number: int = 1


# ---------------------------------------------------------------------------
# App lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging("webhook")
    init_db()
    logger.info("Imagica webhook server started (ElevenLabs + Twilio mode)")
    worker_task = asyncio.create_task(queue_worker())
    yield
    worker_task.cancel()
    logger.info("Imagica webhook server shutting down")


app = FastAPI(title="Imagica Voice Agent Webhook", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Static / health / dashboard
# ---------------------------------------------------------------------------

@app.get("/")
async def dashboard():
    return FileResponse("dashboard.html")


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Twilio voice webhooks
# ---------------------------------------------------------------------------

@app.post("/twilio/answer")
async def twilio_answer(request: Request, cart_id: str = Query(...)):
    """
    Twilio calls this URL when the customer answers.
    Returns TwiML that opens a bidirectional media stream to our WebSocket handler.
    If AMD detects a machine, hangs up instead.
    """
    form = await request.form()
    call_sid = form.get("CallSid", "")
    answered_by = form.get("AnsweredBy", "")

    # AMD: hang up on voicemail / answering machine
    if answered_by and answered_by not in ("human", "unknown"):
        logger.info(f"[AMD] Voicemail detected (AnsweredBy={answered_by}) — hanging up call_sid={call_sid}")
        return Response(
            content='<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>',
            media_type="text/xml",
        )

    # Bind call_sid → cart_id for the WebSocket handler to look up
    if call_sid and cart_id:
        call_sessions[call_sid] = cart_id
        logger.info(f"[TWILIO] Answer: call_sid={call_sid} cart_id={cart_id} answered_by={answered_by or 'n/a'}")

    stream_url = f"wss://{BASE_URL.removeprefix('https://').removeprefix('http://')}/twilio/media-stream?cart_id={cart_id}"
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{stream_url}" />
    </Connect>
</Response>"""
    return Response(content=twiml, media_type="text/xml")


@app.post("/twilio/status")
async def twilio_status(request: Request):
    """
    Twilio posts call lifecycle events here (answered, completed, no-answer, busy, failed).
    Used for logging and triggering retries on no-answer / busy.
    """
    form = await request.form()
    call_sid = form.get("CallSid", "")
    call_status = form.get("CallStatus", "")
    answered_by = form.get("AnsweredBy", "")

    logger.info(
        f"[TWILIO STATUS] call_sid={call_sid} status={call_status} answered_by={answered_by or 'n/a'}"
    )

    # Clean up session store on terminal states
    if call_status in ("completed", "failed", "busy", "no-answer"):
        cart_id = call_sessions.pop(call_sid, None)
        if cart_id:
            cart = cart_sessions.pop(cart_id, None)

            # Retry on no-answer or busy (same retry.py logic, unchanged)
            if call_status in ("no-answer", "busy") and cart:
                from retry import RETRYABLE_DISPOSITIONS, MAX_ATTEMPTS, schedule_retry
                if cart.get("attempt_number", 1) < MAX_ATTEMPTS:
                    logger.info(
                        f"[TWILIO STATUS] Scheduling retry for cart_id={cart_id} "
                        f"(attempt {cart.get('attempt_number', 1)} of {MAX_ATTEMPTS})"
                    )
                    asyncio.create_task(schedule_retry(cart))

    return Response(content="", status_code=204)


# ---------------------------------------------------------------------------
# Twilio media stream WebSocket — bridges Twilio audio ↔ ElevenLabs
# ---------------------------------------------------------------------------

@app.websocket("/twilio/media-stream")
async def twilio_media_stream(websocket: WebSocket, cart_id: str = Query(...)):
    """
    Twilio connects here (from the <Stream> TwiML) and sends bidirectional µ-law audio.
    We bridge it to ElevenLabs Conversational AI via voice_agent.media_stream_handler.
    """
    cart = cart_sessions.get(cart_id)
    if not cart:
        logger.warning(f"[WS] No cart found for cart_id={cart_id} — closing")
        await websocket.close(code=1008)
        return

    # call_sid is set later in the 'start' event; pass cart_id as a stand-in for now
    await media_stream_handler(websocket, cart_id, cart)


# ---------------------------------------------------------------------------
# Cart abandonment webhook — enqueues the call
# ---------------------------------------------------------------------------

@app.post("/webhook/cart-abandoned")
async def cart_abandoned(payload: CartAbandonedPayload):
    logger.info(
        f"Received cart-abandoned: cart_id={payload.cart_id} "
        f"customer={payload.customer_name} phone={payload.customer_phone} "
        f"value=₹{payload.total_amount}"
    )

    if payload.customer_phone in DND_LIST:
        logger.info(f"DND suppressed: {payload.customer_phone}")
        return {"status": "suppressed", "reason": "DND list", "cart_id": payload.cart_id}

    scheduled_at = None
    if not is_calling_hours():
        scheduled_at = next_calling_window()
        now_ist = datetime.now(IST).strftime("%H:%M IST")
        logger.info(
            f"Outside calling hours ({now_ist}) — cart_id={payload.cart_id} "
            f"queued for next window at {scheduled_at} UTC"
        )

    enqueue_call(
        cart_id=payload.cart_id,
        customer_name=payload.customer_name,
        customer_phone=payload.customer_phone,
        cart_value=payload.total_amount,
        cart_data_json=payload.model_dump_json(),
        attempt_number=payload.attempt_number,
        scheduled_at=scheduled_at,
    )
    return {
        "status": "queued",
        "cart_id": payload.cart_id,
        "customer": payload.customer_name,
        "cart_value": payload.total_amount,
        "scheduled_at": scheduled_at or "immediate",
    }


# ---------------------------------------------------------------------------
# Internal retry endpoint (called by agent.py / voice_agent.py after NO_ANSWER/BUSY)
# ---------------------------------------------------------------------------

@app.post("/internal/schedule-retry")
async def internal_schedule_retry(cart: dict):
    asyncio.create_task(_delayed_retry(cart))
    logger.info(
        f"[RETRY] Scheduled attempt #{cart['attempt_number']} for "
        f"cart_id={cart['cart_id']} in {RETRY_DELAY_SECONDS}s"
    )
    return {"status": "retry_scheduled", "attempt_number": cart["attempt_number"]}


# ---------------------------------------------------------------------------
# Outbound dialing via Twilio (replaces LiveKit SIP dial)
# ---------------------------------------------------------------------------

async def dial_customer(cart: dict) -> str:
    """
    Place an outbound call to the customer via Twilio.
    Returns the Twilio call SID. Raises on API error.

    machine_detection="Enable" activates AMD — Twilio will POST AnsweredBy to
    /twilio/answer so we can hang up automatically on voicemail.
    """
    cart_id = cart["cart_id"]
    phone = cart["customer_phone"]

    # Register cart data before dialling so the WebSocket handler can find it
    cart_sessions[cart_id] = cart

    answer_url = f"{BASE_URL}/twilio/answer?cart_id={cart_id}"
    status_url = f"{BASE_URL}/twilio/status"

    call = twilio_client.calls.create(
        to=phone,
        from_=TWILIO_FROM_NUMBER,
        url=answer_url,
        status_callback=status_url,
        status_callback_event=["answered", "completed", "no-answer", "busy", "failed"],
        machine_detection="Enable",
    )
    logger.info(
        f"[TWILIO] Outbound call placed: call_sid={call.sid} "
        f"to={phone} cart_id={cart_id}"
    )
    return call.sid


# ---------------------------------------------------------------------------
# Queue worker — dequeues and dispatches highest-value pending calls
# ---------------------------------------------------------------------------

async def _dispatch_and_dial(queue_row: dict) -> None:
    """Dequeue a call, build the cart dict, dial via Twilio."""
    queue_id = queue_row["id"]
    cart_id = queue_row["cart_id"]
    attempt_number = queue_row.get("attempt_number", 1)
    payload_dict = json.loads(queue_row["cart_data"])

    cart_data = {
        "customer_name": payload_dict["customer_name"],
        "customer_phone": payload_dict["customer_phone"],
        "cart_id": cart_id,
        "visit_date": payload_dict["visit_date"],
        "tickets": payload_dict["tickets"],
        "total_amount": payload_dict["total_amount"],
        "park_name": "Imagicaa Theme Park, Khopoli",
        "booking_link": f"https://imagicaa.com/book?cart={cart_id}",
        "attempt_number": attempt_number,
        "call_placed_at": datetime.now().isoformat(),
    }

    try:
        call_sid = await dial_customer(cart_data)
        mark_queue_done(queue_id)
        logger.info(
            f"[QUEUE] Call dispatched: call_sid={call_sid} "
            f"cart_id={cart_id} customer={cart_data['customer_name']} "
            f"value=₹{cart_data['total_amount']} attempt={attempt_number}"
        )
    except Exception as exc:
        logger.error(
            f"[QUEUE] Dispatch failed for cart_id={cart_id}: {exc}\n{traceback.format_exc()}"
        )
        mark_queue_failed(queue_id)


async def queue_worker() -> None:
    """Background task: every 10 s, dispatch the highest-value pending call."""
    logger.info("[QUEUE] Worker started — polling every 10s")
    while True:
        await asyncio.sleep(10)
        try:
            row = dequeue_next_call()
            if row:
                logger.info(
                    f"[QUEUE] Picked cart_id={row['cart_id']} "
                    f"value=₹{row['cart_value']} attempt={row['attempt_number']}"
                )
                asyncio.create_task(_dispatch_and_dial(row))
        except Exception as exc:
            logger.error(f"[QUEUE] Worker error: {exc}")


async def _delayed_retry(cart: dict) -> None:
    await asyncio.sleep(RETRY_DELAY_SECONDS)
    cart_id = cart["cart_id"]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "http://localhost:8000/webhook/cart-abandoned", json=cart
            )
            resp.raise_for_status()
        logger.info(f"[RETRY] Attempt #{cart['attempt_number']} dispatched for cart_id={cart_id}")
    except Exception as exc:
        logger.error(f"[RETRY] Failed to re-fire webhook for cart_id={cart_id}: {exc}")


# ---------------------------------------------------------------------------
# Observability endpoints (unchanged from previous version)
# ---------------------------------------------------------------------------

@app.get("/metrics")
async def metrics():
    return get_metrics()


@app.get("/calls")
async def list_calls(limit: int = 20):
    rows = get_call_logs()
    out = []
    for r in rows[:limit]:
        transcript = []
        turns = []
        try:
            transcript = json.loads(r.get("transcript") or "[]")
        except Exception:
            pass
        try:
            turns = json.loads(r.get("latency_per_turn") or "[]")
        except Exception:
            pass
        out.append({
            "id": r["id"],
            "cart_id": r["cart_id"],
            "customer": r["customer_name"],
            "phone": r["customer_phone"],
            "disposition": r["disposition"],
            "attempt": r["attempt_number"],
            "called_at": r["called_at"],
            "first_response_ms": r.get("first_response_ms"),
            "latency_avg_ms": round(sum(turns) / len(turns)) if turns else None,
            "latency_per_turn_ms": turns,
            "transcript_turns": len(transcript),
            "transcript": transcript,
        })
    return {"calls": out}


@app.get("/calls/{call_id}")
async def call_detail(call_id: int):
    detail = get_call_detail(call_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Call not found")
    return detail


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
