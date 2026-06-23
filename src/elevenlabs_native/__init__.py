"""ElevenLabs-native outbound server (alternative to the Twilio bridge).

Triggers ElevenLabs hosted outbound calls, receives tool callbacks and the
post-call webhook, and logs to the shared SQLite CRM. Run with
``uvicorn src.elevenlabs_native.app:app --port 8001``.
"""
from src.elevenlabs_native.app import app

__all__ = ["app"]
