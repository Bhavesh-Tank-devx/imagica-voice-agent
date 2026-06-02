# Session Context Dump — Kaya Clinic Voice Agent
_Last updated: 2026-06-02_

## What was built across sessions

Added a full second voice agent campaign (Kaya Clinic) to the existing Imagicaa multi-tenant architecture. Both agents share the same FastAPI server, Twilio number, and WebSocket bridge. Routing is determined by `agent_type` in the cart dict.

In this session: debugged the full call path end-to-end, made a live test call, and confirmed Kaya's Priya speaks over the phone. Also rewrote the system prompt and split the knowledge base per ElevenLabs best practices.

---

## Current state

### Server
- **Entry point:** `python main.py` (port 8000)
- **Tunnel:** `ngrok http --url=redressable-spectrochemical-aarav.ngrok-free.dev 8000` (must run alongside server)
- **Status:** ✅ Fully working — live test call completed, Priya spoke and collected booking details

### Call flow — verified working
```
POST /webhook/kaya-lead
  → enqueue_call(agent_type="kaya", cart_value=0)
    → queue worker picks up in ~10s
      → _dispatch_and_dial builds kaya cart dict
        → dial_customer → twilio_client.calls.create()
          → Twilio dials customer
            → customer answers → AMD fires
              → POST /twilio/answer?cart_id=X&agent_type=kaya
                → returns <Stream url="wss://ngrok/twilio/media-stream"> TwiML (no query params in stream URL)
                  → Twilio opens WebSocket
                    → server accepts WebSocket, reads until "start" event to get callSid
                    → looks up cart via call_sessions[callSid]
                      → media_stream_handler(agent_type="kaya")
                        → sends session_init with dynamic_variables + pcm_16000 format
                        → connects to ELEVENLABS_KAYA_AGENT_ID
                        → audio bridge runs
                          → tool calls: get_closest_branches, book_appointment, end_call
                            → post_call: log_call + log_kaya_booking (if CONVERTED)
```

---

## Bugs fixed this session

### 1. WebSocket 403 — Twilio strips query params from WebSocket URL
**Root cause:** Twilio does not forward URL query parameters when opening a WebSocket connection. `cart_id` was in the stream URL but arrived empty at the server.

**Fix in `main.py`:**
- WebSocket handler now calls `websocket.accept()` first
- Reads Twilio messages until the `"start"` event, which carries `callSid`
- Looks up `cart_id` via `call_sessions[callSid]` (populated by `/twilio/answer`)
- Passes pre-read messages to `media_stream_handler` as `preread` list for replay

**Fix in `voice_agent.py`:**
- `media_stream_handler` no longer calls `websocket.accept()` (caller does it)
- Added `preread: list | None = None` parameter
- `twilio_to_elevenlabs()` replays preread messages before starting the live loop

### 2. ElevenLabs 1008 — `first_message` override not allowed
**Root cause:** ElevenLabs agent security settings block `first_message` override from the client.
**Fix:** Removed `first_message` from `session_init`. Set it directly in the ElevenLabs dashboard.

### 3. ElevenLabs 1008 — `prompt` override not allowed
**Root cause:** Same security restriction for the `prompt` field.
**Fix:** Removed `prompt` from `session_init`. System prompt is now set and managed entirely in the ElevenLabs dashboard. `session_init` only sends `tts.output_format: pcm_16000` and `dynamic_variables`.

### 4. ElevenLabs 1008 — missing required dynamic variable `customer_phone`
**Root cause:** The Kaya agent dashboard has tools that use `{{customer_phone}}` as a dynamic variable placeholder. These must be supplied at conversation start.
**Fix:** Added `dynamic_variables` to `session_init` in `voice_agent.py`:
```python
"dynamic_variables": {
    "customer_phone": cart.get("customer_phone", ""),
    "customer_name": cart.get("customer_name", ""),
    "city": cart.get("city", ""),
}
```

### 5. Retry firing on answered calls
**Root cause:** Calls lasting 7+ minutes with a full conversation were logged as `NO_ANSWER` (no booking tool was called), triggering retries.
**Fix in `voice_agent.py` `_post_call()`:**
- If `disposition == NO_ANSWER` and `duration_sec > 60`: upgrades disposition to `CALL_COMPLETED_NO_OUTCOME`
- Retry only fires if `duration_sec <= 60` (true no-answer / busy)

### 6. `/webhook/call-ended` returning 404
**Root cause:** ElevenLabs POSTs to this URL after each conversation (configured in agent dashboard) but the route didn't exist.
**Fix:** Added `POST /webhook/call-ended` endpoint in `main.py` — logs the event, returns 204.

---

## Files modified this session

### `main.py`
- WebSocket handler: accept first → read until `"start"` event → lookup cart via `call_sessions[callSid]` → pass `preread` to handler
- Added `POST /webhook/call-ended` endpoint for ElevenLabs post-call webhook

### `voice_agent.py`
- `media_stream_handler`: removed `websocket.accept()`, added `preread` param, replays preread messages in `twilio_to_elevenlabs()`
- `session_init`: stripped to `tts.output_format` + `dynamic_variables` only (no prompt/first_message override)
- Added `dynamic_variables`: `customer_phone`, `customer_name`, `city`
- Added `DISPOSITION_CALL_COMPLETED_NO_OUTCOME` import
- `_post_call`: upgrades `NO_ANSWER` → `CALL_COMPLETED_NO_OUTCOME` if `duration > 60s`
- Retry guard: skips retry if `duration_sec > 60`

### `post_call.py`
- Added `DISPOSITION_CALL_COMPLETED_NO_OUTCOME = "CALL_COMPLETED_NO_OUTCOME"` constant

### `kaya_prompt.py`
- Full rewrite per ElevenLabs prompting guide best practices
- Prompt now targets ~900 tokens (down from ~1800)
- Removed all reference data (branches, services list) — these live in the knowledge base
- Added `# Guardrails` section (ElevenLabs tunes the model to this heading)
- Added `"This step is important"` on critical rules: phone privacy, farewell sequence, city confirmation, time slot validation
- Added `# Tools` section with parameter formats (date: YYYY-MM-DD, time: HH:MM 24hr)
- Added full Special Scenarios section: NOT_INTERESTED, CALLBACK, WANTS_HUMAN, WRONG_NUMBER, RETURNING_PATIENT, TRUST_CHALLENGE, ALREADY_HAS_APPOINTMENT
- Added city confirmation step before branch lookup (fixes Vadodara/Surat class of errors)
- Added clinic hours (10 AM–8 PM Mon–Sat) to time slot rule
- Added pin code fallback (when customer doesn't know it)
- Added DOB explanation if asked
- Made referral question optional

### `kaya_knowledge_base.txt`
- Added Section 7b with branch names per city and "Confirm Address" cities to existing file
- This file is now superseded by the three split files below

### `.env`
- Updated `ELEVENLABS_KAYA_AGENT_ID` to `agent_9401kt3ja3hnefya5ch2v13hrgsm`

---

## New files created this session

| File | Purpose | ElevenLabs usage mode |
|---|---|---|
| `kaya_kb_branches.txt` | Branch names per city, Confirm Address cities, consultation fees, branch suggestion rules | **prompt** (always injected) |
| `kaya_kb_services.txt` | All skin/hair/body treatments + concern→treatment guide | **auto** |
| `kaya_kb_general.txt` | Company info, products, FAQs, contact details | **auto** |

---

## ElevenLabs Kaya Agent config

- **Agent ID:** `agent_9401kt3ja3hnefya5ch2v13hrgsm`

### Dashboard settings
- System prompt: paste `_TEMPLATE` from `kaya_prompt.py`
- Knowledge base: upload the 3 split KB files with usage modes as above
- Dynamic variables allowed: `customer_phone`, `customer_name`, `city`, `call_type`
- Post-call webhook URL: `https://redressable-spectrochemical-aarav.ngrok-free.dev/webhook/call-ended`

### RAG configuration
| Setting | Value |
|---|---|
| enabled | true |
| embedding_model | e5_mistral_7b_instruct |
| max_vector_distance | 0.5 |
| max_documents_length | 50000 |
| max_retrieved_rag_chunks_count | 10 |

### Tools (Client Tools, Wait for response ON)
- `get_closest_branches` — params: `pincode` (string, optional), `city` (string, optional)
- `book_appointment` — params: `first_name`, `last_name`, `email`, `pincode`, `branch_name`, `appointment_date` (YYYY-MM-DD), `appointment_time` (HH:MM 24hr), `dob` (YYYY-MM-DD), `city` — all required
- `schedule_callback` — params: `preferred_time` (string, required)
- `transfer_to_human` — params: `reason` (string, required)
- `mark_not_interested` — no params
- `end_call` → **System Tool** (not Client Tool)

---

## Environment

```
BASE_URL=https://redressable-spectrochemical-aarav.ngrok-free.dev
TWILIO_FROM_NUMBER=+13185043576
ELEVENLABS_AGENT_ID=agent_4101kngt2rwqepascfne4kw17p3c        # Imagicaa
ELEVENLABS_KAYA_AGENT_ID=agent_9401kt3ja3hnefya5ch2v13hrgsm  # Kaya
```

---

## How to start

```bash
# Terminal 1
ngrok http --url=redressable-spectrochemical-aarav.ngrok-free.dev 8000

# Terminal 2
source venv/bin/activate
python main.py
```

Test Kaya: open `http://localhost:8000/kaya`, fill name + phone + city, submit.

---

## Known issues / TODOs

- [ ] Kaya SMS confirmation not implemented (`sms.py` still sends Imagicaa booking link — needs Kaya-specific confirmation SMS)
- [ ] `cart_value=0` for Kaya leads — queue priority sorting doesn't apply, all Kaya calls have equal priority
- [ ] DND list is hardcoded — needs live registry API for production
- [ ] `RETRY_DELAY_SECONDS=30` in `retry.py` — change to `7200` for production
- [ ] Upload `kaya_kb_branches.txt`, `kaya_kb_services.txt`, `kaya_kb_general.txt` to ElevenLabs dashboard (not yet done)
- [ ] Configure RAG settings in ElevenLabs dashboard (not yet done)
- [ ] Kaya agent `book_appointment` tool in dashboard still has `dob` and `city` as optional — update to required to match code
- [ ] ElevenLabs quota exhausted during test call — upgrade plan before production
