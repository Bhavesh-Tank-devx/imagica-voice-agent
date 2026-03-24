# agent.py
import logging
import os
from dotenv import load_dotenv

from livekit.agents import (
    AgentSession,
    Agent,
    AutoSubscribe,
    JobContext,
    WorkerOptions,
    cli,
    function_tool,
)
from livekit.plugins import google

from mock_data import CART_DATA

load_dotenv()
logger = logging.getLogger("imagica-agent")

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
MODEL = os.getenv("GEMINI_MODEL", "gemini-live-2.5-flash-native-audio")


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
4. **Close** — Either get commitment to book, send link, or schedule follow-up.
5. **Exit gracefully** — If they say no firmly, mark not interested and wish them well. Never be pushy.

## Tone Rules
- Mix Hindi and English naturally: "Arey Rahul bhai, koi baat nahi, main help karti hoon!"
- Use "aap" (respectful) for the customer, never "tum"
- Be warm but not fake. Don't over-apologize.
- Keep sentences short. Real conversations have pauses.
- Never read out URLs — say "main aapko link bhej deti hoon SMS pe"

## Tools You Have
- send_booking_link → When customer is ready to pay
- schedule_callback → When customer says "baad mein call karo"
- transfer_to_human → When customer is very upset or wants human
- mark_not_interested → When customer firmly says no
- apply_discount → When customer says "expensive hai" or hesitates on price

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


class PriyaAgent(Agent):
    def __init__(self, cart: dict):
        super().__init__(instructions=build_system_prompt(cart))
        self.cart = cart

    @function_tool
    async def send_booking_link(self) -> str:
        """Send the booking link to the customer via SMS so they can complete the purchase.
        Triggered when customer agrees to complete the booking."""
        link = self.cart["booking_link"]
        phone = self.cart["customer_phone"]
        logger.info(f"[MOCK] Sending booking link to {phone}: {link}")
        # In production: call SMS API here (Twilio / MSG91)
        return f"Booking link sent to {phone}. Link: {link}"

    @function_tool
    async def schedule_callback(self, preferred_time: str = "not specified") -> str:
        """Schedule a callback at a time the customer prefers.
        Use when customer says they're busy right now or says 'call me later'.

        Args:
            preferred_time: Customer's preferred callback time, e.g. 'tonight 8pm' or 'tomorrow morning'
        """
        logger.info(
            f"[MOCK] Callback scheduled for {self.cart['customer_name']} at: {preferred_time}"
        )
        # In production: write to DynamoDB / Step Functions scheduler
        return f"Callback scheduled for {preferred_time}."

    @function_tool
    async def transfer_to_human(self, reason: str = "customer requested") -> str:
        """Transfer the call to a human Imagicaa customer care agent.
        Use when customer is very upset, has a complex issue, or explicitly asks for a human.

        Args:
            reason: Brief reason for transfer, e.g. 'customer upset about pricing'
        """
        logger.info(f"[MOCK] Transferring call to human. Reason: {reason}")
        # In production: initiate SIP transfer to CCT queue number
        return "Transferring you to our customer care team now. Please hold."

    @function_tool
    async def mark_not_interested(self, reason: str = "not specified") -> str:
        """Mark this customer as not interested in completing the booking right now.
        Use only when customer clearly refuses and conversation is ending.

        Args:
            reason: Reason customer is not interested, e.g. 'changed plans', 'too expensive'
        """
        logger.info(
            f"[MOCK] Marking {self.cart['customer_name']} as not interested. Reason: {reason}"
        )
        # In production: update Zoho CRM disposition field
        return "Noted. No further calls will be made."

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
        # In production: call Imagica booking API to apply promo code
        return (
            f"Applied {discount_percent}% discount. "
            f"New total: ₹{discounted} (was ₹{original}). "
            f"Updated booking link sent via SMS."
        )


async def entrypoint(ctx: JobContext):
    logger.info("Agent starting, connecting to LiveKit room...")

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    participant = await ctx.wait_for_participant()
    logger.info(f"Participant joined: {participant.identity}")

    model = google.beta.realtime.RealtimeModel(
        model=MODEL,
        vertexai=True,
        project=PROJECT_ID,
        location=LOCATION,
        # Voice options: Puck, Charon, Kore, Fenrir, Aoede
        voice="Aoede",
        temperature=0.8,
    )

    session = AgentSession(llm=model)

    await session.start(
        room=ctx.room,
        agent=PriyaAgent(CART_DATA),
    )

    logger.info("Priya is live! Waiting for conversation...")


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
        )
    )
