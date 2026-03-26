# Imagicaa Voice Agent — "Priya"

An AI outbound voice agent that calls customers who abandoned their booking cart at Imagicaa Theme Park. Priya speaks in Hinglish (Hindi + English mix), understands natural conversation, and can apply discounts, send booking links, and schedule callbacks — all in real time over a phone call.

---

## What It Does

1. The booking engine sends a `POST /webhook/cart-abandoned` with cart details.
2. A LiveKit room is created and the Priya agent is dispatched into it.
3. An outbound SIP call is placed to the customer's phone via Twilio.
4. When the customer picks up, Priya greets them, understands their concern, and tries to complete the sale.
5. If unanswered or busy, the system retries up to 3 times with a configurable delay.
6. Every call outcome is logged to SQLite (dispositioned for Zoho CRM in production).

---

## Architecture

```
Booking Engine
     │
     │  POST /webhook/cart-abandoned
     ▼
FastAPI (main.py)
     │
     ├── Creates LiveKit room
     ├── Dispatches agent job (cart JSON as metadata)
     └── Fires outbound SIP call via Twilio trunk
              │
              │  Customer's phone rings
              ▼
         Phone Call (SIP)
              │
              ▼
     LiveKit Cloud Room
              │
     ┌────────┴────────┐
     │                 │
  Agent Worker     Customer (SIP)
  (agent.py)
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
  ├── send_booking_link
  ├── apply_discount
  ├── schedule_callback
  ├── transfer_to_human
  └── mark_not_interested
     │
     ▼
  post_call.py → SQLite (post_call.db)
     │
     ▼
  retry.py → FastAPI /internal/schedule-retry
           → sleeps in uvicorn event loop
           → re-fires webhook for attempt 2/3
```

---

## File Structure

```
imagica-voice-agent/
├── agent.py          — Agent entrypoint: PriyaAgent class, system prompt, function tools,
│                       transcript capture, post-call logging, retry handoff
├── main.py           — FastAPI server: webhook handler, room/dispatch creation,
│                       SIP dial, retry scheduling endpoint
├── post_call.py      — SQLite CRM mock: init_db(), log_call(), get_call_logs()
│                       Schema maps 1:1 to Zoho Lead fields for easy production swap
├── retry.py          — Retry constants and schedule_retry() handoff to FastAPI server
├── mock_data.py      — Hardcoded CART_DATA for local dev without a webhook
├── functions.py      — Old-style FunctionContext (unused; tools are in agent.py now)
├── test_gemini_live.py — Standalone connectivity test: sends a prompt, saves output.wav
├── post_call.db      — SQLite database (auto-created on first call)
├── requirements.txt  — Python dependencies
└── .env              — Secrets and config (not committed)
```

---

## Setup

### 1. Python environment

```bash
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

# Twilio SIP trunk (optional — omit for browser/playground testing)
LIVEKIT_SIP_TRUNK_ID=ST_xxxxxxxxxxxxxxxx
```

> **Region note**: `gemini-live-2.5-flash-native-audio` is only available in `us-central1` on most GCP projects. Test with `python test_gemini_live.py` before changing the region.

---

## Running

### Terminal 1 — Agent worker

```bash
python agent.py start
```

Connects to LiveKit Cloud and waits for dispatch jobs. Use `start` (not `dev`) for testing — `dev` mode watches all files and restarts the worker on any save, which can orphan active calls.

### Terminal 2 — FastAPI webhook server

```bash
uvicorn main:app --port 8000 --reload
```

---

## Testing

### Option A — Browser (no phone required)

Fire the webhook, then join the room in your browser using a LiveKit token.

**Step 1 — Fire the webhook:**
```bash
curl -X POST http://localhost:8000/webhook/cart-abandoned \
  -H "Content-Type: application/json" \
  -d '{
    "customer_name": "Rahul",
    "customer_phone": "+919876543210",
    "cart_id": "CART-TEST-001",
    "visit_date": "5 April 2025",
    "tickets": [{"type": "Adult", "quantity": 2, "price_per_unit": 1299}],
    "total_amount": 2598,
    "attempt_number": 1
  }'
```

**Step 2 — Generate a room token** (use the room name from webhook response):
```bash
python -c "
from dotenv import load_dotenv; load_dotenv('.env')
import os
from livekit.api import AccessToken, VideoGrants
token = (AccessToken(os.environ['LIVEKIT_API_KEY'], os.environ['LIVEKIT_API_SECRET'])
    .with_identity('customer')
    .with_name('Rahul')
    .with_grants(VideoGrants(room_join=True, room='imagica-CART-TEST-001-1'))
    .to_jwt())
print(token)
"
```

**Step 3 — Join the room:**

Open in browser (replace `TOKEN` with the output from Step 2):
```
https://meet.livekit.io/#cam=0&mic=1&video=0&audio=1&chat=1&token=TOKEN
```

### Option B — Real phone call (SIP)

Requires `LIVEKIT_SIP_TRUNK_ID` set in `.env`.

```bash
curl -X POST http://localhost:8000/webhook/cart-abandoned \
  -H "Content-Type: application/json" \
  -d '{
    "customer_name": "Rahul",
    "customer_phone": "+91XXXXXXXXXX",
    "cart_id": "CART-SIP-001",
    "visit_date": "5 April 2025",
    "tickets": [{"type": "Adult", "quantity": 2, "price_per_unit": 1299}],
    "total_amount": 2598,
    "attempt_number": 1
  }'
```

Your phone will ring within a few seconds. Pick up — Priya speaks first.

### Test Gemini Live connectivity

```bash
python test_gemini_live.py
afplay output.wav   # macOS: listen to Priya's response
```

---

## Retry Logic

If a call ends as `NO_ANSWER` or `BUSY` and `attempt_number < 3`:

1. Agent subprocess POSTs to `POST /internal/schedule-retry` immediately after logging the call.
2. The FastAPI/uvicorn process receives it and runs `asyncio.create_task(_delayed_retry(cart))`.
3. After `RETRY_DELAY_SECONDS` (default: 30s for dev, set to `7200` for production), it re-fires the webhook with `attempt_number` incremented.
4. A new isolated room (`imagica-{cart_id}-{attempt}`) is created for each attempt.

**Why the sleep lives in uvicorn, not the agent subprocess:**
The agent job subprocess exits ~20 seconds after the call ends. Any `asyncio.sleep` inside it gets killed. The uvicorn process stays alive indefinitely, so the delay is reliable.

**Retry stops when:**
- `attempt_number == 3` (max attempts reached)
- Disposition is `BOOKED`, `CALLBACK`, `NOT_INTERESTED`, or `TRANSFERRED`

---

## SIP / Phone Call Setup (Twilio)

### Prerequisites
- Twilio account with a phone number
- Twilio Elastic SIP Trunk configured
- LiveKit Cloud project (SIP requires Cloud — does not work with local Docker dev server)

### Twilio SIP Trunk config
- **Termination URI**: `imagica-trunk.pstn.twilio.com` (or your chosen subdomain)
- **Credential list**: username + password for LiveKit to authenticate

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

## Dispositions

| Value | Meaning | Triggers retry? |
|---|---|---|
| `BOOKED` | Customer agreed, booking link sent | No |
| `CALLBACK` | Customer asked to be called later | No |
| `NOT_INTERESTED` | Customer firmly declined | No |
| `TRANSFERRED` | Call handed to human agent | No |
| `NO_ANSWER` | Call ended with no outcome | Yes (up to attempt 3) |
| `BUSY` | Customer busy / no pickup | Yes (up to attempt 3) |

---

## Database

SQLite file: `post_call.db` (auto-created on first call)

**Inspect calls:**
```bash
sqlite3 post_call.db "SELECT cart_id, customer_name, disposition, attempt_number, duration_sec, called_at FROM call_logs ORDER BY id DESC LIMIT 20;"
```

**Full transcript for a call:**
```bash
sqlite3 post_call.db "SELECT transcript FROM call_logs WHERE cart_id='CART-001' ORDER BY id DESC LIMIT 1;"
```

**Schema:**
```sql
CREATE TABLE call_logs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    cart_id          TEXT,      -- e.g. CART-2025-001
    customer_name    TEXT,
    customer_phone   TEXT,
    disposition      TEXT,      -- BOOKED / CALLBACK / NOT_INTERESTED / TRANSFERRED / NO_ANSWER
    transcript       TEXT,      -- JSON array of {role, text, ts} objects
    summary          TEXT,      -- one-line outcome
    discount_applied INTEGER,   -- 0, 5, or 10
    attempt_number   INTEGER,
    called_at        TEXT,      -- ISO timestamp call started
    ended_at         TEXT       -- ISO timestamp call ended
);
```

---

## Debugging

### Check logs in real time
Agent worker logs are JSON-structured. Key log prefixes:

| Prefix | What it means |
|---|---|
| `[CALL START]` | Priya entered the room, call beginning |
| `[TRANSCRIPT] Customer:` | Customer said something (transcribed by Gemini) |
| `[TRANSCRIPT] Priya:` | Priya said something |
| `[MOCK]` | A function tool fired (discount, link, callback, etc.) |
| `[CALL END]` | Call finished — shows duration, disposition, transcript turns |
| `[TRANSCRIPT FULL]` | Full conversation printed turn-by-turn |
| `[CRM WRITE]` | Record saved to SQLite |
| `[RETRY]` | Retry handoff fired to FastAPI |

### Common issues

| Symptom | Cause | Fix |
|---|---|---|
| `Failed to resolve oauth2.googleapis.com` | No internet / DNS down | Check network; retry fires automatically |
| `1008 policy violation` on Gemini connect | Model not available in that region | Use `us-central1` |
| Priya silent, customer hears nothing | Gemini failed to connect | Check agent logs for API errors |
| Called back after booking | Priya didn't call `send_booking_link()` before customer hung up | Prompt instructs immediate tool call on any positive signal |
| `NotAllowedError: Permission denied` in browser | Playground URL has `&video=1` | Use `&cam=0&video=0` in URL |
| Stale dispatch blocking new calls | Old dispatch orphaned after worker restart | Idempotency guard deletes stale dispatches automatically |
| Voice cutting / lag on SIP | India → `us-central1` latency (~200ms) | Request regional quota for `asia-southeast1` or `asia-northeast1` on GCP |

---

## Production Checklist

- [ ] Replace SQLite `log_call()` with Zoho CRM API call in `post_call.py`
- [ ] Replace `RETRY_DELAY_SECONDS = 30` with `7200` (2 hours) in `retry.py`
- [ ] Replace `HANDOFF_URL = "http://localhost:8000"` with production URL in `retry.py`
- [ ] Replace mock SMS in `send_booking_link()` with Twilio/MSG91 SMS API call
- [ ] Replace DND hardcode in `main.py` with live DND registry API lookup
- [ ] Replace `mock_data.py` fallback with a real error — no mock data in production
- [ ] Set `GOOGLE_CLOUD_LOCATION` to a low-latency region once quota is approved
- [ ] Add webhook authentication (HMAC signature from booking engine)
- [ ] Deploy FastAPI behind a reverse proxy (nginx / Cloud Run) with HTTPS
- [ ] Move SQLite to a persistent volume or swap for PostgreSQL

---

## Key Design Notes

- **Cart data is injected at call start** via LiveKit job metadata — Priya does not look up customer data mid-call.
- **`apply_discount` is clamped** to 5–10%, never exceeding 10%.
- **Room name includes attempt number** (`imagica-{cart_id}-{attempt}`) — each attempt gets an isolated room so browser playground tabs always show fresh transcripts.
- **`on_enter()` makes Priya speak first** — without it, the Gemini Live model waits silently for the user to speak.
- **The retry sleep lives in uvicorn** — the agent subprocess exits ~20s after a call ends, killing any `asyncio.sleep` inside it. The handoff to `/internal/schedule-retry` ensures the delay runs in uvicorn's long-lived event loop.
- **`wait_until_answered=True` in SIP dial** — Priya only starts speaking after the customer actually picks up, not during ringback.
- **`LIVEKIT_SIP_TRUNK_ID` is optional** — if not set, `dial_customer()` is a no-op and the system works with browser/playground testing unchanged.
