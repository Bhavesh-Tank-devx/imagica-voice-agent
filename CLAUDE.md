# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

An outbound voice AI ("Priya") that calls customers who abandoned a booking cart
(Imagicaa Theme Park) or filled a lead form (Kaya Clinic), speaking in Hinglish
(Hindi + English mix). Two voice stacks exist:

- **Live (production): ElevenLabs Conversational AI + Twilio.** A FastAPI server
  receives webhooks, dials via Twilio, and bridges audio to ElevenLabs over a
  WebSocket. This is the path under active development.
- **Legacy: LiveKit + Google Gemini Live (Vertex AI).** The original realtime
  agent worker, kept for the LiveKit deployment.

All application code lives under `src/`, organised by domain.

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file. Live (ElevenLabs + Twilio) path:
```
BASE_URL=https://<your-tunnel>.ngrok-free.dev
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_FROM_NUMBER=+1...
ELEVENLABS_API_KEY=...
ELEVENLABS_AGENT_ID=...
ELEVENLABS_KAYA_AGENT_ID=...        # optional; falls back to ELEVENLABS_AGENT_ID
```

Legacy LiveKit + Gemini worker also needs:
```
LIVEKIT_URL=wss://...
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
GOOGLE_CLOUD_PROJECT=...
GOOGLE_CLOUD_LOCATION=us-central1
GEMINI_MODEL=gemini-live-2.5-flash-native-audio
```
For the worker, authenticate with Google Cloud: `gcloud auth application-default login`.

Configuration is centralised in `src/config.py` (pydantic-settings); never read
`os.getenv` ad hoc — add a field to the relevant settings class instead.

## Running

**Live webhook server + dashboard** (port 8000):
```bash
uvicorn src.main:app --host 0.0.0.0 --port 8000
```

**Legacy LiveKit + Gemini worker** (connects to LiveKit, waits for rooms):
```bash
python -m src.worker.livekit_agent dev
```

**Alternative ElevenLabs-native outbound server** (port 8001):
```bash
uvicorn src.elevenlabs_native.app:app --host 0.0.0.0 --port 8001
```

**Test Gemini Live connectivity** (saves audio to `output.wav`):
```bash
python scripts/test_gemini_live.py && afplay output.wav   # macOS
```

Lint/format: `ruff check src` (config in `pyproject.toml`).

## Architecture

Top-level layout: `src/` (app code), `tests/` (pytest), `scripts/` (ops &
connectivity scripts), `web/` (dashboard HTML served by `src/dashboard`), `docs/`
(guides, plans, `system_prompt.md`, and `docs/knowledge_base/` Kaya KB text),
`benchmark/` (metric harness), `custom_LLM/` (standalone Claude↔ElevenLabs proxy).
`README.md` and `CLAUDE.md` stay at the root.

Live data flow: webhook → priority queue (SQLite) → queue worker → Twilio dial →
`/twilio/answer` (TwiML) → `/twilio/media-stream` WebSocket → ElevenLabs bridge →
tool calls → post-call logging.

`src/` layout:

- **`main.py`** — App factory (`create_app`/`app`) and lifespan (logging, DB,
  background queue worker). `uvicorn src.main:app`.
- **`config.py`** — pydantic-settings grouped by domain (`app_settings`,
  `twilio_settings`, `elevenlabs_settings`, `sms_settings`, `livekit_settings`).
- **`constants.py`** — `Disposition` and `AgentType` enums; `DISPOSITION_*`
  aliases and per-disposition summaries. Disposition values are the persisted
  CRM contract — do not change the strings.
- **`telephony/`** — `client.py` (Twilio dial), `audio.py` (mu-law↔PCM),
  `bridge.py` (the WebSocket bridge + tool dispatch in `_TOOL_HANDLERS` +
  post-call), `router.py` (`/twilio/*`), `sessions.py` (in-memory cart/call maps).
- **`webhooks/`** — `router.py` (`/webhook/cart-abandoned`, `/webhook/kaya-lead`,
  `/webhook/call-ended`, `/internal/schedule-retry`), `schemas.py`,
  `service.py` (DND + calling-hours gating + enqueue).
- **`conversation/`** — `imagica_prompt.py`, `kaya_prompt.py`, `kaya_branches.py`,
  `language.py`, `email_cleanup.py`.
- **`persistence/`** — SQLite CRM split into `db.py` (connection + schema +
  migrations), `calls.py`, `queue.py`, `sessions.py`, `kaya.py`. DB file is
  `data/post_call.db`.
- **`sms/`** — `send_booking_sms` with MSG91 → Twilio → mock fallback.
- **`retry/`** — retry policy (`RETRYABLE_DISPOSITIONS`, `MAX_ATTEMPTS`, handoff).
- **`dashboard/`** — HTML pages + `/metrics`, `/calls`, `/api/kaya/*`.
- **`queue_worker.py`** — background dispatch loop + retry loopback.
- **`worker/livekit_agent.py`** — legacy `PriyaAgent` + LiveKit `entrypoint`.
- **`elevenlabs_native/`** — alternative ElevenLabs-hosted outbound server.
- **`mock_data.py`** — fallback `CART_DATA` for the worker in local dev.

The five booking tools (`send_booking_link`, `schedule_callback`,
`transfer_to_human`, `mark_not_interested`, `apply_discount`) exist in two places
that must stay behaviourally aligned: the bridge's `_TOOL_HANDLERS`
(`telephony/bridge.py`) for the live path, and `PriyaAgent`'s `@function_tool`
methods (`worker/livekit_agent.py`) for the LiveKit path. Kaya adds
`get_closest_branches`, `book_appointment`, and `end_call`.

## Key Design Notes

- Cart data is injected into the system prompt at call start — the agent does not
  look up customer data mid-call.
- `apply_discount` clamps the discount to 5–10%, never exceeding 10%.
- Maximum 3 call attempts per customer (`MAX_ATTEMPTS`); `attempt_number` rides on
  the cart and is referenced in the prompt. Retries only fire for `NO_ANSWER` /
  `BUSY` and only when the call was not actually answered.
- LiveKit worker voice is `"Aoede"` (`Puck`/`Aoede` sound most natural for Hinglish).
- The `benchmark/` package imports the shared production logic (`src.telephony`,
  `src.conversation`, `src.persistence`) and isolates its data via
  `use_benchmark_db()`, which repoints `src.persistence.db.DB_PATH`.
