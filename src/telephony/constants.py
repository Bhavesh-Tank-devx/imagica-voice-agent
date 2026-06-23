"""Constants for the ElevenLabs Conversational AI bridge."""

ELEVENLABS_WSS_URL = "wss://api.elevenlabs.io/v1/convai/conversation"

# Discard end-to-end latency measurements above this — they are not real
# response latencies (usually VAD picking up background noise).
LATENCY_CAP_MS = 15_000

# A call running longer than this with no tool outcome counts as "reached".
ANSWERED_THRESHOLD_SEC = 60

# ASR keyword boosting biases speech-to-text toward these proper nouns (the
# most mis-transcribed words on Hinglish calls). Enable only after switching on
# the dashboard "asr.keywords" override, or session init is rejected with 1008.
# Keep the list to proper nouns only — biasing common words is low-value.
KAYA_ASR_KEYWORDS: list[str] = [
    "Kaya", "Kaya Clinic", "Priya",
    "Vesu", "Ghod Dod Road", "Surat", "Vashi", "Bandra", "Andheri",
    "Juhu", "Koramangala", "Indiranagar", "Bengaluru",
]
