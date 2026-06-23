"""Twilio <-> ElevenLabs Conversational AI WebSocket bridge.

Responsibilities:
  - Bridge audio both ways (Twilio mu-law <-> ElevenLabs PCM).
  - Execute tool calls the ElevenLabs agent requests.
  - Track transcript, disposition, and latency.
  - Post-call: log to SQLite, send SMS, schedule retry.

``media_stream_handler`` is mounted as the ``/twilio/media-stream`` WebSocket
route. The caller must already have accepted the socket and pass any pre-read
messages (consumed while finding the "start" event) via ``preread``.
"""
import asyncio
import base64
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime

import websockets
from fastapi import WebSocket

from src.config import elevenlabs_settings
from src.constants import AgentType, Disposition, disposition_summary
from src.conversation import (
    detect_language,
    fuzzy_correct_email,
    get_closest_branches,
    normalize_email,
)
from src.persistence import init_db, log_call, log_kaya_booking
from src.retry import MAX_ATTEMPTS, RETRYABLE_DISPOSITIONS, schedule_retry
from src.sms import send_booking_sms
from src.telephony.audio import mulaw_to_pcm16k, pcm16k_to_mulaw
from src.telephony.constants import (
    ANSWERED_THRESHOLD_SEC,
    ELEVENLABS_WSS_URL,
    KAYA_ASR_KEYWORDS,
)

logger = logging.getLogger("imagica-voice-agent")

# Discount bounds for the apply_discount tool.
_MIN_DISCOUNT_PCT = 5.0
_MAX_DISCOUNT_PCT = 10.0


# ---------------------------------------------------------------------------
# Tool execution — mirrors the realtime worker's tools for the WebSocket path
# ---------------------------------------------------------------------------

def _record_tool(state: dict, tool_name: str, parameters: dict) -> str:
    """Append a tool-call record to ``state`` and return its ISO timestamp."""
    ts = datetime.now().isoformat()
    state["tool_calls"].append({"tool": tool_name, "ts": ts, "args": parameters})
    return ts


async def _tool_send_booking_link(params: dict, cart: dict, state: dict) -> str:
    """Send the booking link via SMS (once per call)."""
    phone = cart.get("customer_phone", "")
    name = cart.get("customer_name", "Customer")
    booking_link = _booking_link(cart)
    state["disposition"] = Disposition.INTERESTED_LINK_SENT
    if not state["sms_sent"]:
        state["sms_sent"] = True
        await send_booking_sms(phone, name, booking_link)
    logger.info("[TOOL] send_booking_link -> SMS sent to %s", phone)
    return f"Booking link sent to {phone}. Link: {booking_link}"


async def _tool_apply_discount(params: dict, cart: dict, state: dict) -> str:
    """Apply a 5-10% discount and send the updated booking link via SMS."""
    discount = max(_MIN_DISCOUNT_PCT, min(_MAX_DISCOUNT_PCT, float(params.get("discount_percent", 5))))
    discount_pct = int(discount) if discount == int(discount) else discount
    original = cart.get("total_amount", 0)
    discounted = round(original * (1 - discount_pct / 100))
    discounted_link = f"{_booking_link(cart)}&discount={discount_pct}"

    state["discount"] = discount_pct
    state["disposition"] = Disposition.INTERESTED_LINK_SENT
    if not state["sms_sent"]:
        state["sms_sent"] = True
        await send_booking_sms(cart.get("customer_phone", ""), cart.get("customer_name", "Customer"), discounted_link)
    logger.info(
        "[TOOL] apply_discount -> %s%% | Rs.%s -> Rs.%s | SMS sent",
        discount_pct, original, discounted,
    )
    return (
        f"Applied {discount_pct}% discount. "
        f"New total: ₹{discounted} (was ₹{original}). "
        f"Updated booking link sent via SMS."
    )


async def _tool_schedule_callback(params: dict, cart: dict, state: dict) -> str:
    """Record a callback request (CRM write is a production TODO)."""
    preferred_time = params.get("preferred_time", "not specified")
    state["disposition"] = Disposition.CALLBACK_SCHEDULED
    logger.info("[TOOL] schedule_callback -> %s at %s", cart.get("customer_phone"), preferred_time)
    return f"Callback scheduled for {preferred_time}."


async def _tool_transfer_to_human(params: dict, cart: dict, state: dict) -> str:
    """Mark the call as transferred to a human agent."""
    reason = params.get("reason", "customer requested")
    state["disposition"] = Disposition.TRANSFERRED
    logger.info("[TOOL] transfer_to_human -> reason: %s", reason)
    return "Transferring you to our customer care team now. Please hold."


async def _tool_mark_not_interested(params: dict, cart: dict, state: dict) -> str:
    """Mark the customer as not interested and instruct a warm goodbye."""
    reason = params.get("reason", "not specified")
    state["disposition"] = Disposition.NOT_INTERESTED
    logger.info("[TOOL] mark_not_interested -> %s | reason: %s", cart.get("customer_phone"), reason)
    return (
        "Understood. Say a warm, brief goodbye: "
        "'Theek hai, koi baat nahi. Aapka bahut shukriya aur have a great day!' "
        "Then end the conversation."
    )


async def _tool_get_closest_branches(params: dict, cart: dict, state: dict) -> str:
    """Resolve Kaya branches for a pincode or city."""
    result = get_closest_branches(
        pincode=params.get("pincode", ""), city=params.get("city", "")
    )
    logger.info(
        "[TOOL] get_closest_branches -> city=%s branches=%s",
        result.get("city"), result.get("branches"),
    )
    return result["message"]


async def _tool_book_appointment(params: dict, cart: dict, state: dict) -> str:
    """Book a Kaya appointment, repairing the spelled-out email first."""
    try:
        first_name = params.get("first_name", "")
        last_name = params.get("last_name", "")
        email = fuzzy_correct_email(
            normalize_email(params.get("email", "")), first_name, last_name
        )
        booking_id = log_kaya_booking(
            cart_id=cart.get("cart_id", "unknown"),
            customer_phone=cart.get("customer_phone", ""),
            first_name=first_name,
            last_name=last_name,
            email=email,
            pincode=params.get("pincode", ""),
            branch_name=params.get("branch_name", ""),
            appointment_date=params.get("appointment_date", ""),
            appointment_time=params.get("appointment_time", ""),
            dob=params.get("dob", ""),
            city=params.get("city", ""),
            concern_summary=params.get("concern_summary", ""),
        )
        state["disposition"] = Disposition.CONVERTED
        logger.info(
            "[TOOL] book_appointment -> booking_id=%s branch=%s",
            booking_id, params.get("branch_name"),
        )
        return (
            f"Appointment confirmed! Booking ID: {booking_id}. "
            f"Branch: {params.get('branch_name')}. "
            f"Date: {params.get('appointment_date')} at {params.get('appointment_time')}."
        )
    except Exception as exc:  # noqa: BLE001 — surfaced back to the agent as a tool result
        logger.error("[TOOL] book_appointment error: %s", exc)
        return f"Booking failed due to a technical issue: {exc}"


async def _tool_end_call(params: dict, cart: dict, state: dict) -> str:
    """Acknowledge the agent's end-of-call signal."""
    logger.info("[TOOL] end_call -> disposition=%s", state["disposition"])
    return "Call ended."


_ToolHandler = Callable[[dict, dict, dict], Awaitable[str]]
_TOOL_HANDLERS: dict[str, _ToolHandler] = {
    "send_booking_link": _tool_send_booking_link,
    "apply_discount": _tool_apply_discount,
    "schedule_callback": _tool_schedule_callback,
    "transfer_to_human": _tool_transfer_to_human,
    "mark_not_interested": _tool_mark_not_interested,
    "get_closest_branches": _tool_get_closest_branches,
    "book_appointment": _tool_book_appointment,
    "end_call": _tool_end_call,
}


def _booking_link(cart: dict) -> str:
    """Return the cart's booking link, deriving a default from the cart id."""
    cart_id = cart.get("cart_id", "unknown")
    return cart.get("booking_link", f"https://imagicaa.com/book?cart={cart_id}")


async def execute_tool(tool_name: str, parameters: dict, cart: dict, state: dict) -> str:
    """Execute a tool call from ElevenLabs and return the result string.

    ``state`` is a mutable dict shared with the handler so disposition, discount,
    tool history, and the SMS-sent flag persist across the call.
    """
    handler = _TOOL_HANDLERS.get(tool_name)
    if handler is None:
        logger.warning("[TOOL] Unknown tool: %s", tool_name)
        return f"Unknown tool: {tool_name}"
    _record_tool(state, tool_name, parameters)
    return await handler(parameters, cart, state)


# ---------------------------------------------------------------------------
# Session init
# ---------------------------------------------------------------------------

def _build_session_init(cart: dict, agent_type: str) -> dict:
    """Build the ElevenLabs ``conversation_initiation_client_data`` payload."""
    session_init: dict = {
        "type": "conversation_initiation_client_data",
        "conversation_config_override": {"tts": {"output_format": "pcm_16000"}},
    }
    if agent_type != AgentType.KAYA:
        return session_init

    session_init["dynamic_variables"] = {
        "customer_phone": cart.get("customer_phone", ""),
        "customer_name": cart.get("customer_name", ""),
        "city": cart.get("city", ""),
        "call_type": cart.get("call_type", "OUTBOUND"),
    }
    if elevenlabs_settings.KAYA_ASR_KEYWORDS:
        session_init["conversation_config_override"]["asr"] = {"keywords": KAYA_ASR_KEYWORDS}
        logger.info("[EL] ASR keyword boosting enabled (%d keywords)", len(KAYA_ASR_KEYWORDS))
    if not elevenlabs_settings.ELEVENLABS_KAYA_AGENT_ID:
        logger.warning("[WS] ELEVENLABS_KAYA_AGENT_ID not set — falling back to ELEVENLABS_AGENT_ID")
    return session_init


def _elevenlabs_url(agent_type: str) -> str:
    """Return the ElevenLabs WebSocket URL for the campaign's agent."""
    agent_id = (
        elevenlabs_settings.kaya_agent_id
        if agent_type == AgentType.KAYA
        else elevenlabs_settings.ELEVENLABS_AGENT_ID
    )
    return f"{ELEVENLABS_WSS_URL}?agent_id={agent_id}"


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

async def media_stream_handler(
    websocket: WebSocket,
    call_sid: str,
    cart: dict,
    agent_type: str = AgentType.IMAGICA,
    preread: list | None = None,
) -> None:
    """Bridge a Twilio Media Stream to an ElevenLabs conversation.

    The caller must have accepted the socket and pass pre-read messages
    (consumed while finding the "start" event) via ``preread``.
    """
    init_db()
    logger.info(
        "[WS] Twilio connected — call_sid=%s cart_id=%s agent_type=%s",
        call_sid, cart.get("cart_id"), agent_type,
    )

    state: dict = {
        "disposition": Disposition.NO_ANSWER,
        "discount": 0,
        "sms_sent": False,
        "tool_calls": [],
        "transcript": [],          # [{"role", "text", "ts"}]
        "latency_per_turn": [],    # [int ms, ...]
        "first_response_ms": None,
        "call_connected_at": datetime.now().isoformat(),
        "call_start": time.time(),
        "agent_type": agent_type,
        "_user_stopped_at": 0.0,
    }
    stream_sid = [""]  # Twilio stream SID (captured from the 'start' event)

    session_init = _build_session_init(cart, agent_type)
    el_url = _elevenlabs_url(agent_type)
    el_headers = {"xi-api-key": elevenlabs_settings.ELEVENLABS_API_KEY}

    try:
        async with websockets.connect(el_url, additional_headers=el_headers) as el_ws:
            logger.info("[EL] Connected to ElevenLabs Conversational AI")
            await el_ws.send(json.dumps(session_init))
            await _run_bridge(websocket, el_ws, cart, state, stream_sid, preread)
    except websockets.exceptions.WebSocketException as exc:
        logger.error("[EL] WebSocket error: %s", exc)
    except Exception as exc:  # noqa: BLE001 — last-resort guard around a live call
        logger.error("[WS] Unexpected error: %s", exc)
    finally:
        await _post_call(call_sid, cart, state)


async def _run_bridge(
    websocket: WebSocket,
    el_ws,
    cart: dict,
    state: dict,
    stream_sid: list[str],
    preread: list | None,
) -> None:
    """Run the three bridge coroutines until the first one completes."""
    tool_queue: asyncio.Queue = asyncio.Queue()

    async def twilio_to_elevenlabs() -> None:
        for raw in preread or []:
            await _forward_twilio_message(json.loads(raw), el_ws, stream_sid, state, preread=True)
        async for message in websocket.iter_text():
            if await _forward_twilio_message(json.loads(message), el_ws, stream_sid, state):
                break  # Twilio stream stopped

    async def elevenlabs_to_twilio() -> None:
        async for raw in el_ws:
            if await _handle_elevenlabs_message(json.loads(raw), websocket, el_ws, state, stream_sid, tool_queue):
                break  # conversation ended

    async def handle_tool_calls() -> None:
        await _consume_tool_calls(tool_queue, el_ws, cart, state)

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
    for task in done:
        if task.exception():
            logger.error("[WS] Task %s raised: %s", task.get_name(), task.exception())


async def _forward_twilio_message(
    data: dict, el_ws, stream_sid: list[str], state: dict, preread: bool = False
) -> bool:
    """Forward one Twilio message to ElevenLabs. Returns True if the stream stopped."""
    event = data.get("event")
    if event == "start":
        stream_sid[0] = data["start"].get("streamSid", "")
        logger.info("[WS] Stream started%s — streamSid=%s", " (preread)" if preread else "", stream_sid[0])
    elif event == "media":
        pcm_bytes = mulaw_to_pcm16k(base64.b64decode(data["media"]["payload"]))
        await el_ws.send(json.dumps({"user_audio_chunk": base64.b64encode(pcm_bytes).decode()}))
        if not preread:
            state["_user_stopped_at"] = 0.0  # user speaking — reset latency clock
    elif event == "stop" and not preread:
        logger.info("[WS] Twilio stream stopped")
        state["_user_stopped_at"] = time.time()
        return True
    return False


async def _handle_elevenlabs_message(
    msg: dict, websocket: WebSocket, el_ws, state: dict,
    stream_sid: list[str], tool_queue: asyncio.Queue,
) -> bool:
    """Handle one ElevenLabs event. Returns True when the conversation ends."""
    msg_type = msg.get("type")

    if msg_type == "audio":
        await _forward_agent_audio(msg, websocket, state, stream_sid)
    elif msg_type == "agent_response":
        _append_transcript(state, "agent", msg.get("agent_response_event", {}).get("agent_response", ""), "Priya")
    elif msg_type == "user_transcript":
        text = msg.get("user_transcription_event", {}).get("user_transcript", "")
        if _append_transcript(state, "user", text, "Customer"):
            state["_user_stopped_at"] = time.time()  # user finished — start latency clock
    elif msg_type == "interruption":
        if stream_sid[0]:
            await websocket.send_json({"event": "clear", "streamSid": stream_sid[0]})
        state["_user_stopped_at"] = 0.0
    elif msg_type == "client_tool_call":
        await tool_queue.put(msg)
    elif msg_type == "conversation_end":
        reason = msg.get("conversation_end_event", {}).get("reason", "unknown")
        logger.info("[EL] Conversation ended — reason: %s", reason)
        return True
    elif msg_type == "ping":
        await el_ws.send(json.dumps({"type": "pong", "event_id": msg.get("ping_event", {}).get("event_id")}))
    return False


async def _forward_agent_audio(msg: dict, websocket: WebSocket, state: dict, stream_sid: list[str]) -> None:
    """Forward an ElevenLabs audio chunk to Twilio and measure response latency."""
    audio_b64 = msg.get("audio_event", {}).get("audio_base_64", "")
    if audio_b64:
        mulaw_bytes = pcm16k_to_mulaw(base64.b64decode(audio_b64))
        await websocket.send_json({
            "event": "media",
            "streamSid": stream_sid[0],
            "media": {"payload": base64.b64encode(mulaw_bytes).decode()},
        })
    if state["_user_stopped_at"] > 0:
        e2e_ms = int((time.time() - state["_user_stopped_at"]) * 1000)
        state["_user_stopped_at"] = 0.0
        if e2e_ms < 15_000:  # discard outliers > 15s
            state["latency_per_turn"].append(e2e_ms)
            if state["first_response_ms"] is None:
                state["first_response_ms"] = e2e_ms
            logger.info("[LATENCY] e2e=%sms | turn=%s", e2e_ms, len(state["latency_per_turn"]))


def _append_transcript(state: dict, role: str, text: str, speaker: str) -> bool:
    """Append a transcript turn if non-empty. Returns True if appended."""
    if not text:
        return False
    state["transcript"].append({"role": role, "text": text, "ts": datetime.now().isoformat()})
    logger.info("[TRANSCRIPT] %s: %s", speaker, text)
    return True


async def _consume_tool_calls(tool_queue: asyncio.Queue, el_ws, cart: dict, state: dict) -> None:
    """Dequeue tool calls, execute them, and return results to ElevenLabs."""
    while True:
        try:
            msg = await asyncio.wait_for(tool_queue.get(), timeout=1.0)
        except TimeoutError:
            continue

        tool_call = msg.get("client_tool_call", {})
        tool_name = tool_call.get("tool_name", "")
        parameters = tool_call.get("parameters", {})
        tool_call_id = tool_call.get("tool_call_id", "")

        logger.info("[TOOL] %s called — params=%s", tool_name, parameters)
        try:
            result = await execute_tool(tool_name, parameters, cart, state)
            is_error = False
        except Exception as exc:  # noqa: BLE001 — surfaced back to the agent
            result = f"Tool execution error: {exc}"
            is_error = True
            logger.error("[TOOL] %s error: %s", tool_name, exc)

        await el_ws.send(json.dumps({
            "type": "client_tool_result",
            "tool_call_id": tool_call_id,
            "result": result,
            "is_error": is_error,
        }))


# ---------------------------------------------------------------------------
# Post-call logging
# ---------------------------------------------------------------------------

async def _post_call(call_sid: str, cart: dict, state: dict) -> None:
    """Log the call to SQLite and schedule a retry if applicable."""
    duration_sec = int(time.time() - state["call_start"])
    disposition = state["disposition"]
    transcript = state["transcript"]
    latency_per_turn = state["latency_per_turn"]
    attempt = cart.get("attempt_number", 1)

    # A long call with no tool outcome means the customer was reached.
    if disposition == Disposition.NO_ANSWER and duration_sec > ANSWERED_THRESHOLD_SEC:
        disposition = Disposition.CALL_COMPLETED_NO_OUTCOME

    # Map internal retry states to a final disposition on the last attempt.
    if disposition in RETRYABLE_DISPOSITIONS and attempt >= MAX_ATTEMPTS:
        disposition = Disposition.UNREACHABLE

    if latency_per_turn:
        logger.info(
            "[LATENCY SUMMARY] first=%sms | avg=%sms | min=%sms | max=%sms | turns=%s",
            state["first_response_ms"],
            int(sum(latency_per_turn) / len(latency_per_turn)),
            min(latency_per_turn), max(latency_per_turn), len(latency_per_turn),
        )

    logger.info(
        "[CALL END] call_sid=%s cart=%s | disposition=%s | duration=%ss | discount=%s%% | turns=%s",
        call_sid, cart.get("cart_id"), disposition, duration_sec, state["discount"], len(transcript),
    )

    log_call(
        cart=cart,
        disposition=disposition,
        transcript=transcript,
        summary=disposition_summary(disposition),
        discount=state["discount"],
        called_at=state["call_connected_at"],
        call_placed_at=cart.get("call_placed_at"),
        call_connected_at=state["call_connected_at"],
        first_response_ms=state["first_response_ms"],
        latency_per_turn=latency_per_turn,
        tool_calls=state["tool_calls"],
        language_detected=detect_language(transcript),
        duration_sec=duration_sec,
        agent_type=state.get("agent_type", AgentType.IMAGICA),
    )
    logger.info("[CRM WRITE] cart_id=%s disposition=%s saved to SQLite", cart.get("cart_id"), disposition)

    # Retry only if the call was not actually answered.
    if (
        disposition in RETRYABLE_DISPOSITIONS
        and attempt < MAX_ATTEMPTS
        and duration_sec <= ANSWERED_THRESHOLD_SEC
    ):
        await schedule_retry(cart)
