"""HMAC signature verification for ElevenLabs webhooks."""
import hashlib
import hmac
import logging
import time

from src.config import elevenlabs_settings

logger = logging.getLogger("imagica-elevenlabs")

# Reject webhook requests whose signed timestamp is older than this (replay guard).
_MAX_SIGNATURE_AGE_SEC = 300


def verify_elevenlabs_signature(raw_body: bytes, signature_header: str) -> bool:
    """Verify an ElevenLabs webhook HMAC-SHA256 signature.

    Header format: ``t=<unix_timestamp>,v0=<hex_digest>``; the signed payload is
    ``<timestamp>.<raw_body>``. Requests older than five minutes are rejected.
    If no secret is configured, verification is skipped (dev only).
    """
    if not elevenlabs_settings.ELEVENLABS_WEBHOOK_SECRET:
        return True

    try:
        parts = dict(p.split("=", 1) for p in signature_header.split(","))
        timestamp = parts["t"]
        expected_sig = parts["v0"]
    except (KeyError, ValueError):
        return False

    if abs(time.time() - int(timestamp)) > _MAX_SIGNATURE_AGE_SEC:
        return False

    signed_payload = f"{timestamp}.".encode() + raw_body
    computed = hmac.new(
        elevenlabs_settings.ELEVENLABS_WEBHOOK_SECRET.encode(),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(computed, expected_sig)
