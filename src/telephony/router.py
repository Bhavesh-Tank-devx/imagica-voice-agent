"""Twilio voice webhooks and the media-stream WebSocket route."""
import asyncio
import json
import logging

from fastapi import APIRouter, Query, Request, WebSocket
from fastapi.responses import Response

from src.config import app_settings
from src.constants import AgentType
from src.retry import MAX_ATTEMPTS, schedule_retry
from src.telephony.bridge import media_stream_handler
from src.telephony.sessions import bind_call, cart_for_call, end_call

logger = logging.getLogger("imagica-webhook")

router = APIRouter()

_HANGUP_TWIML = (
    '<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>'
)
_TERMINAL_CALL_STATES = ("completed", "failed", "busy", "no-answer")
# Max Twilio messages to read while waiting for the "start" event.
_MAX_PREREAD = 5


@router.post("/twilio/answer")
async def twilio_answer(
    request: Request,
    cart_id: str = Query(...),
    agent_type: str = Query(default=AgentType.IMAGICA),
) -> Response:
    """Return TwiML opening a media stream when the customer answers.

    If AMD reports a machine (voicemail), hangs up instead.
    """
    form = await request.form()
    call_sid = form.get("CallSid", "")
    answered_by = form.get("AnsweredBy", "")

    if answered_by and answered_by not in ("human", "unknown"):
        logger.info("[AMD] Voicemail detected (AnsweredBy=%s) — hanging up call_sid=%s", answered_by, call_sid)
        return Response(content=_HANGUP_TWIML, media_type="text/xml")

    if call_sid and cart_id:
        bind_call(call_sid, cart_id)
        logger.info(
            "[TWILIO] Answer: call_sid=%s cart_id=%s agent_type=%s answered_by=%s",
            call_sid, cart_id, agent_type, answered_by or "n/a",
        )

    base = app_settings.BASE_URL.removeprefix("https://").removeprefix("http://")
    stream_url = f"wss://{base}/twilio/media-stream?cart_id={cart_id}"
    logger.info("[TWILIO] Stream URL -> %s", stream_url)
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        "    <Connect>\n"
        f'        <Stream url="{stream_url}" />\n'
        "    </Connect>\n"
        "</Response>"
    )
    return Response(content=twiml, media_type="text/xml")


@router.post("/twilio/status")
async def twilio_status(request: Request) -> Response:
    """Handle Twilio call lifecycle events; retry on no-answer / busy."""
    form = await request.form()
    call_sid = form.get("CallSid", "")
    call_status = form.get("CallStatus", "")
    answered_by = form.get("AnsweredBy", "")

    logger.info(
        "[TWILIO STATUS] call_sid=%s status=%s answered_by=%s",
        call_sid, call_status, answered_by or "n/a",
    )

    if call_status in _TERMINAL_CALL_STATES:
        cart = end_call(call_sid)
        if call_status in ("no-answer", "busy") and cart:
            if cart.get("attempt_number", 1) < MAX_ATTEMPTS:
                logger.info(
                    "[TWILIO STATUS] Scheduling retry for cart_id=%s (attempt %s of %s)",
                    cart.get("cart_id"), cart.get("attempt_number", 1), MAX_ATTEMPTS,
                )
                asyncio.create_task(schedule_retry(cart))

    return Response(content="", status_code=204)


@router.websocket("/twilio/media-stream")
async def twilio_media_stream(websocket: WebSocket) -> None:
    """Bridge Twilio media to ElevenLabs.

    Twilio strips query params from the WebSocket upgrade, so we accept first,
    read until the "start" event (which carries ``callSid``), then resolve the
    cart from the session stores. Pre-read messages are replayed by the handler.
    """
    await websocket.accept()

    preread: list[str] = []
    cart = None
    call_sid_ws = ""

    try:
        async for raw in websocket.iter_text():
            preread.append(raw)
            data = json.loads(raw)
            if data.get("event") == "start":
                call_sid_ws = data["start"].get("callSid", "")
                cart = cart_for_call(call_sid_ws)
                break
            if len(preread) >= _MAX_PREREAD:
                break
    except Exception as exc:  # noqa: BLE001 — malformed start frame closes the socket
        logger.error("[WS] Error reading start event: %s", exc)

    if not cart:
        logger.warning("[WS] No cart found for call_sid=%r — closing", call_sid_ws)
        await websocket.close(code=1008)
        return

    agent_type = cart.get("agent_type", AgentType.IMAGICA)
    await media_stream_handler(websocket, call_sid_ws, cart, agent_type=agent_type, preread=preread)
