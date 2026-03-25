"""
main.py — FastAPI webhook server for Imagica Voice Agent
Receives cart abandonment events and dispatches Priya (LiveKit agent) to call the customer.
"""
import asyncio
import json
import logging
import os
import traceback
from typing import List

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from livekit import api as lkapi

from retry import RETRY_DELAY_SECONDS

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("imagica-webhook")

LIVEKIT_URL = os.getenv("LIVEKIT_URL", "ws://localhost:7880")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "secret")
SIP_TRUNK_ID = os.getenv("LIVEKIT_SIP_TRUNK_ID")  # set to enable real phone calls; omit for playground testing
AGENT_NAME = "imagica-priya"

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


# --- App ---

app = FastAPI(title="Imagica Voice Agent Webhook")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhook/cart-abandoned")
async def cart_abandoned(payload: CartAbandonedPayload):
    logger.info(
        f"Received cart-abandoned event: cart_id={payload.cart_id} "
        f"customer={payload.customer_name} phone={payload.customer_phone}"
    )

    # DND check
    if payload.customer_phone in DND_LIST:
        logger.info(f"DND suppressed: {payload.customer_phone}")
        return {"status": "suppressed", "reason": "DND list", "cart_id": payload.cart_id}

    room_name = f"imagica-{payload.cart_id}-{payload.attempt_number}"

    # Build cart data dict that matches the shape agent.py expects
    cart_data = {
        "customer_name": payload.customer_name,
        "customer_phone": payload.customer_phone,
        "cart_id": payload.cart_id,
        "visit_date": payload.visit_date,
        "tickets": [t.model_dump() for t in payload.tickets],
        "total_amount": payload.total_amount,
        "park_name": "Imagicaa Theme Park, Khopoli",
        "booking_link": f"https://imagicaa.com/book?cart={payload.cart_id}",
        "attempt_number": payload.attempt_number,
    }

    dispatch_id = None
    try:
        lk = lkapi.LiveKitAPI(
            url=LIVEKIT_URL,
            api_key=LIVEKIT_API_KEY,
            api_secret=LIVEKIT_API_SECRET,
        )
        async with lk:
            # Create the room (idempotent — ok if it already exists)
            await lk.room.create_room(
                lkapi.CreateRoomRequest(name=room_name)
            )
            logger.info(f"Room ready: {room_name}")

            # Idempotency guard — delete any stale/orphaned dispatches before creating a new one.
            # "Block if exists" caused permanent deadlocks when the worker restarted mid-dispatch.
            existing = await lk.agent_dispatch.list_dispatch(room_name)
            for d in existing:
                await lk.agent_dispatch.delete_dispatch(d.id, room_name)
                logger.info(f"Deleted stale dispatch {d.id} for {room_name}")

            # Dispatch the agent job; cart data travels as JSON metadata
            dispatch = await lk.agent_dispatch.create_dispatch(
                lkapi.CreateAgentDispatchRequest(
                    agent_name=AGENT_NAME,
                    room=room_name,
                    metadata=json.dumps(cart_data),
                )
            )
            dispatch_id = dispatch.id
            logger.info(
                f"Agent dispatched: dispatch_id={dispatch_id} "
                f"room={room_name} customer={payload.customer_name}"
            )

        # Dial the customer in the background — webhook returns 200 while the phone rings.
        # wait_until_answered=True inside dial_customer ensures Priya only speaks after pickup.
        asyncio.create_task(dial_customer(room_name, payload.customer_phone))
    except Exception as exc:
        logger.error(f"Dispatch failed for cart {payload.cart_id}: {exc}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Agent dispatch failed: {exc}")

    return {
        "status": "dispatched",
        "cart_id": payload.cart_id,
        "room": room_name,
        "customer": payload.customer_name,
        "dispatch_id": dispatch_id,
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
