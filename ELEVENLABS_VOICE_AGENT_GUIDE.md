# ElevenLabs Voice Agent — End-to-End Build Guide

Built from real experience building two production agents:
- **Imagicaa Priya** — outbound cart abandonment recovery (Hinglish, discount + booking link)
- **Kaya Clinic Priya** — outbound/inbound appointment booking (English/Hinglish, 9-step flow)

---

## Table of Contents

1. [How it all fits together](#1-how-it-all-fits-together)
2. [Accounts and services you need](#2-accounts-and-services-you-need)
3. [Step 1 — Create the ElevenLabs agent](#3-step-1--create-the-elevenlabs-agent)
4. [Step 2 — Set up Twilio](#4-step-2--set-up-twilio)
5. [Step 3 — Set up ngrok](#5-step-3--set-up-ngrok)
6. [Step 4 — Local environment](#6-step-4--local-environment)
7. [Step 5 — Write the system prompt](#7-step-5--write-the-system-prompt)
8. [Step 6 — Define client tools](#8-step-6--define-client-tools)
9. [Step 7 — Run the server](#9-step-7--run-the-server)
10. [Step 8 — Test a call](#10-step-8--test-a-call)
11. [Adding a second agent (multi-tenant)](#11-adding-a-second-agent-multi-tenant)
12. [Post-call observability](#12-post-call-observability)
13. [Production checklist](#13-production-checklist)
14. [Common issues and fixes](#14-common-issues-and-fixes)

---

## 1. How it all fits together

```
Your CRM / Webhook                    Your Server (main.py + voice_agent.py)
        │                                        │
        │  POST /webhook/cart-abandoned          │
        │  POST /webhook/kaya-lead               │
        └───────────────────────────────────────►│
                                                 │  enqueue_call() → SQLite queue
                                                 │
                                    Queue worker (10s poll)
                                                 │
                                                 │  twilio_client.calls.create()
                                                 ▼
                                         Twilio Cloud
                                                 │
                                  customer's phone rings
                                                 │  answers
                                                 │
                                   POST /twilio/answer?cart_id=X&agent_type=Y
                                                 │  → returns <Stream> TwiML
                                                 │
                               WS /twilio/media-stream?cart_id=X&agent_type=Y
                                                 │
                            voice_agent.py (WebSocket bridge)
                            ┌────────────────────┴────────────────────┐
                     Twilio µ-law audio                    ElevenLabs WS API
                     (8 kHz, base64)          ◄──────►    wss://api.elevenlabs.io
                                                          (PCM 16 kHz, base64)
                                                                │
                                                         Tool call events
                                                                │
                                                    execute_tool() in voice_agent.py
                                                    (local Python — no HTTP round-trip)
                                                                │
                                                     post_call.py → SQLite
                                                     retry.py → re-queue if NO_ANSWER
```

**Key insight:** Tool calls are handled locally inside the WebSocket bridge process, not via HTTP webhooks to ElevenLabs. This means zero extra latency on tool execution and no public URL needed for tools.

---

## 2. Accounts and services you need

| Service | What for | Cost |
|---|---|---|
| ElevenLabs | AI voice agent, STT, TTS | Free tier available; Starter $5/mo for outbound calling |
| Twilio | Phone number, outbound dial, media stream WebSocket | ~$1/mo per number + $0.014/min calls |
| ngrok | HTTPS tunnel so Twilio can reach your localhost | Free tier (static domain available) |

---

## 3. Step 1 — Create the ElevenLabs agent

### 3.1 Create the agent

1. Go to [elevenlabs.io](https://elevenlabs.io) → **Conversational AI** → **Agents** → **Create Agent**
2. Choose **Blank template** (don't use predefined templates — they add noise)
3. Give it a name (e.g., `Kaya Clinic - Priya`)

### 3.2 Configure the agent settings

Under **Agent** tab:

| Setting | Value | Notes |
|---|---|---|
| First message | Your opening line | For outbound: `"Hello, am I speaking with {{customer_name}}?"` |
| System prompt | Your full prompt | See Section 5 — Write the system prompt |
| Language | English (or auto-detect) | Set to English even for Hinglish — the prompt handles switching |
| Voice | Choose from ElevenLabs library | `Jessica` or `Aria` work well for Indian English; test 3-4 voices |

Under **Advanced** → **Turn detection**:

| Setting | Recommended value |
|---|---|
| Silence threshold | 0.3–0.5 seconds |
| End of speech pause | 600–800ms |
| Interruption threshold | Medium |

> **Lesson from Imagicaa:** Lower silence threshold → faster turns → feels more natural on phone. But too low causes false positives on background noise. 600ms worked best in testing.

### 3.3 Add client tools

ElevenLabs has two types of tools:
- **Client tools** — the agent sends a `client_tool_call` event over the WebSocket and your server handles it. Zero extra latency. **This is what we use.**
- **Server tools** — ElevenLabs POSTs to a URL you provide. Adds ~200-400ms round-trip.

For each tool, go to **Tools** → **Add Tool** → **Client Tool**:

**Imagicaa tools:**
```
send_booking_link   — Sends booking link SMS to the customer
apply_discount      — Applies 5-10% discount and sends updated link
schedule_callback   — Logs preferred callback time
transfer_to_human   — Escalates call to human agent
mark_not_interested — Marks customer as DNC
```

**Kaya tools:**
```
get_closest_branches — Returns branch list for a pincode or city
book_appointment     — Saves confirmed appointment to database
schedule_callback    — Same as Imagicaa
transfer_to_human    — Same as Imagicaa
end_call             — Signals graceful call end
```

For each tool, define the **parameters** the agent should send. Example for `book_appointment`:
```
first_name       (string, required)
last_name        (string, required)
email            (string, required)
dob              (string, optional)
pincode          (string, required)
city             (string, optional)
branch_name      (string, required)
appointment_date (string, required)
appointment_time (string, required)
concern_summary  (string, optional)
```

### 3.4 Note down the Agent ID

Go to **Agent Settings** → copy the **Agent ID** (format: `agent_xxxxxxxxxxxxxxxx`). You'll need this in `.env`.

### 3.5 Configure a phone number (for outbound)

1. Go to **Deploy** → **Phone Numbers** → **Add Phone Number**
2. Choose **Import from Twilio** (recommended) or buy through ElevenLabs
3. Enter your Twilio `ACCOUNT_SID`, `AUTH_TOKEN`, and the phone number
4. Note down the **Phone Number ID** (format: `phnum_xxxx`) — needed if using ElevenLabs' outbound call API

> **Architecture choice:** In this project we use **Twilio directly** (not ElevenLabs' outbound call API) to place calls, because it gives us:
> - AMD (answering machine detection)
> - DND checking before dialing
> - Our own SQLite queue with retry logic
> - Full control over the `<Stream>` URL (we inject `agent_type` into it)

### 3.6 Post-call webhook (optional)

If you want ElevenLabs to POST a transcript summary after each call:
1. **Agent** → **Post-call webhook** → enter `https://your-ngrok-url/webhook/call-ended`
2. Note down the **Webhook Secret** for HMAC signature verification

---

## 4. Step 2 — Set up Twilio

### 4.1 Get your credentials

1. [console.twilio.com](https://console.twilio.com) → **Account Info** → copy `Account SID` and `Auth Token`

### 4.2 Buy or configure a phone number

1. **Phone Numbers** → **Manage** → **Buy a number**
2. Pick a US or India number with **Voice** capability
3. Note the number in E.164 format (e.g., `+13185043576`)

> The same Twilio number works for both Imagicaa and Kaya since the agent is selected after the customer answers, not during dial.

### 4.3 Enable AMD (Answering Machine Detection)

AMD is set in code via `machine_detection="Enable"` in `twilio_client.calls.create()`. No dashboard config needed. When AMD detects voicemail, Twilio POSTs `AnsweredBy=machine_start` to `/twilio/answer` and your server returns a `<Hangup/>` instead of `<Stream/>`.

---

## 5. Step 3 — Set up ngrok

Twilio must reach your local server over HTTPS. ngrok provides the tunnel.

```bash
# Install
brew install ngrok   # macOS

# Auth (one-time)
ngrok config add-authtoken <your-token>

# Start tunnel (keep this running alongside your server)
ngrok http 8000
```

Copy the `https://xxxx.ngrok-free.app` URL. This is your `BASE_URL` in `.env`.

> **Tip:** ngrok free tier gives a random URL that changes every restart. Use a reserved static domain (ngrok paid, ~$8/mo) or the free `ngrok-free.app` subdomain if it stays stable for you.

---

## 6. Step 4 — Local environment

### 6.1 Clone and install

```bash
git clone <repo>
cd imagica-voice-agent
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt`:
```
elevenlabs>=1.0.0
twilio>=9.0.0
websockets>=12.0
python-dotenv>=1.0.0
fastapi>=0.110.0
uvicorn>=0.29.0
httpx>=0.27.0
pytz>=2024.1
```

### 6.2 Create `.env`

```env
# Server
BASE_URL=https://xxxx.ngrok-free.app     # your ngrok URL — no trailing slash

# Twilio
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxx
TWILIO_FROM_NUMBER=+13185043576

# ElevenLabs
ELEVENLABS_API_KEY=sk_xxxxxxxxxxxxxxxxxxxxxxx
ELEVENLABS_AGENT_ID=agent_xxxxxxxxxxxxxxxx         # Imagicaa agent
ELEVENLABS_KAYA_AGENT_ID=agent_yyyyyyyyyyyyyyyy    # Kaya agent
ELEVENLABS_PHONE_NUMBER_ID=phnum_xxxxxxxxxxxx      # only needed for EL outbound API
ELEVENLABS_WEBHOOK_SECRET=wsec_xxxxxxxxxxxxxxxx    # for post-call webhook HMAC

# SMS (optional — for booking link delivery)
MSG91_AUTH_KEY=
MSG91_TEMPLATE_ID=
```

---

## 7. Step 5 — Write the system prompt

This is the most important step. A bad prompt makes a bad agent no matter how good the infrastructure is.

### 7.1 Prompt structure that works

```
## [AGENT IDENTITY]
Who you are, what company, what your goal is.
One paragraph. Be specific about the goal.

## [LANGUAGE RULE]
Default language and switching rules.

## [CONTEXT VARIABLES]
List the runtime variables injected at call start.
E.g.: Customer Name: {{customer_name}}

## [BARGE-IN HANDLING]
Critical for phone UX — tell the agent to stop mid-sentence when interrupted.

## [STYLE GUIDELINES & HARD RULES]
- Max reply length (e.g., MAX 2 sentences)
- What to never say (diagnoses, prices, etc.)
- One question at a time

## [KNOWLEDGE BASE]
Factual information the agent needs (services, branches, fees, etc.)
The agent can't look things up mid-call — everything must be here.

## [CONVERSATION FLOW]
Numbered steps. Linear flow with branches.
Each step: what to say, what to collect, when to call which tool.

## [TOOL USAGE RULES]
Exact triggers for each tool — when to call, when NOT to call.
```

### 7.2 Lessons from Imagicaa (Hinglish outbound)

- **Short replies are critical.** Enforce MAX 2 sentences in the prompt. Agents tend to monologue — be explicit.
- **Language switching:** Specify exact rules. "If customer uses no English for 2+ consecutive turns, switch to pure Hindi. Switch back to Hinglish the moment they mix."
- **Gender:** For a female agent, explicitly list correct verb forms (`bol rahi hoon`, never `bol raha hoon`).
- **Discount triggers:** List exact phrases that trigger the discount offer — don't leave it to inference. "Offer discount if customer says any of: price is high, can't afford, technical issue, plans changed, hesitates twice."
- **Don't front-load:** Tell the agent to build the conversation gradually, one idea per turn. Forbid long opening monologues.

### 7.3 Lessons from Kaya Clinic (multi-step booking)

- **Numbered flow works.** A 9-step numbered conversation flow is very effective. The agent follows it reliably.
- **Data validation in prompt:** Add explicit rules like "Do not move to next step until email format is valid (text@domain.com)." The agent validates before proceeding.
- **Time slot enforcement:** "Appointments ONLY in 30-minute blocks (5:00 PM, 5:30 PM). NEVER accept 4:15." This prevented a whole class of booking errors.
- **Fee disclosure placement:** Put fee disclosure at a specific step (Step 6 here) — if it's in the opening, customers tune out. Disclosed after rapport is built, it lands better.
- **Out-of-order questions:** Add a "flow flexibility" rule — "If customer asks an out-of-order question (services, fees, branches), answer in one sentence then return to the current step."

### 7.4 Runtime variable injection

If the prompt has variables that change per call (customer name, call type, etc.), inject them in Python at call start:

```python
# kaya_prompt.py
def build_kaya_system_prompt(cart: dict) -> str:
    return (
        _TEMPLATE
        .replace("{{call_type}}", cart.get("call_type", "OUTBOUND"))
        .replace("{{customer_name}}", cart.get("customer_name", ""))
        .replace("{{customer_phone}}", cart.get("customer_phone", ""))
    )
```

This injected prompt is sent to ElevenLabs via the `conversation_initiation_client_data` override in the WebSocket handshake — so the agent sees the right data from the very first word.

---

## 8. Step 6 — Define client tools

### 8.1 How client tools work in this architecture

1. ElevenLabs agent decides to call a tool
2. ElevenLabs sends a `client_tool_call` event over the WebSocket
3. `handle_tool_calls()` in `voice_agent.py` dequeues the event
4. `execute_tool()` runs local Python and returns a result string
5. The result is sent back via `client_tool_result` over the same WebSocket
6. ElevenLabs reads the result and continues the conversation

The agent hears the tool result as text and decides what to say next.

### 8.2 The execute_tool function pattern

```python
async def execute_tool(tool_name: str, parameters: dict, cart: dict, state: dict) -> str:
    ts = datetime.now().isoformat()

    if tool_name == "book_appointment":
        # 1. Do the real work
        booking_id = log_kaya_booking(
            cart_id=cart["cart_id"],
            first_name=parameters["first_name"],
            ...
        )
        # 2. Update call state
        state["disposition"] = DISPOSITION_CONVERTED
        state["tool_calls"].append({"tool": tool_name, "ts": ts, "args": parameters})
        # 3. Return a string — the agent will read this and respond
        return f"Appointment confirmed. Booking ID: {booking_id}."

    if tool_name == "end_call":
        state["tool_calls"].append({"tool": tool_name, "ts": ts, "args": parameters})
        return "Call ended."  # agent will say goodbye per system prompt

    # Always have a fallback
    logger.warning(f"[TOOL] Unknown tool: {tool_name}")
    return f"Unknown tool: {tool_name}"
```

### 8.3 Tool design rules

- **Return short, factual strings.** The agent converts them to speech — "Booking confirmed. ID: 12345. Branch: Bandra." is better than JSON.
- **Set `state["disposition"]`** in every tool that has a meaningful outcome. This is what gets logged to the database after the call ends.
- **Deduplicate side effects.** Use a `state["sms_sent"]` flag so SMS isn't sent twice if the agent calls a tool twice.
- **Log every tool call** to `state["tool_calls"]` for post-call analysis.

---

## 9. Step 7 — Run the server

```bash
# Terminal 1 — ngrok tunnel
ngrok http 8000

# Terminal 2 — FastAPI server
source venv/bin/activate
python main.py
```

`main.py` does everything:
- Starts FastAPI on port 8000
- Initialises SQLite DB on startup
- Starts the queue worker background task (polls every 10 seconds)
- Mounts the `/twilio/media-stream` WebSocket endpoint

**You do not run `voice_agent.py` separately.** It is imported by `main.py`.

---

## 10. Step 8 — Test a call

### Test the Imagicaa agent

```bash
curl -X POST http://localhost:8000/webhook/cart-abandoned \
  -H "Content-Type: application/json" \
  -d '{
    "customer_name": "Neha",
    "customer_phone": "+919913874598",
    "cart_id": "CART-TEST-001",
    "visit_date": "15 June 2025",
    "tickets": [{"type": "Adult", "quantity": 2, "price_per_unit": 1499}],
    "total_amount": 2998,
    "attempt_number": 1
  }'
```

### Test the Kaya agent

```bash
curl -X POST http://localhost:8000/webhook/kaya-lead \
  -H "Content-Type: application/json" \
  -d '{
    "customer_name": "Bhavesh",
    "customer_phone": "+919999999999",
    "cart_id": "KAYA-TEST-001"
  }'
```

### What happens next (timeline)

```
0s     Webhook received → call enqueued in SQLite
10s    Queue worker picks it up → twilio_client.calls.create()
15s    Customer's phone rings
~25s   Customer answers → /twilio/answer fires → <Stream> TwiML returned
~26s   Twilio opens WebSocket to /twilio/media-stream
~26s   voice_agent.py connects to ElevenLabs WebSocket
~27s   session_init sent → agent hears the system prompt
~28s   Agent speaks its first_message opening line
```

### Check logs

```bash
tail -f logs/webhook.log    # server-level events (queue, Twilio, WebSocket)
```

Per-call transcripts are written to `logs/calls/<cart_id>-attempt-<N>-<timestamp>.log`.

### Check the database

```bash
python -c "
import sqlite3, json
conn = sqlite3.connect('post_call.db')
conn.row_factory = sqlite3.Row
rows = conn.execute('SELECT cart_id, disposition, duration_seconds, agent_type FROM call_logs ORDER BY id DESC LIMIT 5').fetchall()
for r in rows:
    print(dict(r))
"
```

### Check metrics

```
GET http://localhost:8000/metrics
GET http://localhost:8000/calls
```

---

## 11. Adding a second agent (multi-tenant)

The pattern used for Kaya alongside Imagicaa. Five touches across five files.

### The routing key: `agent_type`

Every call carries `agent_type` from the moment it enters the queue to the moment the WebSocket bridge connects to ElevenLabs. It flows through:

```
/webhook/kaya-lead
  → enqueue_call(agent_type="kaya")
    → _dispatch_and_dial builds kaya cart dict
      → dial_customer adds agent_type to answer URL
        → /twilio/answer?cart_id=X&agent_type=kaya
          → stream URL includes &agent_type=kaya
            → media_stream_handler(agent_type="kaya")
              → uses ELEVENLABS_KAYA_AGENT_ID + build_kaya_system_prompt()
```

### Files to create for a new agent

**1. `<agent>_prompt.py`** — prompt builder
```python
_TEMPLATE = """..."""  # full system prompt with {{variable}} placeholders

def build_<agent>_system_prompt(cart: dict) -> str:
    return (
        _TEMPLATE
        .replace("{{variable}}", cart.get("variable", ""))
    )
```

**2. `<agent>_branches.py`** (if needed) — any lookup data your tools need

### Files to modify for a new agent

**`main.py`:**
- Add `class <Agent>LeadPayload(BaseModel)` with the fields your webhook accepts
- Add `@app.post("/webhook/<agent>-lead")` endpoint that calls `enqueue_call(agent_type="<agent>")`
- Add `elif agent_type == "<agent>":` branch in `_dispatch_and_dial` to build the cart dict
- Update `_delayed_retry` to route retries to the correct endpoint based on `agent_type`

**`voice_agent.py`:**
- Add `ELEVENLABS_<AGENT>_AGENT_ID = os.getenv("ELEVENLABS_<AGENT>_AGENT_ID", "")`
- Add `elif agent_type == "<agent>":` block in `media_stream_handler` to build `session_init` with the new prompt and agent ID
- Add any new tools to `execute_tool()`

**`post_call.py`:**
- If the agent needs its own result table (like `kaya_bookings`), add it in `init_db()` and write a `log_<agent>_booking()` function

**`.env`:**
- Add `ELEVENLABS_<AGENT>_AGENT_ID=agent_xxxxxxxxxxxxxxxx`

---

## 12. Post-call observability

### What gets logged

Every call writes to `call_logs` in `post_call.db`:

| Column | What it contains |
|---|---|
| `disposition` | Final outcome code (CONVERTED, CALLBACK_SCHEDULED, NOT_INTERESTED, etc.) |
| `transcript` | Full JSON array of `{role, text, ts}` turns |
| `tool_calls` | JSON array of every tool that fired with args and timestamp |
| `latency_per_turn` | JSON array of e2e response latency in ms per turn |
| `first_response_ms` | Ms from user stopped speaking → agent first audio chunk |
| `duration_seconds` | Total call duration |
| `agent_type` | `"imagica"` or `"kaya"` |
| `language_detected` | `"english"` / `"hinglish"` / `"hindi"` |

### Disposition codes

```
CONVERTED             — Appointment booked / booking link accepted
INTERESTED_LINK_SENT  — Customer interested; SMS sent
CALLBACK_SCHEDULED    — Customer asked for a callback
NOT_INTERESTED        — Explicit refusal; stop retrying
TRANSFERRED_TO_HUMAN  — Escalated to agent
NO_ANSWER             — Call ended with no outcome (retryable)
BUSY                  — Customer busy (retryable)
UNREACHABLE           — Still NO_ANSWER after all 3 attempts
TECHNICAL_FAILURE     — WebSocket or tool error
```

### Retry logic

- `NO_ANSWER` and `BUSY` are retried up to `MAX_ATTEMPTS = 3` times
- Retry delay: 30 seconds in dev (`RETRY_DELAY_SECONDS`), set to 7200 (2 hours) for production
- Retries re-enqueue via the correct campaign webhook (`/webhook/cart-abandoned` for Imagicaa, `/webhook/kaya-lead` for Kaya)

---

## 13. Production checklist

Before going live with real customer calls:

### Compliance
- [ ] **TRAI calling hours:** Only call 9 AM–9 PM IST (enforced in `is_calling_hours()`)
- [ ] **DND registry:** Replace the hardcoded `DND_LIST` set with a live DND registry API lookup
- [ ] **Max attempts:** Keep `MAX_ATTEMPTS = 3` — don't increase without legal review
- [ ] **Call recording disclosure:** If you record, add a disclosure in the agent's opening

### Infrastructure
- [ ] Replace ngrok with a real domain (EC2 + nginx, Railway, Render, etc.)
- [ ] Move SQLite to PostgreSQL for concurrent writes
- [ ] Set `RETRY_DELAY_SECONDS = 7200` (2 hours between attempts)
- [ ] Set up log rotation and alerts on `TECHNICAL_FAILURE` rate

### ElevenLabs
- [ ] Test the agent on ElevenLabs playground with real voice before deploying to calls
- [ ] Enable webhook signature verification (`ELEVENLABS_WEBHOOK_SECRET` in `.env`)
- [ ] Choose a voice and test on actual phone audio — speaker quality changes significantly

### SMS
- [ ] For Kaya: the current SMS in `sms.py` sends an Imagicaa booking link. Write a Kaya-specific SMS function if you need confirmation texts
- [ ] For production India SMS: configure `MSG91_AUTH_KEY` and `MSG91_TEMPLATE_ID` (needs DLT approval, 1–2 days)

---

## 14. Common issues and fixes

### Agent speaks but tool call never fires

Check that the tool name in the system prompt exactly matches the tool name defined in the ElevenLabs dashboard and handled in `execute_tool()`. Case-sensitive.

### Twilio WebSocket connects but ElevenLabs gives 401

`ELEVENLABS_API_KEY` is wrong or expired. Verify at elevenlabs.io → Profile → API Keys.

### Agent is too slow to respond

Typical e2e latency on this stack is 800ms–1.4s. If you're seeing >2s:
- Check your ngrok latency (`ngrok http 8000` shows connection stats)
- ElevenLabs has regional endpoints — ensure you're hitting the closest one
- Reduce the system prompt length — very long prompts increase TTS processing time

### Audio is choppy or robot-sounding

The Twilio ↔ ElevenLabs bridge converts:
- Twilio → ElevenLabs: µ-law 8kHz → PCM 16kHz (via `audioop.ulaw2lin` + `audioop.ratecv`)
- ElevenLabs → Twilio: PCM 16kHz → µ-law 8kHz

If choppy, check that `output_format: "pcm_16000"` is set in `session_init.tts`. Do not change this — it must match `ELEVENLABS_SAMPLE_RATE = 16000`.

### Agent interrupts itself after tool call

The ElevenLabs agent sometimes starts speaking the tool result before the human confirms. Add to the system prompt: `"Do not confirm tool execution out loud."` and `"Do not announce what tool you are about to call."`

### `ELEVENLABS_KAYA_AGENT_ID` not set warning

If you see `[WS] ELEVENLABS_KAYA_AGENT_ID not set — falling back to ELEVENLABS_AGENT_ID` in logs, add the key to `.env`. The system falls back gracefully but will use the wrong agent.

### Queue worker dispatches but call never dials

Check: is `TWILIO_FROM_NUMBER` in E.164 format? (`+13185043576` not `13185043576`). Also verify calling hours — the worker skips calls outside 9 AM–9 PM IST.

### `audioop` import error on Python 3.13+

`audioop` was removed from Python 3.13. Pin to Python 3.11 or 3.12 (`python3.12 -m venv venv`).
