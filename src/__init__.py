"""Imagica / Kaya voice-agent backend.

A LiveKit- and ElevenLabs-based outbound voice AI ("Priya") that calls
customers who abandoned a booking cart (Imagicaa Theme Park) or filled a
lead form (Kaya Clinic), speaking in Hinglish over a phone call.

The package is organised into domain modules:

- ``config``        — application settings (pydantic-settings).
- ``constants``     — cross-cutting enums (dispositions, agent types).
- ``persistence``   — SQLite "CRM": call logs, queue, sessions, bookings.
- ``conversation``  — system prompts, language detection, email cleanup.
- ``telephony``     — Twilio dialing and the Twilio<->ElevenLabs media bridge.
- ``sms``           — multi-provider booking-link SMS delivery.
- ``retry``         — retry policy for unanswered / busy calls.
- ``webhooks``      — inbound HTTP webhook routers (live server).
- ``dashboard``     — dashboard pages and observability endpoints.
- ``main``          — FastAPI app factory for the live webhook server.
"""

__version__ = "1.0.0"
