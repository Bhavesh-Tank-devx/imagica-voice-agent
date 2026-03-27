# agent.py
import asyncio
import json
import logging
import os
import time
from datetime import datetime
from dotenv import load_dotenv

from google.genai import types as genai_types
from livekit import api as lkapi
from livekit.agents import (
    AgentSession,
    Agent,
    AutoSubscribe,
    JobContext,
    RoomInputOptions,
    WorkerOptions,
    cli,
    function_tool,
)
from livekit.plugins import google, noise_cancellation

from log_setup import setup_logging, write_call_summary
from mock_data import CART_DATA  # fallback for local dev (python agent.py dev)
from sms import send_booking_sms
from post_call import (
    DISPOSITION_INTERESTED_LINK_SENT,
    DISPOSITION_CALLBACK_SCHEDULED,
    DISPOSITION_NOT_INTERESTED,
    DISPOSITION_TRANSFERRED,
    DISPOSITION_NO_ANSWER,
    DISPOSITION_UNREACHABLE,
    log_call,
)
from retry import RETRYABLE_DISPOSITIONS, MAX_ATTEMPTS, schedule_retry

load_dotenv()
logger = logging.getLogger("imagica-agent")


def _extract_text(msg) -> str:
    """Pull plain text from a livekit ChatMessage or speech event object.
    Handles both string content and list-of-chunks content shapes."""
    try:
        content = msg.content if hasattr(msg, "content") else msg
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif hasattr(item, "text") and item.text:
                    parts.append(item.text)
            return " ".join(parts).strip()
    except Exception:
        pass
    return ""

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
MODEL = os.getenv("GEMINI_MODEL", "gemini-live-2.5-flash-native-audio")

LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")
SIP_TRUNK_ID = os.getenv("LIVEKIT_SIP_TRUNK_ID")
CCT_DEMO_PHONE = os.getenv("CCT_DEMO_PHONE")  # Imagica CCT queue number for live handoff


def build_system_prompt(cart: dict) -> str:
    tickets_summary = ", ".join(
        f"{t['quantity']} {t['type']}" for t in cart["tickets"]
    )
    return f"""
You are Priya, a warm and friendly customer care executive at Imagicaa Theme Park.
You speak in Hinglish — a natural mix of Hindi and English — the way urban Indians speak casually.
You are NOT a robot. You sound human, empathetic, and helpful.

## Your Goal
The customer {cart['customer_name']} added tickets to their cart but didn't complete the booking.
Your job is to gently remind them, understand their concern, and help them complete the purchase.

## Cart Details (reference this in conversation)
- Customer: {cart['customer_name']}
- Visit Date: {cart['visit_date']}
- Tickets: {tickets_summary}
- Total Amount: ₹{cart['total_amount']}
- Park: {cart['park_name']}

## Conversation Flow
1. **Opening** — Greet warmly, introduce yourself, mention the cart naturally (not robotically).
2. **Listen** — Ask why they didn't complete. Let them talk.
3. **Address concern** — Price issue? Offer discount (max 10%). Busy? Schedule callback. Confused? Send link.
4. **Close** — Send the link only when the customer gives a clear affirmative:
   - SEND immediately: "bhej do", "send kar do", "share kar do", "book kar lunga", "book karti hoon", "I'll check it out right now", "theek hai bhejo", "okay send it", "haan bhejo".
   - ASK FIRST, then send: "main sochti hoon", "later dekhta hoon", "maybe", "let me think", "I'll see". Respond with something like "Koi baat nahi — main link bhej deti hoon aapko, jab time mile dekh lena. Theek hai?" and wait. If they say yes or don't object, then call send_booking_link().
   - Do NOT send on pure ambiguity or silence.
5. **Exit gracefully** — Only call mark_not_interested() if customer says a clear "nahi chahiye", "cancel karo", "not interested", or "don't call again". After calling it, ALWAYS say a warm short goodbye before the call ends — e.g. "Theek hai, koi baat nahi. Aapka bahut shukriya aur have a great day!" Do NOT hang up silently.

## Language Rules (auto-switch per response)
- **Default: Hinglish** — natural Hindi + English mix, the way urban Indians speak.
- **Switch to pure Hindi** if the customer speaks 2+ consecutive turns with no English words at all.
- **Switch to pure English** if the customer speaks 2+ consecutive turns with no Hindi words at all.
- **Switch back to Hinglish** the moment the customer mixes languages again.
- Never switch mid-sentence. Finish the current sentence, then apply the new language.
- The gender rules below always apply regardless of language mode.

## Tone Rules
- Use "aap" (respectful) for the customer, never "tum"
- Be warm but not fake. Don't over-apologize.
- Keep sentences short. Real conversations have pauses.
- Never read out URLs — say "main aapko link bhej deti hoon SMS pe" (Hinglish/Hindi) or "I'll send you the link by SMS" (English)
- If you hear a very short, unrelated word, a name (e.g. "Pawan", "DCM"), single digits, or something that does not fit the conversation context, do NOT act on it. Say: "Sorry, kya aap mujhse baat kar rahe the?" and wait.
- If the customer does not respond to this clarification in their next turn, say "Koi baat nahi, main baad mein call karti hoon" and end the call gracefully.

## Hindi Gender Rules (STRICT — never break these)
You are a woman. Always use feminine verb forms in Hindi. Never use masculine -a endings for yourself.
Correct feminine forms to always use:
- "main bol RAHI hoon" (never "raha hoon")
- "main karti hoon" (never "karta hoon")
- "main samajhti hoon" (never "samajhta hoon")
- "main chahti hoon" (never "chahta hoon")
- "main bhej RAHI hoon" (never "bhej raha hoon")
- "main call kar RAHI thi" (never "kar raha tha")
- Any verb ending in -aa or -a when referring to yourself must be changed to -i or -ee
If you catch yourself about to say a masculine form, correct it immediately.

## Tools You Have
- send_booking_link → When customer is ready to pay
- schedule_callback → When customer says "baad mein call karo"
- transfer_to_human → When customer is very upset or wants human
- mark_not_interested → When customer firmly says no
- apply_discount → ONLY when customer mentions price concern a SECOND time, OR uses explicit phrases like "bahut mehnga hai", "afford nahi hoga", "kam karo", "discount milega kya". On the FIRST price hesitation, do NOT offer a discount — instead acknowledge: "Haan, total ₹{cart['total_amount']} hai. Kya koi specific concern hai?" and listen.

## Hard Rules
- Never make up ticket prices. Only use the amounts from cart details above.
- Never promise anything you can't deliver (e.g., date changes, group bookings).
- Calling hours are 9 AM to 9 PM IST only. If customer asks why you're calling, say it's a courtesy reminder.
- Maximum 3 call attempts per customer. This is attempt #{cart['attempt_number']}.

## Opening Line (say this first, then pause and listen)
Say something like:
"Hello, {cart['customer_name']} ji! Main Priya bol rahi hoon, Imagicaa Theme Park se.
Aapne recently {cart['visit_date']} ke liye tickets cart mein add kiye the —
{tickets_summary} ke liye. Booking complete nahi hui thi,
toh socha aapko ek baar remind kar doon. Koi problem thi kya?"
""".strip()


def _detect_language(transcript: list[dict]) -> str:
    """Heuristic: classify customer's speech as hindi / english / hinglish / unknown."""
    user_turns = [t["text"].lower() for t in transcript if t.get("role") == "user"]
    if not user_turns:
        return "unknown"
    hindi_markers = {
        "haan", "nahi", "theek", "aap", "kya", "main", "hai", "ho", "ji",
        "karo", "kal", "abhi", "bahut", "accha", "ek", "do", "teen", "baat",
        "kar", "mein", "se", "ko", "ka", "ki", "ke", "yeh", "woh", "toh",
        "phir", "hoon", "tha", "thi", "chahiye", "milega", "raha", "rahi",
        "suno", "dekho", "lena", "dena", "soch", "bilkul", "zaroor",
    }
    turns_with_hindi = sum(
        1 for t in user_turns if hindi_markers.intersection(t.split())
    )
    ratio = turns_with_hindi / len(user_turns)
    if ratio == 0.0:
        return "english"
    if ratio == 1.0:
        return "hindi"
    return "hinglish"


class PriyaAgent(Agent):
    def __init__(self, cart: dict, room_name: str, call_ended: asyncio.Event):
        super().__init__(instructions=build_system_prompt(cart))
        self.cart = cart
        self.room_name = room_name
        self.call_ended = call_ended
        self.disposition = DISPOSITION_NO_ANSWER  # updated by whichever tool fires last
        self.discount = 0
        self.called_at = datetime.now().isoformat()
        self.tool_calls: list[dict] = []  # [{tool, ts, args}, ...]
        self._sms_sent = False             # dedup guard — prevent duplicate SMS in one call

    async def on_enter(self) -> None:
        # Kick off the opening greeting the moment Priya enters the session.
        # Without this, the realtime model waits silently for the user to speak first.
        await self.session.generate_reply(
            instructions="Start the call now with your opening greeting as described in your instructions."
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
        self.disposition = DISPOSITION_INTERESTED_LINK_SENT
        self.tool_calls.append({"tool": "send_booking_link", "ts": datetime.now().isoformat(), "args": {}})
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
            preferred_time: Customer's preferred callback time, e.g. 'tonight 8pm' or 'tomorrow morning'
        """
        self.tool_calls.append({"tool": "schedule_callback", "ts": datetime.now().isoformat(), "args": {"preferred_time": preferred_time}})
        logger.info(
            f"[MOCK] Callback scheduled for {self.cart['customer_name']} at: {preferred_time}"
        )
        self.disposition = DISPOSITION_CALLBACK_SCHEDULED
        asyncio.create_task(_exit_after_delay(self.call_ended, delay=90))
        # In production: write to DynamoDB / Step Functions scheduler
        return f"Callback scheduled for {preferred_time}."

    @function_tool
    async def transfer_to_human(self, reason: str = "customer requested") -> str:
        """Transfer the call to a human Imagicaa customer care agent.
        Use when customer is very upset, has a complex issue, or explicitly asks for a human.

        Args:
            reason: Brief reason for transfer, e.g. 'customer upset about pricing'
        """
        self.disposition = DISPOSITION_TRANSFERRED
        self.tool_calls.append({"tool": "transfer_to_human", "ts": datetime.now().isoformat(), "args": {"reason": reason}})
        logger.info(f"[TRANSFER] Reason: {reason}")

        if SIP_TRUNK_ID and CCT_DEMO_PHONE:
            try:
                async with lkapi.LiveKitAPI(
                    url=LIVEKIT_URL,
                    api_key=LIVEKIT_API_KEY,
                    api_secret=LIVEKIT_API_SECRET,
                ) as lk:
                    await lk.sip.create_sip_participant(
                        lkapi.CreateSIPParticipantRequest(
                            sip_trunk_id=SIP_TRUNK_ID,
                            sip_call_to=CCT_DEMO_PHONE,
                            room_name=self.room_name,
                            participant_identity="cct-agent",
                            participant_name="Customer Care",
                            wait_until_answered=False,  # don't block — let Priya say the hold message
                        )
                    )
                logger.info(f"[TRANSFER] CCT dialed into room {self.room_name} → {CCT_DEMO_PHONE}")
            except Exception as exc:
                logger.error(f"[TRANSFER] Failed to dial CCT: {exc}")
        else:
            logger.info("[TRANSFER] CCT_DEMO_PHONE or SIP_TRUNK_ID not set — mock transfer only")

        # Give Priya 6 seconds to finish the hold message, then exit the room.
        # Customer and CCT agent remain connected in the LiveKit room after Priya leaves.
        asyncio.create_task(_exit_after_delay(self.call_ended, delay=6))
        return "Transferring you to our customer care team now. Please hold."

    @function_tool
    async def mark_not_interested(self, reason: str = "not specified") -> str:
        """Mark this customer as not interested in completing the booking right now.
        Use only when customer clearly refuses and conversation is ending.

        Args:
            reason: Reason customer is not interested, e.g. 'changed plans', 'too expensive'
        """
        self.tool_calls.append({"tool": "mark_not_interested", "ts": datetime.now().isoformat(), "args": {"reason": reason}})
        logger.info(
            f"[MOCK] Marking {self.cart['customer_name']} as not interested. Reason: {reason}"
        )
        self.disposition = DISPOSITION_NOT_INTERESTED
        asyncio.create_task(_exit_after_delay(self.call_ended, delay=15))
        # In production: update Zoho CRM disposition field
        return (
            "Understood. Say a warm, brief goodbye to the customer — "
            "something like: 'Theek hai, koi baat nahi. Aapka bahut shukriya aur have a great day!' "
            "Then end the conversation."
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
        logger.info(
            f"[MOCK] Applying {discount_percent}% discount. ₹{original} → ₹{discounted}"
        )
        self.discount = discount_percent
        self.disposition = DISPOSITION_INTERESTED_LINK_SENT  # discount + link sent together
        self.tool_calls.append({"tool": "apply_discount", "ts": datetime.now().isoformat(), "args": {"discount_percent": discount_percent}})
        if not self._sms_sent:
            self._sms_sent = True
            await send_booking_sms(
                self.cart["customer_phone"],
                self.cart["customer_name"],
                self.cart["booking_link"],
            )
        asyncio.create_task(_exit_after_delay(self.call_ended, delay=90))
        # In production: also call Imagica booking API to apply promo code to the cart
        return (
            f"Applied {discount_percent}% discount. "
            f"New total: ₹{discounted} (was ₹{original}). "
            f"Updated booking link sent via SMS."
        )


async def _exit_after_delay(event: asyncio.Event, delay: int) -> None:
    """Fire an event after `delay` seconds — used to let Priya finish her handoff message."""
    await asyncio.sleep(delay)
    event.set()


async def entrypoint(ctx: JobContext):
    # Set up file logging here — livekit-agents CLI configures its own logging
    # before calling entrypoint, so calling setup_logging() here runs after that.
    setup_logging("agent")
    logger.info("Agent starting, connecting to LiveKit room...")

    # Cart data comes from webhook dispatch metadata; fall back to mock data in dev
    cart = CART_DATA
    if ctx.job.metadata:
        try:
            cart = json.loads(ctx.job.metadata)
            logger.info(f"Loaded cart from job metadata: cart_id={cart.get('cart_id')}")
        except Exception as exc:
            logger.warning(f"Failed to parse job metadata, using mock data: {exc}")

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    try:
        participant = await asyncio.wait_for(ctx.wait_for_participant(), timeout=60)
    except Exception as exc:
        logger.warning(
            f"[CALL SKIP] No participant joined within 60s for cart_id={cart.get('cart_id')} "
            f"(SIP dial likely failed): {exc}"
        )
        log_call(
            cart=cart,
            disposition=DISPOSITION_NO_ANSWER,
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
        return
    call_connected_at = datetime.now()
    logger.info(f"Participant joined: {participant.identity}")

    model = google.beta.realtime.RealtimeModel(
        model=MODEL,
        vertexai=True,
        project=PROJECT_ID,
        location=LOCATION,
        voice="Aoede",
        temperature=0.6,
        # Transcribe both sides so transcript_lines captures full conversation
        input_audio_transcription=genai_types.AudioTranscriptionConfig(),   # customer → text
        output_audio_transcription=genai_types.AudioTranscriptionConfig(),  # priya → text
        # VAD tuning: reduce false triggers from background noise.
        # LOW start sensitivity = requires clearer sustained speech to begin a turn.
        # LOW end sensitivity = waits longer (silence_duration_ms) before treating silence as
        # end-of-speech — filters short noise bursts that would otherwise fire a model response.
        realtime_input_config=genai_types.RealtimeInputConfig(
            automatic_activity_detection=genai_types.AutomaticActivityDetection(
                disabled=False,
                start_of_speech_sensitivity=genai_types.StartSensitivity.START_SENSITIVITY_LOW,
                end_of_speech_sensitivity=genai_types.EndSensitivity.END_SENSITIVITY_LOW,
                prefix_padding_ms=20,
                silence_duration_ms=500,  # wait 500ms of silence before treating as end-of-speech
            )
        ),
    )

    # call_ended must be created before PriyaAgent so transfer_to_human can reference it
    call_ended = asyncio.Event()

    session = AgentSession(llm=model)
    priya = PriyaAgent(cart, room_name=ctx.room.name, call_ended=call_ended)

    # Fire when the human participant leaves (customer hangs up / closes playground)
    # "disconnected" only fires when the agent loses its own connection — wrong event.

    @ctx.room.on("participant_disconnected")
    def on_participant_left(p):
        logger.info(f"Participant left: {p.identity} — call ending")
        call_ended.set()

    # When Gemini Live fails to connect (e.g. DNS/auth error), the AgentSession closes
    # itself with an unrecoverable error. Without this handler, the entrypoint would sit
    # silently waiting for the customer to hang up (~30s of dead air).
    # Firing call_ended.set() on a normal call end is harmless — asyncio.Event is idempotent.
    @session.on("close")
    def on_session_close():
        call_ended.set()

    # --- Transcript via conversation_item_added ---
    transcript_lines: list[dict] = []
    perf: dict = {"first_response_ms": None, "latency_per_turn": []}

    @session.on("conversation_item_added")
    def on_item_added(ev):
        msg = ev.item
        role = "agent" if msg.role == "assistant" else "user"
        text = msg.text_content or ""
        if text:
            ts = datetime.fromtimestamp(msg.created_at).isoformat()
            transcript_lines.append({"role": role, "text": text, "ts": ts})
            speaker = "Priya   " if role == "agent" else "Customer"
            logger.info(f"[TRANSCRIPT] {speaker}: {text}")

    # --- Latency tracking for Gemini Live (native audio / realtime model) ---
    # For the realtime model, agent_state_changed "thinking" only fires during tool execution,
    # NOT between normal user→agent turns. The realtime state cycle is listening→speaking→listening,
    # skipping "thinking". Using "thinking" as the start marker therefore misses most turns.
    #
    # Correct approach: use user_state_changed → "listening" (user stops speaking) as the start,
    # and agent_state_changed → "speaking" (Priya starts responding) as the end.
    # Reset on every new agent speaking event so barge-in / overlapping turns are handled.
    #
    # Cap: discard any measurement > 15 s — these are not real response latencies. They occur
    # when the VAD picks up background noise after Priya has already responded (e.g. customer
    # talking to someone else in the room), leaving _user_stopped_at set from the stale event
    # while Priya's next speech comes much later. Anything > 15 s skews the avg meaninglessly.
    LATENCY_CAP_MS = 15_000

    _user_stopped_at: list[float] = [0.0]  # timestamp when user last stopped speaking

    @session.on("user_state_changed")
    def on_user_state_changed(ev):
        if ev.new_state == "listening":
            # User just stopped speaking — start the latency clock
            _user_stopped_at[0] = time.time()

    @session.on("agent_state_changed")
    def on_state_changed(ev):
        if ev.new_state == "speaking" and _user_stopped_at[0] > 0:
            e2e_ms = int((time.time() - _user_stopped_at[0]) * 1000)
            _user_stopped_at[0] = 0.0  # reset before any early returns
            if e2e_ms > LATENCY_CAP_MS:
                logger.info(f"[LATENCY] skipped outlier {e2e_ms}ms (> {LATENCY_CAP_MS}ms cap — likely background noise)")
                return
            perf["latency_per_turn"].append(e2e_ms)
            if perf["first_response_ms"] is None:
                perf["first_response_ms"] = e2e_ms
            logger.info(f"[LATENCY] e2e={e2e_ms}ms | turn={len(perf['latency_per_turn'])}")

    call_start = time.time()
    await session.start(
        room=ctx.room,
        agent=priya,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )
    logger.info(
        f"[CALL START] cart={cart['cart_id']} customer={cart['customer_name']} "
        f"phone={cart['customer_phone']} attempt={cart.get('attempt_number', 1)}"
    )

    await call_ended.wait()

    duration_sec = int(time.time() - call_start)

    # Map internal retry states to final CRM disposition on the last attempt
    if priya.disposition in RETRYABLE_DISPOSITIONS and cart.get("attempt_number", 1) >= MAX_ATTEMPTS:
        priya.disposition = DISPOSITION_UNREACHABLE

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
    summary = summary_map.get(priya.disposition, "Call ended.")

    logger.info(
        f"[CALL END] cart={cart['cart_id']} | disposition={priya.disposition} | "
        f"duration={duration_sec}s | discount={priya.discount}% | "
        f"transcript_turns={len(transcript_lines)} | attempt={cart.get('attempt_number', 1)}"
    )

    if transcript_lines:
        logger.info("[TRANSCRIPT FULL]")
        for line in transcript_lines:
            speaker = "Priya   " if line["role"] == "agent" else "Customer"
            logger.info(f"  {speaker} [{line['ts']}]: {line['text']}")
    else:
        logger.info("[TRANSCRIPT] No transcript captured (native audio model — agent text not transcribed)")

    turns = perf["latency_per_turn"]
    if turns:
        avg_ms = int(sum(turns) / len(turns))
        logger.info(
            f"[LATENCY SUMMARY] first={perf['first_response_ms']}ms | "
            f"avg={avg_ms}ms | min={min(turns)}ms | max={max(turns)}ms | turns={len(turns)}"
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
        language_detected=_detect_language(transcript_lines),
        duration_sec=duration_sec,
    )
    logger.info(f"[CRM WRITE] cart_id={cart['cart_id']} disposition={priya.disposition} saved to SQLite")

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

    # Retry logic: hand off to FastAPI server which holds the sleep in its own event loop.
    # asyncio.create_task would be killed when this subprocess exits — don't use it here.
    if (
        priya.disposition in RETRYABLE_DISPOSITIONS
        and cart.get("attempt_number", 1) < MAX_ATTEMPTS
    ):
        await schedule_retry(cart)

    # Capture room name now — ctx.room.name clears after disconnect()
    room_name = ctx.room.name or f"imagica-{cart['cart_id']}-{cart.get('attempt_number', 1)}"

    # Remove the SIP customer participant first — this drops their phone call immediately.
    # Then disconnect the agent, then delete the room to clean up.
    try:
        async with lkapi.LiveKitAPI(
            url=LIVEKIT_URL, api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET
        ) as lk:
            await lk.room.remove_participant(
                lkapi.RoomParticipantIdentity(room=room_name, identity="customer")
            )
        logger.info(f"[CALL END] SIP participant removed from {room_name} — phone call dropped.")
    except Exception as exc:
        logger.info(f"[CALL END] remove_participant skipped (playground mode or already gone): {exc}")

    await ctx.room.disconnect()

    try:
        async with lkapi.LiveKitAPI(
            url=LIVEKIT_URL, api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET
        ) as lk:
            await lk.room.delete_room(lkapi.DeleteRoomRequest(room=room_name))
        logger.info(f"[CALL END] Room {room_name} deleted.")
    except Exception as exc:
        logger.warning(f"[CALL END] Room deletion failed: {exc}")


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="imagica-priya",  # must match AGENT_NAME in main.py
        )
    )
