"""
main.py — FastAPI webhook server for Imagica Voice Agent
Receives cart abandonment events and dispatches Priya (LiveKit agent) to call the customer.
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
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from livekit import api as lkapi
from livekit.api import AccessToken, VideoGrants

from contextlib import asynccontextmanager

from log_setup import setup_logging
from post_call import (
    init_db, get_metrics, get_call_logs, get_call_detail,
    enqueue_call, dequeue_next_call, mark_queue_done, mark_queue_failed,
)
from retry import RETRY_DELAY_SECONDS

load_dotenv()
logger = logging.getLogger("imagica-webhook")

LIVEKIT_URL = os.getenv("LIVEKIT_URL", "ws://localhost:7880")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "secret")
SIP_TRUNK_ID = os.getenv("LIVEKIT_SIP_TRUNK_ID")  # set to enable real phone calls; omit for playground testing
AGENT_NAME = "imagica-priya"

IST = pytz.timezone("Asia/Kolkata")
CALLING_HOURS_START = 9   # 9 AM IST (TRAI compliance)
CALLING_HOURS_END = 21    # 9 PM IST


def is_calling_hours() -> bool:
    now = datetime.now(IST)
    return CALLING_HOURS_START <= now.hour < CALLING_HOURS_END


def next_calling_window() -> str:
    """Return the next 9 AM IST window as a UTC timestamp string for SQLite comparison.

    SQLite's datetime('now') is UTC, so scheduled_at must also be UTC for
    the `scheduled_at <= datetime('now')` comparison in dequeue_next_call() to work.
    """
    now_ist = datetime.now(IST)
    if now_ist.hour < CALLING_HOURS_START:
        # Before 9 AM today — schedule for 9 AM this morning
        target_ist = now_ist.replace(hour=CALLING_HOURS_START, minute=0, second=0, microsecond=0)
    else:
        # At or after 9 PM — schedule for 9 AM tomorrow
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


# --- Request models ---

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
    mode: str = "browser"  # "browser" = LiveKit room only; "phone" = also dial SIP


# --- App ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs AFTER uvicorn configures its own logging — our file handler won't be overwritten
    setup_logging("webhook")
    init_db()
    logger.info("Imagica webhook server started")
    worker_task = asyncio.create_task(queue_worker())
    yield
    worker_task.cancel()
    logger.info("Imagica webhook server shutting down")

app = FastAPI(title="Imagica Voice Agent Webhook", lifespan=lifespan)


@app.get("/")
async def dashboard():
    return FileResponse("dashboard.html")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/token")
async def get_room_token(room: str, identity: str = "developer"):
    """Generate a LiveKit participant token for browser-mode joining."""
    if not LIVEKIT_URL or LIVEKIT_URL == "ws://localhost:7880":
        raise HTTPException(
            status_code=500,
            detail="LIVEKIT_URL is not set to a cloud URL in .env — browser join won't work. Set LIVEKIT_URL=wss://your-project.livekit.cloud",
        )
    token = (
        AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name("Developer")
        .with_grants(VideoGrants(room_join=True, room=room))
    )
    return {"token": token.to_jwt(), "livekit_url": LIVEKIT_URL, "room": room}


@app.get("/metrics")
async def metrics():
    return get_metrics()


@app.get("/calls")
async def list_calls(limit: int = 20):
    """List recent calls with transcript + latency summary."""
    rows = get_call_logs()
    out = []
    for r in rows[:limit]:
        import json as _json
        transcript = []
        try:
            transcript = _json.loads(r.get("transcript") or "[]")
        except Exception:
            pass
        turns = []
        try:
            turns = _json.loads(r.get("latency_per_turn") or "[]")
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
    """Full detail for one call — transcript, latency breakdown, tool calls."""
    detail = get_call_detail(call_id)
    if not detail:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Call not found")
    return detail


@app.post("/webhook/cart-abandoned")
async def cart_abandoned(payload: CartAbandonedPayload):
    logger.info(
        f"Received cart-abandoned event: cart_id={payload.cart_id} "
        f"customer={payload.customer_name} phone={payload.customer_phone} "
        f"value=₹{payload.total_amount}"
    )

    # DND check — always suppress regardless of calling hours
    if payload.customer_phone in DND_LIST:
        logger.info(f"DND suppressed: {payload.customer_phone}")
        return {"status": "suppressed", "reason": "DND list", "cart_id": payload.cart_id}

    # Calling hours check: if outside 9 AM–9 PM IST, schedule for next window
    # instead of dropping the call (fixes the working.md gap: "webhook at 11 PM is dropped")
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


@app.post("/internal/schedule-retry")
async def internal_schedule_retry(cart: dict):
    """
    Called by the agent subprocess after a NO_ANSWER/BUSY call.
    Schedules the retry sleep + webhook re-fire inside uvicorn's event loop,
    which stays alive long after the agent subprocess exits.
    """
    asyncio.create_task(_delayed_retry(cart))
    logger.info(
        f"[RETRY] Scheduled attempt #{cart['attempt_number']} for "
        f"cart_id={cart['cart_id']} in {RETRY_DELAY_SECONDS}s"
    )
    return {"status": "retry_scheduled", "attempt_number": cart["attempt_number"]}


async def _dispatch_and_dial(queue_row: dict) -> None:
    """Create a LiveKit room, dispatch the agent, and optionally SIP-dial the customer.

    Called by queue_worker for each dequeued call. Marks the queue row 'done' on
    success or 'failed' on error so the dashboard can surface dispatch failures.
    """
    queue_id = queue_row["id"]
    cart_id = queue_row["cart_id"]
    attempt_number = queue_row.get("attempt_number", 1)
    payload_dict = json.loads(queue_row["cart_data"])
    mode = payload_dict.get("mode", "browser")

    room_name = f"imagica-{cart_id}-{attempt_number}"

    # Enrich the raw payload into the cart_data shape that agent.py expects
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
        async with lkapi.LiveKitAPI(
            url=LIVEKIT_URL,
            api_key=LIVEKIT_API_KEY,
            api_secret=LIVEKIT_API_SECRET,
        ) as lk:
            await lk.room.create_room(
                lkapi.CreateRoomRequest(name=room_name, empty_timeout=1800)
            )
            logger.info(f"[QUEUE] Room ready: {room_name}")

            # Idempotency guard — delete stale dispatches before creating a new one
            existing = await lk.agent_dispatch.list_dispatch(room_name)
            for d in existing:
                await lk.agent_dispatch.delete_dispatch(d.id, room_name)
                logger.info(f"[QUEUE] Deleted stale dispatch {d.id} for {room_name}")

            dispatch = await lk.agent_dispatch.create_dispatch(
                lkapi.CreateAgentDispatchRequest(
                    agent_name=AGENT_NAME,
                    room=room_name,
                    metadata=json.dumps(cart_data),
                )
            )
            logger.info(
                f"[QUEUE] Agent dispatched: dispatch_id={dispatch.id} "
                f"room={room_name} customer={cart_data['customer_name']} "
                f"value=₹{cart_data['total_amount']}"
            )

        mark_queue_done(queue_id)

        if mode == "phone":
            asyncio.create_task(dial_customer(room_name, payload_dict["customer_phone"]))

    except Exception as exc:
        logger.error(
            f"[QUEUE] Dispatch failed for cart_id={cart_id}: {exc}\n{traceback.format_exc()}"
        )
        mark_queue_failed(queue_id)


async def queue_worker() -> None:
    """Background task: every 10 s, dispatch the highest-value pending call.

    Priority is cart_value DESC (PRD §5.5) — a ₹15,000 family booking is always
    dispatched before a ₹1,500 single ticket. Calls with a future scheduled_at
    (e.g. outside calling hours) are skipped until their window opens.
    """
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


async def dial_customer(room_name: str, phone: str) -> None:
    """
    Place an outbound SIP call to the customer's phone and put them into room_name.
    Runs as a background task — the webhook returns 200 immediately while this dials.
    wait_until_answered=True holds the SIP leg open until the customer picks up,
    so Priya only starts speaking after the call is answered (not during ringback).
    No-op when LIVEKIT_SIP_TRUNK_ID is not set (playground / browser testing mode).
    """
    if not SIP_TRUNK_ID:
        logger.info("LIVEKIT_SIP_TRUNK_ID not set — skipping SIP dial (playground mode)")
        return
    try:
        async with lkapi.LiveKitAPI(
            url=LIVEKIT_URL,
            api_key=LIVEKIT_API_KEY,
            api_secret=LIVEKIT_API_SECRET,
        ) as lk:
            await lk.sip.create_sip_participant(
                lkapi.CreateSIPParticipantRequest(
                    sip_trunk_id=SIP_TRUNK_ID,
                    sip_call_to=phone,
                    room_name=room_name,
                    participant_identity="customer",
                    participant_name="Customer",
                    wait_until_answered=True,
                )
            )
            logger.info(f"SIP call answered: {phone} joined {room_name}")
    except Exception as exc:
        logger.error(f"SIP dial failed for {phone} in {room_name}: {exc}")


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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
