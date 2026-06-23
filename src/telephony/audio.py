"""Audio transcoding between Twilio and ElevenLabs.

Twilio Media Streams send 8 kHz mu-law; ElevenLabs Conversational AI expects
16 kHz 16-bit mono PCM. These helpers convert in both directions.
"""
import audioop

TWILIO_SAMPLE_RATE = 8000
ELEVENLABS_SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2  # bytes per sample (16-bit)


def mulaw_to_pcm16k(mulaw_bytes: bytes) -> bytes:
    """Convert Twilio mu-law 8 kHz audio to PCM 16-bit 16 kHz for ElevenLabs."""
    pcm_8k = audioop.ulaw2lin(mulaw_bytes, SAMPLE_WIDTH)
    pcm_16k, _ = audioop.ratecv(
        pcm_8k, SAMPLE_WIDTH, 1, TWILIO_SAMPLE_RATE, ELEVENLABS_SAMPLE_RATE, None
    )
    return pcm_16k


def pcm16k_to_mulaw(pcm_bytes: bytes) -> bytes:
    """Convert ElevenLabs PCM 16-bit 16 kHz audio to Twilio mu-law 8 kHz."""
    pcm_8k, _ = audioop.ratecv(
        pcm_bytes, SAMPLE_WIDTH, 1, ELEVENLABS_SAMPLE_RATE, TWILIO_SAMPLE_RATE, None
    )
    return audioop.lin2ulaw(pcm_8k, SAMPLE_WIDTH)
