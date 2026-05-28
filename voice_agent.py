"""
voice_agent.py — Twilio ↔ ElevenLabs Conversational AI WebSocket bridge.

Responsibilities:
  - WebSocket endpoint /twilio/media-stream
  - Connect to ElevenLabs Conversational AI WebSocket
  - Bridge audio: Twilio µ-law → PCM → ElevenLabs, ElevenLabs PCM → µ-law → Twilio
  - Handle ElevenLabs tool call events → execute local functions
  - Track transcript, disposition, latency
  - Post-call: log to SQLite, send SMS, schedule retry

Usage: mount media_stream_handler as a WebSocket route in your FastAPI app.
"""
import asyncio
import audioop
import base64
import json
import logging
import os
import time
from datetime import datetime

import websockets
from dotenv import load_dotenv
from fastapi import WebSocket

from agent import build_system_prompt, _detect_language
from post_call import (
    DISPOSITION_NO_ANSWER,
    DISPOSITION_INTERESTED_LINK_SENT,
    DISPOSITION_CALLBACK_SCHEDULED,
    DISPOSITION_NOT_INTERESTED,
    DISPOSITION_TRANSFERRED,
    DISPOSITION_UNREACHABLE,
    log_call,
    init_db,
)
from retry import RETRYABLE_DISPOSITIONS, MAX_ATTEMPTS, schedule_retry
from sms import send_booking_sms

load_dotenv()
logger = logging.getLogger("imagica-voice-agent")

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID", "")
ELEVENLABS_WSS_URL = "wss://api.elevenlabs.io/v1/convai/conversation"

# Twilio sends 8 kHz µ-law; ElevenLabs expects 16 kHz PCM 16-bit mono.
TWILIO_SAMPLE_RATE = 8000
ELEVENLABS_SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2  # bytes per sample (16-bit)


# ---------------------------------------------------------------------------
# Audio conversion helpers
# ---------------------------------------------------------------------------

def mulaw_to_pcm16k(mulaw_bytes: bytes) -> bytes:
    """Convert Twilio µ-law 8 kHz to PCM 16-bit 16 kHz for ElevenLabs."""
    pcm_8k = audioop.ulaw2lin(mulaw_bytes, SAMPLE_WIDTH)
    pcm_16k, _ = audioop.ratecv(
        pcm_8k, SAMPLE_WIDTH, 1, TWILIO_SAMPLE_RATE, ELEVENLABS_SAMPLE_RATE, None
    )
    return pcm_16k


def pcm16k_to_mulaw(pcm_bytes: bytes) -> bytes:
    """Convert ElevenLabs PCM 16-bit 16 kHz to Twilio µ-law 8 kHz."""
    pcm_8k, _ = audioop.ratecv(
        pcm_bytes, SAMPLE_WIDTH, 1, ELEVENLABS_SAMPLE_RATE, TWILIO_SAMPLE_RATE, None
    )
    return audioop.lin2ulaw(pcm_8k, SAMPLE_WIDTH)


# ---------------------------------------------------------------------------
# Tool execution — mirrors the tools in agent.py but for the WebSocket path
# ---------------------------------------------------------------------------

async def execute_tool(
    tool_name: str,
    parameters: dict,
    cart: dict,
    state: dict,
) -> str:
    """
    Execute a tool call received from ElevenLabs and return the result string.
    `state` is a mutable dict shared with the caller so the handler can track
    disposition, discount, tool_calls, and the SMS-sent dedup flag.
    """
    phone = cart.get("customer_phone", "")
    name = cart.get("customer_name", "Customer")
    cart_id = cart.get("cart_id", "unknown")
    booking_link = cart.get("booking_link", f"https://imagicaa.com/book?cart={cart_id}")

    ts = datetime.now().isoformat()

    if tool_name == "send_booking_link":
        state["tool_calls"].append({"tool": tool_name, "ts": ts, "args": parameters})
        state["disposition"] = DISPOSITION_INTERESTED_LINK_SENT
        if not state["sms_sent"]:
            state["sms_sent"] = True
            await send_booking_sms(phone, name, booking_link)
        logger.info(f"[TOOL] send_booking_link → SMS sent to {phone}")
        return f"Booking link sent to {phone}. Link: {booking_link}"

    if tool_name == "apply_discount":
        discount = float(parameters.get("discount_percent", 5))
        discount = max(5.0, min(10.0, discount))
        discount_pct = int(discount) if discount == int(discount) else discount
        original = cart.get("total_amount", 0)
        discounted = round(original * (1 - discount_pct / 100))
        discounted_link = f"{booking_link}&discount={discount_pct}"

        state["tool_calls"].append({"tool": tool_name, "ts": ts, "args": parameters})
        state["discount"] = discount_pct
        state["disposition"] = DISPOSITION_INTERESTED_LINK_SENT
        if not state["sms_sent"]:
            state["sms_sent"] = True
            await send_booking_sms(phone, name, discounted_link)
        logger.info(f"[TOOL] apply_discount → {discount_pct}% | ₹{original} → ₹{discounted} | SMS sent")
        return (
            f"Applied {discount_pct}% discount. "
            f"New total: ₹{discounted} (was ₹{original}). "
            f"Updated booking link sent via SMS."
        )

    if tool_name == "schedule_callback":
        preferred_time = parameters.get("preferred_time", "not specified")
        state["tool_calls"].append({"tool": tool_name, "ts": ts, "args": parameters})
        state["disposition"] = DISPOSITION_CALLBACK_SCHEDULED
        logger.info(f"[TOOL] schedule_callback → {phone} at {preferred_time}")
        # TODO: write to CRM / scheduling system
        return f"Callback scheduled for {preferred_time}."

    if tool_name == "transfer_to_human":
        reason = parameters.get("reason", "customer requested")
        state["tool_calls"].append({"tool": tool_name, "ts": ts, "args": parameters})
        state["disposition"] = DISPOSITION_TRANSFERRED
        logger.info(f"[TOOL] transfer_to_human → reason: {reason}")
        # TODO: initiate SIP transfer to CCT queue
        return "Transferring you to our customer care team now. Please hold."

    if tool_name == "mark_not_interested":
        reason = parameters.get("reason", "not specified")
        state["tool_calls"].append({"tool": tool_name, "ts": ts, "args": parameters})
        state["disposition"] = DISPOSITION_NOT_INTERESTED
        logger.info(f"[TOOL] mark_not_interested → {phone} | reason: {reason}")
        # TODO: update CRM opt-out flag
        return (
            "Understood. Say a warm, brief goodbye: "
            "'Theek hai, koi baat nahi. Aapka bahut shukriya aur have a great day!' "
            "Then end the conversation."
        )

    logger.warning(f"[TOOL] Unknown tool: {tool_name}")
    return f"Unknown tool: {tool_name}"


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

async def media_stream_handler(websocket: WebSocket, call_sid: str, cart: dict):
    """
    WebSocket handler for Twilio Media Streams.

    Mount this in your FastAPI app:
        @app.websocket("/twilio/media-stream")
        async def ws_endpoint(websocket: WebSocket, call_sid: str = Query(...)):
            cart = get_cart_for_call(call_sid)   # your lookup here
            await media_stream_handler(websocket, call_sid, cart)
    """
    init_db()
    await websocket.accept()
    logger.info(f"[WS] Twilio connected — call_sid={call_sid} cart_id={cart.get('cart_id')}")

    # Mutable state shared across the three bridge coroutines
    state: dict = {
        "disposition": DISPOSITION_NO_ANSWER,
        "discount": 0,
        "sms_sent": False,
        "tool_calls": [],
        "transcript": [],          # [{"role": "agent"|"user", "text": "...", "ts": "..."}]
        "latency_per_turn": [],    # [int ms, ...]
        "first_response_ms": None,
        "call_connected_at": datetime.now().isoformat(),
        "call_start": time.time(),
        # VAD latency: timestamp when user last stopped speaking
        "_user_stopped_at": 0.0,
    }

    stream_sid: list[str] = [""]  # Twilio stream SID (captured from 'start' event)

    # Build ElevenLabs session init payload with dynamic system prompt
    session_init = {
        "type": "conversation_initiation_client_data",
        "conversation_config_override": {
            "agent": {
                "prompt": {
                    "prompt": build_system_prompt(cart)
                },
                "first_message": (
                    f"Hello, {cart['customer_name']} ji! "
                    "Main Priya bol rahi hoon, Imagicaa se."
                ),
            },
            "tts": {
                "output_format": "pcm_16000",
            },
        },
    }

    el_headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
    }
    el_url = f"{ELEVENLABS_WSS_URL}?agent_id={ELEVENLABS_AGENT_ID}"

    try:
        async with websockets.connect(el_url, additional_headers=el_headers) as el_ws:
            logger.info("[EL] Connected to ElevenLabs Conversational AI")

            # Send dynamic config override immediately after connecting
            await el_ws.send(json.dumps(session_init))

            # ------------------------------------------------------------------
            # Task 1 — Twilio → ElevenLabs: receive Twilio audio, forward to EL
            # ------------------------------------------------------------------
            async def twilio_to_elevenlabs():
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    event = data.get("event")

                    if event == "start":
                        stream_sid[0] = data["start"]["streamSid"]
                        logger.info(f"[WS] Stream started — streamSid={stream_sid[0]}")

                    elif event == "media":
                        mulaw_bytes = base64.b64decode(data["media"]["payload"])
                        pcm_bytes = mulaw_to_pcm16k(mulaw_bytes)
                        # ElevenLabs expects base64-encoded raw PCM in user_audio_chunk
                        await el_ws.send(json.dumps({
                            "user_audio_chunk": base64.b64encode(pcm_bytes).decode()
                        }))
                        # User speaking → reset latency clock (VAD "speaking" state)
                        state["_user_stopped_at"] = 0.0

                    elif event == "stop":
                        logger.info("[WS] Twilio stream stopped")
                        # Signal user stopped speaking — start latency clock
                        state["_user_stopped_at"] = time.time()
                        break

                    elif event == "mark":
                        pass  # acknowledgement — no action needed

            # ------------------------------------------------------------------
            # Task 2 — ElevenLabs → Twilio: receive EL events, forward audio
            # ------------------------------------------------------------------
            async def elevenlabs_to_twilio():
                async for raw in el_ws:
                    msg = json.loads(raw)
                    msg_type = msg.get("type")

                    # --- Audio output from ElevenLabs → Twilio ---
                    if msg_type == "audio":
                        audio_event = msg.get("audio_event", {})
                        audio_b64 = audio_event.get("audio_base_64", "")
                        if audio_b64:
                            pcm_bytes = base64.b64decode(audio_b64)
                            mulaw_bytes = pcm16k_to_mulaw(pcm_bytes)
                            payload = base64.b64encode(mulaw_bytes).decode()
                            await websocket.send_json({
                                "event": "media",
                                "streamSid": stream_sid[0],
                                "media": {"payload": payload},
                            })
                        # First audio chunk → measure latency
                        if state["_user_stopped_at"] > 0:
                            e2e_ms = int((time.time() - state["_user_stopped_at"]) * 1000)
                            state["_user_stopped_at"] = 0.0
                            if e2e_ms < 15_000:  # discard outliers > 15s
                                state["latency_per_turn"].append(e2e_ms)
                                if state["first_response_ms"] is None:
                                    state["first_response_ms"] = e2e_ms
                                logger.info(
                                    f"[LATENCY] e2e={e2e_ms}ms | "
                                    f"turn={len(state['latency_per_turn'])}"
                                )

                    # --- Agent text transcript ---
                    elif msg_type == "agent_response":
                        text = msg.get("agent_response_event", {}).get("agent_response", "")
                        if text:
                            ts = datetime.now().isoformat()
                            state["transcript"].append({"role": "agent", "text": text, "ts": ts})
                            logger.info(f"[TRANSCRIPT] Priya: {text}")

                    # --- User (customer) transcript ---
                    elif msg_type == "user_transcript":
                        text = msg.get("user_transcription_event", {}).get("user_transcript", "")
                        if text:
                            ts = datetime.now().isoformat()
                            state["transcript"].append({"role": "user", "text": text, "ts": ts})
                            logger.info(f"[TRANSCRIPT] Customer: {text}")
                            # User finished speaking → start latency clock
                            state["_user_stopped_at"] = time.time()

                    # --- Interruption — user barged in ---
                    elif msg_type == "interruption":
                        # Clear any buffered Twilio audio with a clear event
                        if stream_sid[0]:
                            await websocket.send_json({
                                "event": "clear",
                                "streamSid": stream_sid[0],
                            })
                        state["_user_stopped_at"] = 0.0

                    # --- Tool calls handled by Task 3 via a queue ---
                    elif msg_type == "client_tool_call":
                        await _tool_queue.put(msg)

                    # --- Conversation end signal from ElevenLabs ---
                    elif msg_type == "conversation_end":
                        reason = msg.get("conversation_end_event", {}).get("reason", "unknown")
                        logger.info(f"[EL] Conversation ended — reason: {reason}")
                        break

                    elif msg_type == "ping":
                        # Respond to keep-alive pings
                        await el_ws.send(json.dumps({
                            "type": "pong",
                            "event_id": msg.get("ping_event", {}).get("event_id"),
                        }))

            # ------------------------------------------------------------------
            # Task 3 — Tool calls: dequeue from EL, execute, return result
            # ------------------------------------------------------------------
            _tool_queue: asyncio.Queue = asyncio.Queue()

            async def handle_tool_calls():
                while True:
                    try:
                        msg = await asyncio.wait_for(_tool_queue.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue

                    tool_call = msg.get("client_tool_call", {})
                    tool_name = tool_call.get("tool_name", "")
                    parameters = tool_call.get("parameters", {})
                    tool_call_id = tool_call.get("tool_call_id", "")

                    logger.info(f"[TOOL] {tool_name} called — params={parameters}")
                    try:
                        result = await execute_tool(tool_name, parameters, cart, state)
                        is_error = False
                    except Exception as exc:
                        result = f"Tool execution error: {exc}"
                        is_error = True
                        logger.error(f"[TOOL] {tool_name} error: {exc}")

                    await el_ws.send(json.dumps({
                        "type": "client_tool_result",
                        "tool_call_id": tool_call_id,
                        "result": result,
                        "is_error": is_error,
                    }))

            # Run all three tasks concurrently; the first to finish cancels the others
            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(twilio_to_elevenlabs(), name="twilio_to_el"),
                    asyncio.create_task(elevenlabs_to_twilio(), name="el_to_twilio"),
                    asyncio.create_task(handle_tool_calls(), name="tool_calls"),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            # Surface any exceptions from completed tasks
            for task in done:
                if task.exception():
                    logger.error(f"[WS] Task {task.get_name()} raised: {task.exception()}")

    except websockets.exceptions.WebSocketException as exc:
        logger.error(f"[EL] WebSocket error: {exc}")
    except Exception as exc:
        logger.error(f"[WS] Unexpected error: {exc}")
    finally:
        await _post_call(call_sid, cart, state)


# ---------------------------------------------------------------------------
# Post-call logging
# ---------------------------------------------------------------------------

async def _post_call(call_sid: str, cart: dict, state: dict) -> None:
    """Log call to SQLite, apply retry logic if needed."""
    duration_sec = int(time.time() - state["call_start"])
    disposition = state["disposition"]
    transcript = state["transcript"]
    tool_calls = state["tool_calls"]
    latency_per_turn = state["latency_per_turn"]

    # Map internal retry states to final CRM disposition on last attempt
    if (
        disposition in RETRYABLE_DISPOSITIONS
        and cart.get("attempt_number", 1) >= MAX_ATTEMPTS
    ):
        disposition = DISPOSITION_UNREACHABLE

    summary_map = {
        "INTERESTED_LINK_SENT": "Customer showed interest; booking link sent via SMS.",
        "CONVERTED": "Booking confirmed by customer.",
        "CALLBACK_SCHEDULED": "Customer requested callback at a later time.",
        "PRICE_OBJECTION": "Customer raised price concern; no commitment yet.",
        "DATE_CHANGE": "Customer wants a different visit date.",
        "NOT_INTERESTED": "Customer not interested; no further calls.",
        "UNREACHABLE": "Customer unreachable after all attempts.",
        "TRANSFERRED_TO_HUMAN": "Call transferred to human agent.",
        "TECHNICAL_FAILURE": "Call dropped due to technical issue.",
        "WRONG_NUMBER": "Customer confirmed wrong number.",
        "NO_ANSWER": "Call ended with no conclusive outcome.",
        "BUSY": "Customer was busy; retry scheduled.",
    }
    summary = summary_map.get(disposition, "Call ended.")

    if latency_per_turn:
        avg_ms = int(sum(latency_per_turn) / len(latency_per_turn))
        logger.info(
            f"[LATENCY SUMMARY] first={state['first_response_ms']}ms | "
            f"avg={avg_ms}ms | min={min(latency_per_turn)}ms | "
            f"max={max(latency_per_turn)}ms | turns={len(latency_per_turn)}"
        )

    logger.info(
        f"[CALL END] call_sid={call_sid} cart={cart.get('cart_id')} | "
        f"disposition={disposition} | duration={duration_sec}s | "
        f"discount={state['discount']}% | turns={len(transcript)}"
    )

    log_call(
        cart=cart,
        disposition=disposition,
        transcript=transcript,
        summary=summary,
        discount=state["discount"],
        called_at=state["call_connected_at"],
        call_placed_at=cart.get("call_placed_at"),
        call_connected_at=state["call_connected_at"],
        first_response_ms=state["first_response_ms"],
        latency_per_turn=latency_per_turn,
        tool_calls=tool_calls,
        language_detected=_detect_language(transcript),
        duration_sec=duration_sec,
    )
    logger.info(f"[CRM WRITE] cart_id={cart.get('cart_id')} disposition={disposition} saved to SQLite")

    # Schedule retry if applicable
    if (
        state["disposition"] in RETRYABLE_DISPOSITIONS
        and cart.get("attempt_number", 1) < MAX_ATTEMPTS
    ):
        await schedule_retry(cart)
