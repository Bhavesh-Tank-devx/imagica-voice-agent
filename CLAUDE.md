# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A LiveKit-based voice AI agent ("Priya") for Imagicaa Theme Park. Priya calls customers who abandoned their booking cart and speaks in Hinglish (Hindi + English mix). The agent uses Google Gemini Live via Vertex AI for real-time bidirectional audio.

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file with:
```
LIVEKIT_URL=wss://...
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
GOOGLE_CLOUD_PROJECT=...
GOOGLE_CLOUD_LOCATION=us-central1
GEMINI_MODEL=gemini-live-2.5-flash-native-audio
```

Authenticate with Google Cloud: `gcloud auth application-default login`

## Running

**Run the agent worker** (connects to LiveKit, waits for rooms):
```bash
python agent.py dev
```

**Test Gemini Live API connectivity** (sends a test prompt, saves audio to `output.wav`):
```bash
python test_gemini_live.py
afplay output.wav  # listen to response on macOS
```

## Architecture

The data flow is: LiveKit room (phone call) -> `MultimodalAgent` -> Gemini Live API (bidirectional audio) -> function tools in `ImagicaFunctions`.

- **`agent.py`** — Entrypoint. `build_system_prompt()` injects cart data into Priya's persona/instructions. `entrypoint()` connects to the LiveKit room, waits for a participant, then starts the `MultimodalAgent` with the Gemini Live model and function context.
- **`functions.py`** — Defines `ImagicaFunctions(llm.FunctionContext)` with 5 AI-callable tools: `send_booking_link`, `schedule_callback`, `transfer_to_human`, `mark_not_interested`, `apply_discount`. All are currently mocked (log + return string). Production integrations are noted in comments (SMS API, CRM, SIP transfer, etc.).
- **`mock_data.py`** — Hardcoded `CART_DATA` dict that simulates a webhook payload from the Imagica booking engine. In production this would be replaced by a live webhook from the booking system.
- **`post_call.py`** — Placeholder for post-call processing logic (currently empty).

## Key Design Notes

- Cart data is injected into the system prompt at call start — the agent does not look up customer data mid-call.
- `apply_discount` clamps discount to 5–10% range, never exceeding 10%.
- Maximum 3 call attempts per customer; `attempt_number` is tracked in cart data and referenced in the prompt.
- Voice is set to `"Aoede"` — per code comments, `Puck` or `Aoede` sound most natural for Hinglish.
