"""LiveKit + Gemini Live realtime agent worker (legacy).

Connects to a LiveKit room, runs the Gemini Live native-audio model as Priya,
exposes the booking tools, and writes post-call data. This is the original
architecture; the production path is now ElevenLabs + Twilio (``src.telephony``).

Run:
    python -m src.worker.livekit_agent dev
"""
import asyncio
import json
import logging
import time
from datetime import datetime

from google.genai import types as genai_types
from livekit import api as lkapi
from livekit.agents import (
    Agent,
    AgentSession,
    AutoSubscribe,
    JobContext,
    RoomInputOptions,
    WorkerOptions,
    cli,
    function_tool,
)
from livekit.plugins import google, noise_cancellation

from src.config import livekit_settings
from src.constants import Disposition, disposition_summary
from src.conversation import build_system_prompt, detect_language
from src.logging_setup import setup_logging, write_call_summary
from src.mock_data import CART_DATA  # fallback for local dev
from src.persistence import log_call
from src.retry import MAX_ATTEMPTS, RETRYABLE_DISPOSITIONS, schedule_retry
from src.sms import send_booking_sms

logger = logging.getLogger("imagica-agent")

# Discard latency measurements above this — usually VAD picking up room noise.
_LATENCY_CAP_MS = 15_000
_PARTICIPANT_TIMEOUT_SEC = 60


class PriyaAgent(Agent):
    """The Priya persona plus the five booking tools, for the realtime session."""

    def __init__(self, cart: dict, room_name: str, call_ended: asyncio.Event):
        super().__init__(instructions=build_system_prompt(cart))
        self.cart = cart
        self.room_name = room_name
        self.call_ended = call_ended
        self.disposition = Disposition.NO_ANSWER  # set by whichever tool fires last
        self.discount = 0
        self.called_at = datetime.now().isoformat()
        self.tool_calls: list[dict] = []
        self._sms_sent = False  # dedup guard — prevent duplicate SMS in one call

    def _record_tool(self, tool: str, args: dict | None = None) -> None:
        """Append a tool-call record with the current timestamp."""
        self.tool_calls.append(
            {"tool": tool, "ts": datetime.now().isoformat(), "args": args or {}}
        )

    async def on_enter(self) -> None:
        """Kick off the opening greeting (the model would otherwise wait silently)."""
        await self.session.generate_reply(
            instructions=(
                "Start the call now. Say ONLY a brief greeting: your name, that "
                "you're calling from Imagicaa. Nothing else — no cart details, no "
                "reason for calling yet. Just 1-2 sentences. Then stop and wait."
            )
        )

    @function_tool
    async def send_booking_link(self) -> str:
        """Send the booking link to the customer via SMS so they can complete the purchase.
        Call this only when the customer gives a clear affirmative signal.
        Clear triggers (send immediately): 'bhej do', 'send kar do', 'share kar do',
        'book kar lunga', 'book karti hoon', 'theek hai bhejo', 'okay send it', 'haan bhejo',
        'I'll check it out right now'.
        Ambiguous phrases ('main sochti hoon', 'later dekhta hoon', 'maybe', 'let me think')
        require asking the customer first — wait for a yes before calling this tool.
        IMPORTANT: Do NOT call this if apply_discount() was already called — that tool already
        sends the link. Calling both causes the customer to receive duplicate messages."""
        link = self.cart["booking_link"]
        phone = self.cart["customer_phone"]
        name = self.cart["customer_name"]
        self.disposition = Disposition.INTERESTED_LINK_SENT
        self._record_tool("send_booking_link")
        if not self._sms_sent:
            self._sms_sent = True
            await send_booking_sms(phone, name, link)
        asyncio.create_task(_exit_after_delay(self.call_ended, delay=90))
        return f"Booking link sent to {phone}. Link: {link}"

    @function_tool
    async def schedule_callback(self, preferred_time: str = "not specified") -> str:
        """Schedule a callback at a time the customer prefers.
        Use when customer says they're busy right now or says 'call me later'.

        Args:
            preferred_time: Customer's preferred callback time, e.g. 'tonight 8pm'.
        """
        self._record_tool("schedule_callback", {"preferred_time": preferred_time})
        logger.info("[MOCK] Callback scheduled for %s at: %s", self.cart["customer_name"], preferred_time)
        self.disposition = Disposition.CALLBACK_SCHEDULED
        asyncio.create_task(_exit_after_delay(self.call_ended, delay=90))
        return f"Callback scheduled for {preferred_time}."

    @function_tool
    async def transfer_to_human(self, reason: str = "customer requested") -> str:
        """Transfer the call to a human Imagicaa customer care agent.
        Use when customer is very upset, has a complex issue, or explicitly asks for a human.

        Args:
            reason: Brief reason for transfer, e.g. 'customer upset about pricing'.
        """
        self.disposition = Disposition.TRANSFERRED
        self._record_tool("transfer_to_human", {"reason": reason})
        logger.info("[TRANSFER] Reason: %s", reason)
        await self._dial_cct()
        # Give Priya 6s to finish the hold message, then leave the room.
        asyncio.create_task(_exit_after_delay(self.call_ended, delay=6))
        return "Transferring you to our customer care team now. Please hold."

    async def _dial_cct(self) -> None:
        """Bridge a human customer-care agent into the room via SIP (best-effort)."""
        if not (livekit_settings.LIVEKIT_SIP_TRUNK_ID and livekit_settings.CCT_DEMO_PHONE):
            logger.info("[TRANSFER] CCT_DEMO_PHONE or SIP_TRUNK_ID not set — mock transfer only")
            return
        try:
            async with lkapi.LiveKitAPI(
                url=livekit_settings.LIVEKIT_URL,
                api_key=livekit_settings.LIVEKIT_API_KEY,
                api_secret=livekit_settings.LIVEKIT_API_SECRET,
            ) as lk:
                await lk.sip.create_sip_participant(
                    lkapi.CreateSIPParticipantRequest(
                        sip_trunk_id=livekit_settings.LIVEKIT_SIP_TRUNK_ID,
                        sip_call_to=livekit_settings.CCT_DEMO_PHONE,
                        room_name=self.room_name,
                        participant_identity="cct-agent",
                        participant_name="Customer Care",
                        wait_until_answered=False,  # don't block Priya's hold message
                    )
                )
            logger.info("[TRANSFER] CCT dialed into room %s -> %s", self.room_name, livekit_settings.CCT_DEMO_PHONE)
        except Exception as exc:  # noqa: BLE001 — transfer failure must not crash the call
            logger.error("[TRANSFER] Failed to dial CCT: %s", exc)

    @function_tool
    async def mark_not_interested(self, reason: str = "not specified") -> str:
        """Mark this customer as not interested in completing the booking right now.
        Use only when customer clearly refuses and conversation is ending.

        Args:
            reason: Reason customer is not interested, e.g. 'changed plans'.
        """
        self._record_tool("mark_not_interested", {"reason": reason})
        logger.info("[MOCK] Marking %s as not interested. Reason: %s", self.cart["customer_name"], reason)
        self.disposition = Disposition.NOT_INTERESTED
        asyncio.create_task(_exit_after_delay(self.call_ended, delay=15))
        return (
            "Understood. Say a warm, brief goodbye to the customer — "
            "something like: 'Theek hai, koi baat nahi. Aapka bahut shukriya aur "
            "have a great day!' Then end the conversation."
        )

    @function_tool
    async def apply_discount(self, discount_percent: int = 5) -> str:
        """Apply a discount to the customer's cart to incentivize booking.
        Use when customer hesitates due to price. Maximum discount is 10%.

        Args:
            discount_percent: Discount percentage to apply. Must be between 5 and 10.
        """
        discount_percent = max(5, min(10, int(discount_percent)))
        original = self.cart["total_amount"]
        discounted = round(original * (1 - discount_percent / 100))
        logger.info("[MOCK] Applying %s%% discount. Rs.%s -> Rs.%s", discount_percent, original, discounted)
        self.discount = discount_percent
        self.disposition = Disposition.INTERESTED_LINK_SENT  # discount + link sent together
        self._record_tool("apply_discount", {"discount_percent": discount_percent})
        if not self._sms_sent:
            self._sms_sent = True
            await send_booking_sms(
                self.cart["customer_phone"],
                self.cart["customer_name"],
                self.cart["booking_link"],
            )
        asyncio.create_task(_exit_after_delay(self.call_ended, delay=90))
        return (
            f"Applied {discount_percent}% discount. "
            f"New total: ₹{discounted} (was ₹{original}). "
            f"Updated booking link sent via SMS."
        )


async def _exit_after_delay(event: asyncio.Event, delay: int) -> None:
    """Set ``event`` after ``delay`` seconds (lets Priya finish a handoff message)."""
    await asyncio.sleep(delay)
    event.set()


def _build_realtime_model() -> "google.beta.realtime.RealtimeModel":
    """Build the Gemini Live realtime model with VAD tuned for low latency."""
    return google.beta.realtime.RealtimeModel(
        model=livekit_settings.GEMINI_MODEL,
        vertexai=True,
        project=livekit_settings.GOOGLE_CLOUD_PROJECT,
        location=livekit_settings.GOOGLE_CLOUD_LOCATION,
        voice="Aoede",
        temperature=0.6,
        input_audio_transcription=genai_types.AudioTranscriptionConfig(),
        output_audio_transcription=genai_types.AudioTranscriptionConfig(),
        realtime_input_config=genai_types.RealtimeInputConfig(
            automatic_activity_detection=genai_types.AutomaticActivityDetection(
                disabled=False,
                start_of_speech_sensitivity=genai_types.StartSensitivity.START_SENSITIVITY_LOW,
                end_of_speech_sensitivity=genai_types.EndSensitivity.END_SENSITIVITY_HIGH,
                prefix_padding_ms=20,
                silence_duration_ms=300,  # reduced from 500ms -> saves ~200ms/turn
            )
        ),
    )


def _load_cart(ctx: JobContext) -> dict:
    """Return the cart from the job metadata, falling back to mock data."""
    if ctx.job.metadata:
        try:
            cart = json.loads(ctx.job.metadata)
            logger.info("Loaded cart from job metadata: cart_id=%s", cart.get("cart_id"))
            return cart
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse job metadata, using mock data: %s", exc)
    return CART_DATA


def _wire_session_observers(session: AgentSession, transcript_lines: list[dict], perf: dict) -> None:
    """Attach transcript and latency observers to the session."""
    user_stopped_at = [0.0]  # timestamp when the user last stopped speaking

    @session.on("conversation_item_added")
    def on_item_added(ev) -> None:
        msg = ev.item
        role = "agent" if msg.role == "assistant" else "user"
        text = msg.text_content or ""
        if text:
            ts = datetime.fromtimestamp(msg.created_at).isoformat()
            transcript_lines.append({"role": role, "text": text, "ts": ts})
            logger.info("[TRANSCRIPT] %s: %s", "Priya" if role == "agent" else "Customer", text)

    @session.on("user_state_changed")
    def on_user_state_changed(ev) -> None:
        if ev.new_state == "listening":
            user_stopped_at[0] = time.time()  # user stopped — start the latency clock

    @session.on("agent_state_changed")
    def on_state_changed(ev) -> None:
        if ev.new_state == "speaking" and user_stopped_at[0] > 0:
            e2e_ms = int((time.time() - user_stopped_at[0]) * 1000)
            user_stopped_at[0] = 0.0
            if e2e_ms > _LATENCY_CAP_MS:
                logger.info("[LATENCY] skipped outlier %sms (> %sms cap)", e2e_ms, _LATENCY_CAP_MS)
                return
            perf["latency_per_turn"].append(e2e_ms)
            if perf["first_response_ms"] is None:
                perf["first_response_ms"] = e2e_ms
            logger.info("[LATENCY] e2e=%sms | turn=%s", e2e_ms, len(perf["latency_per_turn"]))


def _livekit_api() -> lkapi.LiveKitAPI:
    """Build a LiveKit API client from settings."""
    return lkapi.LiveKitAPI(
        url=livekit_settings.LIVEKIT_URL,
        api_key=livekit_settings.LIVEKIT_API_KEY,
        api_secret=livekit_settings.LIVEKIT_API_SECRET,
    )


async def _remove_sip_customer(room_name: str) -> None:
    """Drop the SIP customer participant, ending their phone call immediately."""
    try:
        async with _livekit_api() as lk:
            await lk.room.remove_participant(
                lkapi.RoomParticipantIdentity(room=room_name, identity="customer")
            )
        logger.info("[CALL END] SIP participant removed from %s — phone call dropped.", room_name)
    except Exception as exc:  # noqa: BLE001 — playground mode or already gone
        logger.info("[CALL END] remove_participant skipped: %s", exc)


async def _delete_room(room_name: str) -> None:
    """Delete the LiveKit room (best-effort cleanup)."""
    try:
        async with _livekit_api() as lk:
            await lk.room.delete_room(lkapi.DeleteRoomRequest(room=room_name))
        logger.info("[CALL END] Room %s deleted.", room_name)
    except Exception as exc:  # noqa: BLE001 — best-effort cleanup
        logger.warning("[CALL END] Room deletion failed: %s", exc)


def _log_no_participant(cart: dict) -> None:
    """Log a NO_ANSWER record when no participant joins (SIP dial failed)."""
    log_call(
        cart=cart,
        disposition=Disposition.NO_ANSWER,
        transcript=[],
        summary="No participant joined — SIP dial failed or timed out.",
        discount=0,
        called_at=datetime.now().isoformat(),
        call_placed_at=cart.get("call_placed_at"),
        call_connected_at=None,
        first_response_ms=None,
        latency_per_turn=[],
        tool_calls=[],
        language_detected="unknown",
        duration_sec=0,
    )


async def entrypoint(ctx: JobContext) -> None:
    """LiveKit job entrypoint: run one Priya call from connect to cleanup."""
    setup_logging("agent")
    logger.info("Agent starting, connecting to LiveKit room...")
    cart = _load_cart(ctx)

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    try:
        participant = await asyncio.wait_for(
            ctx.wait_for_participant(), timeout=_PARTICIPANT_TIMEOUT_SEC
        )
    except (TimeoutError, Exception) as exc:  # noqa: BLE001
        logger.warning(
            "[CALL SKIP] No participant joined within %ss for cart_id=%s: %s",
            _PARTICIPANT_TIMEOUT_SEC, cart.get("cart_id"), exc,
        )
        _log_no_participant(cart)
        return

    call_connected_at = datetime.now()
    logger.info("Participant joined: %s", participant.identity)

    call_ended = asyncio.Event()
    session = AgentSession(llm=_build_realtime_model())
    priya = PriyaAgent(cart, room_name=ctx.room.name, call_ended=call_ended)

    @ctx.room.on("participant_disconnected")
    def on_participant_left(p) -> None:
        logger.info("Participant left: %s — call ending", p.identity)
        call_ended.set()

    @session.on("close")
    def on_session_close() -> None:
        call_ended.set()

    transcript_lines: list[dict] = []
    perf: dict = {"first_response_ms": None, "latency_per_turn": []}
    _wire_session_observers(session, transcript_lines, perf)

    call_start = time.time()
    await session.start(
        room=ctx.room,
        agent=priya,
        room_input_options=RoomInputOptions(noise_cancellation=noise_cancellation.BVC()),
    )
    logger.info(
        "[CALL START] cart=%s customer=%s phone=%s attempt=%s",
        cart["cart_id"], cart["customer_name"], cart["customer_phone"], cart.get("attempt_number", 1),
    )

    await call_ended.wait()
    await _finalize_call(ctx, cart, priya, transcript_lines, perf, call_connected_at, call_start)


async def _finalize_call(
    ctx: JobContext,
    cart: dict,
    priya: PriyaAgent,
    transcript_lines: list[dict],
    perf: dict,
    call_connected_at: datetime,
    call_start: float,
) -> None:
    """Log the call, write its summary, schedule a retry, and tear down the room."""
    duration_sec = int(time.time() - call_start)
    attempt = cart.get("attempt_number", 1)

    if priya.disposition in RETRYABLE_DISPOSITIONS and attempt >= MAX_ATTEMPTS:
        priya.disposition = Disposition.UNREACHABLE

    summary = disposition_summary(priya.disposition)
    logger.info(
        "[CALL END] cart=%s | disposition=%s | duration=%ss | discount=%s%% | turns=%s | attempt=%s",
        cart["cart_id"], priya.disposition, duration_sec, priya.discount, len(transcript_lines), attempt,
    )

    log_call(
        cart=cart,
        disposition=priya.disposition,
        transcript=transcript_lines,
        summary=summary,
        discount=priya.discount,
        called_at=priya.called_at,
        call_placed_at=cart.get("call_placed_at"),
        call_connected_at=call_connected_at.isoformat(),
        first_response_ms=perf["first_response_ms"],
        latency_per_turn=perf["latency_per_turn"],
        tool_calls=priya.tool_calls,
        language_detected=detect_language(transcript_lines),
        duration_sec=duration_sec,
    )
    logger.info("[CRM WRITE] cart_id=%s disposition=%s saved to SQLite", cart["cart_id"], priya.disposition)

    write_call_summary(
        cart=cart,
        disposition=priya.disposition,
        duration_sec=duration_sec,
        discount=priya.discount,
        transcript=transcript_lines,
        tool_calls=priya.tool_calls,
        latency_per_turn=perf["latency_per_turn"],
        first_response_ms=perf["first_response_ms"],
        called_at=priya.called_at,
    )

    # Hand retry off to the FastAPI server (its event loop outlives this subprocess).
    if priya.disposition in RETRYABLE_DISPOSITIONS and attempt < MAX_ATTEMPTS:
        await schedule_retry(cart)

    # Capture room name now — ctx.room.name clears after disconnect().
    # Order matches the original: drop the SIP customer, disconnect, then delete.
    room_name = ctx.room.name or f"imagica-{cart['cart_id']}-{attempt}"
    await _remove_sip_customer(room_name)
    await ctx.room.disconnect()
    await _delete_room(room_name)


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="imagica-priya",  # must match the dispatch name
        )
    )
