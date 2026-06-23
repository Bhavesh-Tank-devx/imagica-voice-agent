# Imagicaa Voice Agent — "Priya"

An AI outbound voice agent that calls customers who abandoned their booking cart at Imagicaa Theme Park. Priya speaks in Hinglish (Hindi + English mix), understands natural conversation, and can apply discounts, send booking links, and schedule callbacks — all in real time over a phone call.

---

## How Many Terminals You Need

The live system (ElevenLabs + Twilio) runs as **one server process** plus a tunnel.
The Twilio ↔ ElevenLabs audio bridge runs inside the server — there is no separate
agent worker on the live path. The LiveKit/Gemini worker is legacy (see below).

| Terminal | Command                                            | What it does                                   |
| -------- | -------------------------------------------------- | ---------------------------------------------- |
| **1**    | `uvicorn src.main:app --host 0.0.0.0 --port 8000`  | FastAPI webhook server + dashboard on port 8000 |
| **2**    | `ngrok http --url=...ngrok-free.dev 8000`          | Public tunnel for Twilio/ElevenLabs callbacks   |

---

## Quick Start (after IDE crash / fresh session)

### Terminal 1 — Webhook Server + Dashboard

```bash
cd ~/imagica-voice-agent
source venv/bin/activate
uvicorn src.main:app --host 0.0.0.0 --port 8000
```

### Terminal 2 - ngrok

ngrok http --url=redressable-spectrochemical-aarav.ngrok-free.dev 8000

You should see:

```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Imagica webhook server started
```

Then open **http://localhost:8000** — the dev dashboard loads with a booking form.

---

## Codebase Layout

All application code lives under `src/`, split by domain:

```
src/
  main.py              app factory for the live webhook server (uvicorn src.main:app)
  config.py            pydantic-settings (Twilio, ElevenLabs, calling hours, ...)
  constants.py         Disposition / AgentType enums
  telephony/           Twilio dialing + Twilio↔ElevenLabs media bridge
  webhooks/            cart-abandoned / kaya-lead / call-ended routers
  conversation/        prompts, language detection, email cleanup, branch lookup
  persistence/         SQLite "CRM": call logs, queue, sessions, Kaya bookings
  sms/                 multi-provider booking-link SMS
  retry/               retry policy for unanswered / busy calls
  dashboard/           dashboard pages + observability endpoints
  worker/              legacy LiveKit + Gemini realtime agent worker
  elevenlabs_native/   alternative ElevenLabs-hosted outbound server
```

### Other entrypoints

```bash
# Legacy LiveKit + Gemini realtime worker (only for the LiveKit deployment)
python -m src.worker.livekit_agent dev

# Alternative ElevenLabs-native outbound server (port 8001)
uvicorn src.elevenlabs_native.app:app --host 0.0.0.0 --port 8001
```

---

## Dev Dashboard (no curl needed)

Open **http://localhost:8000** and use the form to dispatch Priya:

- Fill in customer name, phone, visit date, ticket types + quantities
- **Cart ID is auto-generated** from the customer name — nothing to type
- Toggle **Browser** (join from your browser) or **Phone** (SIP dial, requires `LIVEKIT_SIP_TRUNK_ID`)
- Click **Dispatch Priya** — the response shows the room name and a one-click **Join** button
- Call history at the bottom auto-updates on each dispatch

---

## What It Does

1. The dashboard (or booking engine) sends a `POST /webhook/cart-abandoned` with cart details.
2. A LiveKit room is created and the Priya agent is dispatched into it.
3. In **browser mode**: you join the room via the dashboard's Join button (opens LiveKit Playground).
   In **phone mode**: an outbound SIP call is placed to the customer's phone via Twilio.
4. When the participant joins, Priya greets them, understands their concern, and tries to complete the sale.
5. If unanswered or busy, the system retries up to 3 times with a configurable delay.
6. Every call outcome is logged to SQLite (dispositioned for Zoho CRM in production).

---

## Architecture

```
Dashboard / Booking Engine
     │
     │  POST /webhook/cart-abandoned
     ▼
FastAPI (main.py)  ←──── GET / (dashboard.html)
     │                   GET /token?room=... (LiveKit JWT)
     ├── Creates LiveKit room
     ├── Dispatches agent job (cart JSON as metadata)
     └── [phone mode only] Fires outbound SIP call via Twilio trunk
              │
              ▼
     LiveKit Cloud Room
              │
     ┌────────┴────────┐
     │                 │
  Agent Worker     Participant
  (agent.py)       (browser or SIP phone)
     │
     ▼
  PriyaAgent (livekit-agents 1.x)
     │
     ▼
  Gemini Live API (Vertex AI)
  Bidirectional real-time audio
     │
     ▼
  Function Tools (in-call actions)
  ├── send_booking_link  — SMS via Twilio/MSG91
  ├── apply_discount     — clamps to 5–10%
  ├── schedule_callback  — mocked
  ├── transfer_to_human  — real SIP if CCT_DEMO_PHONE set
  └── mark_not_interested
     │
     ▼
  post_call.py → SQLite (data/post_call.db)
     │
     ▼
  retry.py → FastAPI /internal/schedule-retry
           → sleeps in uvicorn event loop
           → re-fires webhook for attempt 2/3
```

---

## File Structure

Application code lives in `src/` (see **Codebase Layout** above). Top-level:

```
imagica-voice-agent/
├── src/              — application code, split by domain (see Codebase Layout)
├── tests/            — pytest suite (run: pytest)
├── scripts/          — ops & connectivity scripts (load_test, concurrent_test,
│                       test_gemini_live, test_elevenlabs)
├── web/              — dashboard HTML pages served by src/dashboard
├── docs/             — guides, plans, study, session notes, system_prompt.md
│   └── knowledge_base/ — Kaya Clinic KB text (uploaded to the ElevenLabs dashboard)
├── benchmark/        — multi-stack Class B metric harness
├── custom_LLM/       — standalone Claude↔ElevenLabs proxy server
├── data/             — SQLite databases (post_call.db; benchmark_runs.db is gitignored)
├── requirements.txt  — Python dependencies
├── pyproject.toml    — ruff / black / mypy / pytest config
├── README.md · CLAUDE.md
└── .env              — secrets and config (not committed)
```

---

## Setup (first time only)

### 1. Python environment

```bash
cd ~/imagica-voice-agent
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Google Cloud authentication

```bash
gcloud auth application-default login
```

### 3. `.env` file

Create `.env` in the project root:

```env
# LiveKit Cloud
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_api_key
LIVEKIT_API_SECRET=your_api_secret

# Google Vertex AI (Gemini Live)
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=us-central1
GEMINI_MODEL=gemini-live-2.5-flash-native-audio

# SMS — optional, falls back to mock log if not set
TWILIO_ACCOUNT_SID=ACxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxx
TWILIO_FROM_NUMBER=+13185043576

# SIP phone calls — optional, browser mode works without this
LIVEKIT_SIP_TRUNK_ID=ST_xxxxxxxxxxxxxxxx
CCT_DEMO_PHONE=+91XXXXXXXXXX   # human handoff number for transfer_to_human
```

> **Region note**: `gemini-live-2.5-flash-native-audio` is only available in `us-central1` on most GCP projects. Test with `python scripts/test_gemini_live.py` before changing the region.

---

## API Endpoints

| Method | Path                       | Description                      |
| ------ | -------------------------- | -------------------------------- |
| `GET`  | `/`                        | Dev dashboard (HTML)             |
| `GET`  | `/health`                  | Health check                     |
| `GET`  | `/token?room=<name>`       | Generate LiveKit participant JWT |
| `GET`  | `/metrics`                 | Aggregated call metrics          |
| `GET`  | `/calls`                   | Recent call logs (last 20)       |
| `GET`  | `/calls/{id}`              | Full detail for one call         |
| `POST` | `/webhook/cart-abandoned`  | Dispatch Priya for a cart        |
| `POST` | `/internal/schedule-retry` | Internal retry scheduling        |

---

## Manual curl (alternative to dashboard)

```bash
curl -X POST http://localhost:8000/webhook/cart-abandoned \
  -H "Content-Type: application/json" \
  -d '{
    "customer_name": "Rahul",
    "customer_phone": "+919876543210",
    "visit_date": "5 April 2026",
    "tickets": [{"type": "Adult", "quantity": 2, "price_per_unit": 1299}],
    "total_amount": 2598,
    "attempt_number": 1,
    "mode": "browser"
  }'
```

The response includes `room` name. Then get a token to join:

```bash
curl "http://localhost:8000/token?room=imagica-CART-RAHUL-12345-1"
```

Paste the token into `https://meet.livekit.io/custom?liveKitUrl=YOUR_URL&token=TOKEN`.

---

## Testing Gemini Live connectivity

```bash
source venv/bin/activate
python scripts/test_gemini_live.py
afplay output.wav   # macOS: listen to Priya's response
```

---

## Retry Logic

If a call ends as `NO_ANSWER` or `BUSY` and `attempt_number < 3`:

1. Agent POSTs to `/internal/schedule-retry` immediately after logging.
2. FastAPI receives it and runs `asyncio.create_task(_delayed_retry(cart))`.
3. After `RETRY_DELAY_SECONDS` (30s in dev — set to `7200` for production), re-fires the webhook with `attempt_number` incremented.
4. A new isolated room (`imagica-{cart_id}-{attempt}`) is created for each attempt.

**Why the sleep lives in uvicorn, not the agent subprocess:**
The agent job subprocess exits ~20 seconds after the call ends. Any `asyncio.sleep` inside it gets killed. The uvicorn process stays alive indefinitely, so the delay is reliable.

---

## SIP / Phone Call Setup (Twilio)

Requires `LIVEKIT_SIP_TRUNK_ID` in `.env`. Without it, the system works in browser mode only.

### Create the LiveKit SIP outbound trunk

```bash
lk sip outbound create \
  --url wss://your-project.livekit.cloud \
  --api-key YOUR_KEY \
  --api-secret YOUR_SECRET \
  --name "imagica-twilio-trunk" \
  --address "imagica-trunk.pstn.twilio.com" \
  --username YOUR_TWILIO_CRED_USERNAME \
  --password YOUR_TWILIO_CRED_PASSWORD
```

Copy the `ST_xxx` trunk ID into `.env` as `LIVEKIT_SIP_TRUNK_ID`.

---

## Database

SQLite file: `data/post_call.db` (auto-created on first call)

**Inspect calls:**

```bash
sqlite3 data/post_call.db "SELECT cart_id, customer_name, disposition, attempt_number, duration_seconds, called_at FROM call_logs ORDER BY id DESC LIMIT 20;"
```

**Full transcript for a call:**

```bash
sqlite3 data/post_call.db "SELECT transcript FROM call_logs WHERE id=1;"
```

---

## Dispositions

| Value                  | Meaning                                    | Retries?              |
| ---------------------- | ------------------------------------------ | --------------------- |
| `INTERESTED_LINK_SENT` | Positive signal, booking link sent via SMS | No                    |
| `CALLBACK_SCHEDULED`   | Customer asked to be called later          | No                    |
| `NOT_INTERESTED`       | Customer firmly declined                   | No                    |
| `TRANSFERRED_TO_HUMAN` | Escalated to human agent                   | No                    |
| `NO_ANSWER`            | Call ended with no outcome                 | Yes (up to attempt 3) |
| `BUSY`                 | Customer busy / no pickup                  | Yes (up to attempt 3) |
| `UNREACHABLE`          | Final state after all attempts exhausted   | No                    |

---

## Debugging

Key log prefixes:

| Prefix                   | Meaning                           |
| ------------------------ | --------------------------------- |
| `[CALL START]`           | Priya entered the room            |
| `[TRANSCRIPT] Customer:` | Customer speech (transcribed)     |
| `[TRANSCRIPT] Priya:`    | Priya's speech (transcribed)      |
| `[LATENCY]`              | Per-turn e2e latency in ms        |
| `[CALL END]`             | Duration, disposition, turn count |
| `[CRM WRITE]`            | Record saved to SQLite            |
| `[RETRY]`                | Retry handoff fired               |
| `[SMS/...]`              | SMS send attempt                  |
| `[TRANSFER]`             | Human handoff triggered           |

### Common issues

| Symptom                                   | Cause                    | Fix                               |
| ----------------------------------------- | ------------------------ | --------------------------------- |
| `Failed to resolve oauth2.googleapis.com` | No internet / DNS        | Check network                     |
| `1008 policy violation` on Gemini connect | Model not in that region | Use `us-central1`                 |
| Priya silent after join                   | Gemini failed to connect | Check agent logs for API errors   |
| `NotAllowedError` in browser              | Mic permission denied    | Allow mic in browser prompt       |
| Stale dispatch blocking new calls         | Old dispatch orphaned    | Auto-handled by idempotency guard |

---

## Production Checklist

- [ ] Replace SQLite `log_call()` with Zoho CRM API call in `post_call.py`
- [ ] Set `RETRY_DELAY_SECONDS = 7200` (2 hours) in `retry.py`
- [ ] Replace `HANDOFF_URL = "http://localhost:8000"` with production URL in `retry.py`
- [ ] Set MSG91 env vars for DLT-approved SMS in India
- [ ] Replace DND hardcode in `main.py` with live DND registry API lookup
- [ ] Replace `mock_data.py` fallback with a hard error — no mock data in production
- [ ] Add webhook authentication (HMAC signature from booking engine)
- [ ] Deploy FastAPI behind nginx / Cloud Run with HTTPS
- [ ] Move SQLite to a persistent volume or swap for PostgreSQL

---

## Key Design Notes

- **Cart data is injected at call start** via LiveKit job metadata — Priya does not look up customer data mid-call.
- **`apply_discount` is clamped** to 5–10%, never exceeding 10%.
- **Room name includes attempt number** (`imagica-{cart_id}-{attempt}`) — each attempt gets an isolated room.
- **`on_enter()` makes Priya speak first** — without it, Gemini Live waits silently for the user to speak.
- **The retry sleep lives in uvicorn** — the agent subprocess exits ~20s after a call ends, killing any `asyncio.sleep` inside it.
- **`wait_until_answered=True` in SIP dial** — Priya only speaks after the customer actually picks up.
- **`LIVEKIT_SIP_TRUNK_ID` is optional** — if not set, `dial_customer()` is a no-op; browser mode works unchanged.
