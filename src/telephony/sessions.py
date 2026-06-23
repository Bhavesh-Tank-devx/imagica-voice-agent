"""In-memory session stores linking carts, calls, and WebSocket connections.

Twilio strips query params from the WebSocket upgrade, so the media-stream
handler cannot read ``cart_id`` directly. Instead the dial step records the cart
by ``cart_id``, the ``/twilio/answer`` step maps ``call_sid -> cart_id``, and the
WebSocket handler resolves the cart from the ``call_sid`` in the "start" event.

These dicts are process-local and reset on restart (acceptable for in-flight
calls only; durable state lives in SQLite).
"""

# cart_id -> full cart dict (populated when a call is dispatched).
cart_sessions: dict[str, dict] = {}

# call_sid -> cart_id (populated when Twilio hits /twilio/answer).
call_sessions: dict[str, str] = {}


def register_cart(cart_id: str, cart: dict) -> None:
    """Record a cart before dialing so the WebSocket handler can find it."""
    cart_sessions[cart_id] = cart


def bind_call(call_sid: str, cart_id: str) -> None:
    """Map a Twilio call SID to its cart ID."""
    call_sessions[call_sid] = cart_id


def cart_for_call(call_sid: str) -> dict | None:
    """Resolve the cart dict for a Twilio call SID, or None if not found."""
    cart_id = call_sessions.get(call_sid, "")
    return cart_sessions.get(cart_id)


def end_call(call_sid: str) -> dict | None:
    """Pop the call/cart sessions for a terminated call; return the cart if any."""
    cart_id = call_sessions.pop(call_sid, None)
    if cart_id is None:
        return None
    return cart_sessions.pop(cart_id, None)
