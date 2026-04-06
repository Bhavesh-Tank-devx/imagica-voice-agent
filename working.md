# How the Imagicaa Priya Voice Agent Works — End to End

This document explains every step of the system in detail: what it does, why it is built that way, what the alternatives are, and what can be improved. It is written for someone who already knows the codebase.

---

## Architecture Overview

The system is two independent processes that communicate via LiveKit's cloud infrastructure:

```
[ Browser / Phone ] ──audio──► [ LiveKit Cloud Room ]
                                        │
                          ┌─────────────┴──────────────┐
                          │                            │
              [ FastAPI Server / main.py ]   [ Agent Worker / agent.py ]
               - Receives webhook              - Connects to LiveKit room
               - Creates room                  - Runs Gemini Live model
               - Dispatches agent              - Handles conversation
               - Serves dashboard              - Calls tools
               - Logs calls (SQLite)           - Writes post-call data
               - Retries failed calls
```

**Why two processes?**
The agent worker (agent.py) is a short-lived subprocess. It spawns, handles one call, then exits. The FastAPI server (main.py) is long-lived — it handles HTTP, holds retry timers, and serves the dashboard. If both were in one process, a crash in the agent worker would take down the whole server.

**Why LiveKit?**
LiveKit is a WebRTC media server. It handles the hard parts: real-time audio transport, TURN/STUN traversal, SIP bridging for phone calls, and participant management. Without it, you'd need to build low-latency audio streaming infrastructure yourself.

---

## Step 1 — Cart Abandonment Event Arrives (main.py)

### What happens
The booking engine POSTs a JSON payload to `POST /webhook/cart-abandoned`. In production this would come from Imagicaa's booking platform. In dev, you fire it manually from the dashboard.

### Payload shape
```json
{
  "customer_name": "Bhavesh",
  "customer_phone": "+919913874598",
  "cart_id": "CART-BHAVESH-05051",
  "visit_date": "12 April 2026",
  "tickets": [{"type": "Adult", "quantity": 2, "price_per_unit": 1499}],
  "total_amount": 4796,
  "attempt_number": 1,
  "mode": "browser"
}
```

The `mode` field is dev-only — browser skips SIP dialling so you can test via the playground. In production you'd always use `phone`.

### Why FastAPI?
Async HTTP framework that integrates cleanly with LiveKit's async Python SDK. Uvicorn (the ASGI server) runs its own event loop which hosts retry timers even after the agent subprocess exits.

---

## Step 2 — Calling Hours + DND Check (main.py)

### Calling hours
```python
CALLING_HOURS_START = 9   # 9 AM IST
CALLING_HOURS_END   = 21  # 9 PM IST
```
TRAI regulations in India prohibit outbound marketing calls outside 9 AM–9 PM IST. Calls attempted outside this window get `"status": "suppressed"` and are not queued for later — the webhook fires once and is gone.

**Current gap:** If the webhook arrives at 11 PM, it is dropped. In production you'd enqueue it and fire again at 9 AM next day. Right now that logic does not exist.

### DND check
`DND_LIST` is a hardcoded Python set of phone numbers to never call. TRAI maintains a national DND registry. The live API for it (NDNC / TRAI DND API) requires a registered telemarketer ID and is not free. The hardcoded set is a placeholder.

**Alternative:** Scrub the number against the live TRAI NDNC API before every call. Add a Redis cache layer (24 hours TTL) so you're not hitting the API on every webhook.

---

## Step 3 — LiveKit Room Creation (main.py)

```python
room_name = f"imagica-{cart_id}-{attempt_number}"

await lk.room.create_room(
    CreateRoomRequest(name=room_name, empty_timeout=1800)
)
```

A LiveKit room is the virtual space where all audio participants meet — the agent, the customer, and (if needed) the CCT human agent. Each call gets its own isolated room.

**Why `empty_timeout=1800`?**
By default LiveKit deletes a room after 5 minutes of being empty. In browser mode, the developer has to open the playground, copy the token, paste it, and join — this can easily take more than 5 minutes. Without this setting, the room would expire before the developer joins, and the agent crashes waiting for a participant. 1800 seconds (30 minutes) gives ample time.

**Stale dispatch cleanup:**
Before creating a new dispatch, the code lists all existing dispatches for that room and deletes them. This handles the case where the server restarts mid-call — on restart it might try to re-dispatch to a room that already has a running agent, causing two agents to compete. Deleting stale dispatches prevents this.

---

## Step 4 — Agent Dispatch (main.py)

```python
dispatch = await lk.agent_dispatch.create_dispatch(
    CreateAgentDispatchRequest(
        agent_name="imagica-priya",
        room=room_name,
        metadata=json.dumps(cart_data),
    )
)
```

This tells LiveKit's dispatch system: "find a running worker named `imagica-priya` and assign it the job of entering room `room_name`, passing `cart_data` as metadata."

**How cart data travels:**
The metadata field (a plain JSON string) is the only data channel between the webhook server and the agent worker. The agent reads it at startup with `json.loads(ctx.job.metadata)`. If it fails to parse, it falls back to `CART_DATA` from `mock_data.py`.

**Why not a database lookup in the agent?**
The agent is a subprocess that might run on a different machine in a scaled deployment. Passing data through the dispatch metadata avoids a shared database dependency between the two processes. It also makes each call self-contained — the agent has everything it needs from the start.

**`agent_name` must match `WorkerOptions(agent_name=...)` in agent.py:**
Both are hardcoded to `"imagica-priya"`. If they differ, the dispatch sits in a queue indefinitely and nothing happens.

---

## Step 5 — SIP Dial (Phone Mode Only) (main.py)

```python
if payload.mode == "phone":
    asyncio.create_task(dial_customer(room_name, payload.customer_phone))
```

```python
await lk.sip.create_sip_participant(
    CreateSIPParticipantRequest(
        sip_trunk_id=SIP_TRUNK_ID,
        sip_call_to=phone,
        room_name=room_name,
        participant_identity="customer",
        wait_until_answered=True,
    )
)
```

LiveKit makes an outbound SIP call to the customer's phone via the SIP trunk (a Twilio-configured SIP trunk in this case). The `wait_until_answered=True` flag is critical — it blocks until the customer picks up, so the agent doesn't start speaking into voicemail ringback.

Once the customer picks up, they appear in the LiveKit room as a participant with identity `"customer"`. The agent, already waiting in the room, detects this and begins the conversation.

**Why `asyncio.create_task` and not `await`?**
`dial_customer` can block for 30–60 seconds (ringing time). The webhook response must return immediately (otherwise the booking engine times out). The task runs in the background.

**Browser mode difference:**
In browser mode, no SIP call is placed. The agent sits in the room waiting. The developer joins manually via the LiveKit Agents Playground using the token from the dashboard. `participant_identity` in browser mode is `"developer"` (set by the `/token` endpoint).

**Current limitation:**
`SIP_TRUNK_ID` is an env var. If not set, the function logs "skipping SIP dial" and returns. This is the browser testing mode.

---

## Step 6 — Agent Worker Startup (agent.py)

The agent worker (`python agent.py dev`) runs continuously as a separate process. It connects to LiveKit Cloud and registers itself as a worker named `"imagica-priya"`. When a dispatch arrives, LiveKit hands it an `entrypoint` function call.

```python
async def entrypoint(ctx: JobContext):
    cart = CART_DATA  # fallback
    if ctx.job.metadata:
        cart = json.loads(ctx.job.metadata)  # real cart from webhook

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    participant = await ctx.wait_for_participant()
```

`auto_subscribe=AUDIO_ONLY` means the agent subscribes to audio tracks only — it ignores any video the browser might send. This reduces bandwidth and is the right setting for a voice-only agent.

`wait_for_participant()` blocks until someone joins the room. This is how the agent knows the customer has picked up (phone mode) or the developer has joined (browser mode).

---

## Step 7 — System Prompt Injection (agent.py)

```python
def build_system_prompt(cart: dict) -> str:
    tickets_summary = ", ".join(
        f"{t['quantity']} {t['type']}" for t in cart["tickets"]
    )
    return f"""
You are Priya...
- Visit Date: {cart['visit_date']}
- Tickets: {tickets_summary}
- Total Amount: ₹{cart['total_amount']}
...
"""
```

All customer-specific data (name, cart contents, visit date, total amount) is injected into the prompt at call start. The agent does not look anything up during the call — everything it needs is in the prompt.

**Why inject at start instead of tool lookup?**
Gemini Live is a streaming audio model with no pause between user speech and agent response. If the agent had to call a database mid-conversation, you'd get a noticeable silence. Injecting at start gives zero-latency access to all cart data.

**What the prompt contains (and why):**

| Section | What it says | Why |
|---|---|---|
| Opening line | Scripted first sentence referencing cart details | Ensures a consistent, warm, accurate opening on every call |
| Conversation flow (5 steps) | Listen → Address concern → Close → Exit | Structures the model's decision tree so it doesn't go off-script |
| Language rules | Default Hinglish, switch to Hindi or English based on 2+ consecutive turns | Urban Indian customers mix Hindi and English; forcing one language sounds unnatural |
| Gender rules | Priya always uses feminine Hindi verb forms | LLMs default to masculine forms; without these rules Priya says "bol raha hoon" |
| Tools section | What each tool is for | The model needs to know what's available to call |
| Hard rules | No made-up prices, calling hours, max 3 attempts | Prevents hallucination and policy violations |

---

## Step 8 — Gemini Live Model Setup (agent.py)

```python
model = google.beta.realtime.RealtimeModel(
    model="gemini-live-2.5-flash-native-audio",
    vertexai=True,
    project=PROJECT_ID,
    location=LOCATION,
    voice="Puck",
    temperature=0.6,
    input_audio_transcription=AudioTranscriptionConfig(),
    output_audio_transcription=AudioTranscriptionConfig(),
)
```

**Why Gemini Live instead of standard LLM?**
Standard pipeline: Customer speaks → STT (transcribe) → LLM (text response) → TTS (synthesize) → play. This has 3 serial network calls with ~2–4 seconds total latency.

Gemini Live is a single model that handles audio-in → audio-out directly. It processes speech in real time, interrupts gracefully, and responds in ~400ms. This is why the agent sounds natural instead of robotic.

**`native-audio` vs standard Gemini Live:**
`gemini-live-2.5-flash-native-audio` generates audio natively, not via a TTS synthesis step. The voice is more natural but the model can sometimes generate lower-quality speech in non-English languages. The standard variant processes text then synthesises — slower but more controllable.

**Voice: `"Puck"`**
Tested voices for Hinglish naturalness. `"Puck"` and `"Aoede"` are the most natural-sounding for mixed Hindi-English. Other voices have stronger American accents that sound incongruous for an Indian customer call.

**`temperature=0.6`**
Lower temperature (0.0–0.3) makes responses more deterministic but also more robotic. Higher (0.8–1.0) is more creative but risks going off-script. 0.6 balances natural variation with consistency.

**`input_audio_transcription`:**
Enables Gemini to transcribe the customer's speech to text as a side-channel. This feeds `conversation_item_added` events which populate the transcript in the call log. Without this, you'd have no record of what the customer said.

**`output_audio_transcription`:**
Same but for Priya's speech. Without this the transcript logs show Priya's turns as empty.

---

## Step 9 — The Opening Greeting (agent.py)

```python
async def on_enter(self) -> None:
    await self.session.generate_reply(
        instructions="Start the call now with your opening greeting..."
    )
```

`on_enter` fires when the agent enters the session. Without `generate_reply`, the realtime model waits silently for the customer to speak first. This creates an uncomfortable silence at the start of a phone call.

`generate_reply` forces the model to speak immediately. The instructions parameter nudges it to use the opening line in the system prompt rather than generating something arbitrary.

**Why is the first response slow (~10–18 seconds)?**
This is Gemini Live's cold-start latency — the time to:
1. Establish the WebSocket connection to Google's servers
2. Upload the full session config (system prompt, tools, audio settings)
3. Generate and stream the first audio frame

Subsequent turns are ~400ms. The cold start is a property of the Gemini Live API and cannot be eliminated in the current architecture. One mitigation: pre-warm the model connection in `on_enter` before waiting for the participant, so the WebSocket is ready when the customer joins. This is not implemented yet.

---

## Step 10 — The Conversation Loop

Once the session starts, the realtime model runs a continuous loop managed by LiveKit Agents internally:

```
Customer speaks
     │
     ▼
VAD (Voice Activity Detection)
     │ detects end of speech
     ▼
Audio chunk sent to Gemini Live
     │
     ▼
Gemini processes audio → decides: respond with speech, call tool, or both
     │
     ├─ Speech: streams audio frames back to LiveKit → customer hears Priya
     │
     └─ Tool call: LiveKit Agents invokes the @function_tool method in PriyaAgent
```

**State machine (relevant to latency measurement):**
```
initializing → listening → speaking → listening → speaking → ...
```
The `"thinking"` state only appears in the standard STT→LLM→TTS pipeline and after tool execution. For native audio Gemini Live, the state goes directly `listening → speaking` — there is no "thinking" phase between normal turns. This is why the old latency tracker (which watched for "thinking") missed most turns.

**Barge-in (interruption):**
If the customer speaks while Priya is speaking, the VAD detects it and sends the interrupt signal. Gemini Live stops mid-sentence. This is intentional for natural conversation but can be triggered by background noise, another person talking in the room, or TV audio — producing phantom interruptions that make Priya sound choppy.

**Latency measurement (fixed):**
We now listen to `user_state_changed` → `"listening"` (user stops speaking) and `agent_state_changed` → `"speaking"` (Priya starts responding). The diff is the true round-trip latency per turn. This fires on every turn regardless of tool calls.

---

## Step 11 — Function Tools (agent.py)

The model can call five tools during a conversation. These are defined as methods decorated with `@function_tool` on `PriyaAgent`.

### `send_booking_link`
Sends the booking link via SMS. Sets disposition to `INTERESTED_LINK_SENT`. Starts a 90-second exit timer.

**Trigger (current):** Customer gives a clear affirmative — "bhej do", "book kar lunga", "theek hai bhejo", "yes send it".
**Does NOT trigger on:** "main sochti hoon", "later dekhta hoon", "maybe" — these require Priya to ask "Kya main link bhej doon?" and wait for confirmation.

**Why 90 seconds exit timer?**
After the link is sent, Priya says something like "Maine link bhej diya hai, aap jab chahein book kar lena." The customer might respond. 90 seconds gives both sides time to close the conversation naturally before the room auto-shuts.

### `schedule_callback`
Logs the preferred callback time. Sets disposition to `CALLBACK_SCHEDULED`. Starts 90-second exit timer.

**Current limitation:** Nothing actually schedules a real callback. In production this would write to a task queue (e.g. AWS Step Functions, DynamoDB TTL trigger) that re-fires the webhook at the customer's preferred time.

### `transfer_to_human`
If `SIP_TRUNK_ID` and `CCT_DEMO_PHONE` are set, dials the CCT queue number into the room. Priya says "Please hold" and exits after 6 seconds. The customer and the CCT agent are now talking directly.

**Why 6 seconds not 90?**
Priya only needs to say a single handoff message before leaving. The human agent takes over. 6 seconds is enough for one sentence.

### `mark_not_interested`
Logs the refusal. Sets disposition to `NOT_INTERESTED`. Starts 15-second exit timer.

**Why 15 seconds?**
After the tool fires, the model needs to generate and play a goodbye message. With the previous 8-second timer, the call was ending before Priya could speak, causing abrupt silent disconnects. 15 seconds gives enough time for a ~5-second goodbye + buffer.

**What Priya says now:**
The tool returns an explicit instruction: "Say a warm, brief goodbye — 'Theek hai, koi baat nahi. Aapka bahut shukriya aur have a great day!' Then end the conversation." The model follows this as its next response.

### `apply_discount`
Applies 5–10% discount (clamped), recomputes total, sends SMS with the same booking link. This tool also sends the SMS if not already sent, setting `_sms_sent = True` to prevent duplicates.

---

## Step 12 — SMS Sending (sms.py)

```python
async def send_booking_sms(phone, name, link):
    if MSG91_AUTH_KEY and MSG91_TEMPLATE_ID:
        return await _send_via_msg91(...)  # production India SMS
    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
        return await _send_via_twilio(...)  # demo/testing
    logger.info(f"[SMS MOCK] Would send to {phone} → {link}")
    return False
```

**MSG91 (production):**
India's preferred bulk SMS provider. Requires a DLT-registered sender ID and pre-approved template (mandatory by TRAI for transactional/promotional SMS). The template must have placeholders like `{{name}}` and `{{link}}` exactly matching the API call. DLT approval takes 1–2 business days.

**Twilio (demo):**
Works immediately with a Twilio account. The free trial can only send SMS to phone numbers verified in your Twilio console. Production Twilio requires a paid account and a registered Indian sender route.

**Dedup guard (`_sms_sent`):**
A single call can trigger both `apply_discount` and `send_booking_link` (e.g. Priya applies a discount and then the customer says "okay bhej do"). `_sms_sent = True` after the first SMS prevents a second message going out for the same link.

---

## Step 13 — Call End and Cleanup (agent.py)

```python
await call_ended.wait()
```

Three things can trigger `call_ended`:

| Trigger | How | When |
|---|---|---|
| Customer leaves | `participant_disconnected` event | Customer hangs up / closes playground |
| Tool exit timer | `_exit_after_delay()` | Tool fires and timer expires (90s, 15s, 6s) |
| Session close | `session.on("close")` | Gemini Live WebSocket drops (auth error, network issue) |

After `call_ended` fires:

1. **Disposition mapping:** If disposition is still `NO_ANSWER` (default) and this was the last attempt, map it to `UNREACHABLE`.
2. **Remove SIP participant:** For phone mode, explicitly remove the `"customer"` participant from the room. This drops their phone call immediately. Without this, the phone line stays open and the customer hears silence.
3. **Disconnect agent:** `ctx.room.disconnect()` — agent leaves the room.
4. **Delete room:** `lk.room.delete_room()` — cleans up the LiveKit room. Without this, empty rooms accumulate.

**Why is room deletion separate from agent disconnect?**
When the agent disconnects, if the customer was still in the room (browser mode, playground), the room might stay alive. Explicitly deleting it ensures cleanup regardless of participant state.

---

## Step 14 — Post-Call Logging (post_call.py + log_setup.py)

### SQLite database (`post_call.db`)
Every call writes one row to `call_logs`. Fields include: cart_id, customer info, disposition, full transcript (JSON), tool calls (JSON), per-turn latency (JSON array), duration, and timestamps.

This database serves:
- The `GET /calls` API (dashboard call table)
- The `GET /metrics` API (aggregate stats)
- The `GET /calls/{id}` API (full call detail)

**Production swap:** Replace `sqlite3` with a PostgreSQL client (asyncpg or psycopg3). The schema is intentionally flat so it maps to a CRM (Zoho Lead fields) with minimal transformation.

### Per-call log file (`logs/calls/*.log`)
Human-readable summary written after every call: disposition, duration, transcript with timestamps, tools called, and latency stats. Useful for quick debugging without opening the database.

### Agent log (`logs/agent.log`)
Rotating file log (5 MB, 5 backups). All `[LATENCY]`, `[TRANSCRIPT]`, `[CALL END]`, `[CRM WRITE]` entries land here. The log is shared between the agent worker and the webhook server because both use the same `setup_logging()` utility.

---

## Step 15 — Retry Logic (retry.py + main.py)

```python
RETRYABLE_DISPOSITIONS = {"NO_ANSWER", "BUSY"}
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 30  # production: 7200 (2 hours)
```

If the call ends with `NO_ANSWER` or `BUSY` and attempts < 3, the agent calls `schedule_retry(cart)` which POSTs to `/internal/schedule-retry` on the FastAPI server.

**Why does the retry live in FastAPI and not the agent?**
The agent is a subprocess that exits after each call. `asyncio.sleep(7200)` inside a subprocess would be killed when the process exits. The FastAPI server (uvicorn) stays alive indefinitely — its event loop can hold a 2-hour sleep safely via `asyncio.create_task(_delayed_retry(cart))`.

**The retry flow:**
```
agent.py (subprocess):
  call ends NO_ANSWER
  → POST /internal/schedule-retry {cart with attempt+1}
  → exit

main.py (uvicorn, stays alive):
  receives POST
  → asyncio.create_task(_delayed_retry(cart))  ← holds 2h sleep
  → (2 hours later) POST /webhook/cart-abandoned with attempt=2
  → normal dispatch flow starts again
```

---

## Conversation Quality: What the Logs Show and How to Improve

Based on reading all 7 call transcripts:

### What is working well
- **CART-BHAVESH-72555**: Perfect call. 5-turn natural conversation. Customer said "call me after 2 days." Priya scheduled callback, said proper goodbye "Have a good day!" — this is the ideal flow.
- **CART-BHAVESH-96670**: Good call. Customer said "urgent kaam aa gaya." Priya asked before sending link. Customer confirmed "haan, theek hai." Priya sent link and closed properly.

### Problem 1 — Background noise triggering tools ✅ FIXED
**CART-BHAVESH-29800**: Customer was clearly talking to someone else ("Pawan, aage badh") — background conversation. Priya heard fragments and called `schedule_callback`, then `send_booking_link` when the customer said "yo" while walking away.

**CART-BHAVESH-66984**: Customer made random sounds ("DCM", "de un", "2 1") — likely phone shuffling or background sounds. Priya interpreted these as price objection and applied a 10% discount.

**Root cause:** Gemini Live's VAD is very sensitive. Any audio above the threshold — background TV, other people talking, handling noise — triggers the model to respond. This is a property of the model, not the code.

**Fixes applied (2026-03-27):**
- `realtime_input_config` added to `RealtimeModel`: `START_SENSITIVITY_LOW`, `END_SENSITIVITY_LOW`, `silence_duration_ms=500`, `prefix_padding_ms=20` — requires more sustained speech to trigger a turn; filters short noise bursts
- Confusion recovery in Tone Rules: if Priya hears unrelated words/names/digits, she says "Sorry, kya aap mujhse baat kar rahe the?" and waits before acting
- `noise_cancellation.BVC()` added to `session.start()` via `RoomInputOptions` — suppresses phone echo before it reaches the VAD
- Confirmed working in load test: CART-LOAD-00004 showed "Sorry, kya aap mujhse baat" firing correctly when customer talked to someone else

### Problem 2 — SMS sent on "कोई प्रॉब्लम नहीं थी" (No problem existed)
**CART-BHAVESH-05051**: Customer said "नहीं, कोई प्रॉब्लम नहीं थी।" (No, there was no problem). This is a neutral statement — the customer is saying the booking didn't have any particular issue. The old prompt triggered `send_booking_link` immediately on this.

**Fix already applied:** The prompt now distinguishes explicit requests from ambiguous ones. "कोई प्रॉब्लम नहीं" should now route to Priya asking: "Kya main aapko link bhej doon taaki aap baad mein complete kar sakein?"

### Problem 3 — Abrupt disconnect on "Not interested"
**CART-NEHA-JOS-17868**: Customer said "Not interested." Tool fired, 8-second timer started. Call ended silently with no goodbye from Priya.

**Fix already applied:** Exit delay for `mark_not_interested` increased to 15 seconds. Tool return now explicitly instructs the model to say a warm goodbye before ending.

### Problem 4 — Priya is too persistent on price ✅ FIXED
**Fix applied (2026-03-27):** `apply_discount` tool description updated. Now only triggers on: second price mention, or explicit phrases ("bahut mehnga hai", "afford nahi hoga", "kam karo", "discount milega kya"). On first hesitation, Priya acknowledges price and listens. Confirmed in CART-LOAD-00003: customer mentioned price + date issue in one turn; Priya asked about date change first, did not offer discount until after date was resolved.

**Remaining edge case:** If customer says "bahut pricy" (contains "bahut" near price context), model treats it as explicit trigger even on first mention — borderline but defensible per the rule.

### Problem 5 — No recovery when customer is mid-conversation with someone else ✅ FIXED
**Fix applied (2026-03-27):** Confusion recovery rules added to Tone Rules section of system prompt:
```
- If you hear a very short, unrelated word, a name (e.g. "Pawan", "DCM"), single digits,
  or something that does not fit context — say "Sorry, kya aap mujhse baat kar rahe the?" and wait.
- If no response in next turn, say "Koi baat nahi, main baad mein call karti hoon" and end gracefully.
```
Confirmed working in load test: CART-LOAD-00004, customer said "पति जय छोटू-मोनी को छे।" (Gujarati, talking to someone else) → Priya responded "Sorry, kya aap mujhse baat" correctly.

### Problem 6 — Priya cuts her own sentences short (barge-in from SIP echo)
Several transcripts show Priya's sentences ending mid-phrase: "Kya aapko booking", "main aapko baad mein call", "Theek hai, main aapko link". This is acoustic echo — on SIP phone calls there is no WebRTC echo cancellation, so the phone's speaker output leaks back into the microphone, the VAD detects it as customer speech, and Priya interrupts herself. Customer Vimal explicitly said "आपकी आवाज कट रही है" (your voice is cutting) during testing.

**Root cause:** `aec warmup` only suppresses interruptions for 3 seconds at session start. Opening greetings take longer than 3 seconds on phone calls, so by the time barge-in is re-enabled the echo is still present and immediately fires.

**Fix applied:** Added `livekit-plugins-noise-cancellation` (BVC — Background Voice Cancellation) to the audio pipeline. BVC runs on the incoming SIP audio and suppresses the phone echo before it reaches the VAD, so the echo can no longer trigger barge-in.

```python
# requirements.txt
livekit-plugins-noise-cancellation>=0.2.0

# agent.py — session.start()
from livekit.agents import RoomInputOptions
from livekit.plugins import noise_cancellation

await session.start(
    room=ctx.room,
    agent=priya,
    room_input_options=RoomInputOptions(
        noise_cancellation=noise_cancellation.BVC(),
    ),
)
```

**Remaining issue:** If Priya is still cutting mid-sentence after BVC, it means the phone's echo level exceeds what BVC can suppress (e.g. speakerphone at high volume). Workaround: ask the customer to use earphones, or disable barge-in entirely by increasing `silence_duration_ms` further.

### Problem 7 — 10–18 second cold start on first response
Already noted in Step 9. Not fixable without pre-warming the Gemini Live connection before the customer joins.

**Workaround for phone mode:** Add a brief hold music track or a "Connecting you to Priya..." IVR message during the cold-start window so the customer doesn't hear 15 seconds of silence and hang up.

---

## Files and Their Roles

| File | Role |
|---|---|
| `main.py` | FastAPI server: webhook receiver, priority queue enqueue, queue_worker dispatcher, SIP dial, retry orchestration, dashboard API |
| `agent.py` | LiveKit agent worker: conversation logic, system prompt, function tools, latency tracking, post-call logging |
| `post_call.py` | SQLite CRM + priority queue: `call_logs` table, `call_queue` table, `log_call()`, `enqueue_call()`, `dequeue_next_call()`, `get_metrics()` |
| `log_setup.py` | Dual logging (console + rotating file), `write_call_summary()` for per-call `.log` files |
| `sms.py` | SMS dispatch: MSG91 (production) → Twilio (demo) → mock |
| `retry.py` | Retry constants and `schedule_retry()` which POSTs to the FastAPI server's retry endpoint |
| `mock_data.py` | Hardcoded cart for `python agent.py dev` without a webhook |
| `dashboard.html` | Single-page dev UI: dispatch form, result display with join token, recent calls table |
| `load_test.py` | Concurrent webhook load tester: fires N webhooks simultaneously, prints priority order, room token curl commands, and live /metrics snapshot |

---

## Session Changes — 2026-03-27

### Conversation quality fixes (agent.py — system prompt)
1. **Noise/confusion recovery** (Tone Rules): Priya says "Sorry, kya aap mujhse baat kar rahe the?" on unrelated words/names/digits instead of acting on them. If no response follows, ends gracefully.
2. **Discount hold-back** (`apply_discount` tool description): Discount only offered on 2nd price mention OR explicit phrases ("bahut mehnga hai", "afford nahi hoga", etc.). First hesitation → acknowledge price and listen.
3. **`send_booking_link` dedup** (tool docstring): Explicitly tells model not to call `send_booking_link` if `apply_discount` already ran — prevents double-SMS and duplicate link announcements. (Caught from CART-LOAD-00003 where both tools fired.)

### VAD tuning (agent.py — RealtimeModel)
Added `realtime_input_config` with:
- `START_SENSITIVITY_LOW` — requires more sustained speech to begin a turn
- `END_SENSITIVITY_LOW` + `silence_duration_ms=500` — waits 500ms before treating silence as end-of-speech
- `prefix_padding_ms=20`

### Noise cancellation (agent.py — session.start)
- `noise_cancellation.BVC()` via `RoomInputOptions` added to the audio pipeline
- Suppresses phone SIP echo before it reaches the VAD, reducing self-interruption

### Participant timeout (agent.py — entrypoint)
- `ctx.wait_for_participant()` now wrapped in `asyncio.wait_for(timeout=60)`
- If no one joins within 60s, logs `DISPOSITION_NO_ANSWER` to SQLite and exits cleanly instead of throwing `RuntimeError: room disconnected while waiting for participant`

### Latency measurement fix (agent.py — latency tracking)
- Added `LATENCY_CAP_MS = 15_000` — outlier turns > 15s are logged as skipped and excluded from averages
- Root cause: after tool fires and Priya says goodbye, customer background noise leaves `_user_stopped_at` set; next Priya speech registers as 20s+ turn. Cap eliminates this from summaries.

### Priority queue (post_call.py + main.py) — PRD §5.5
**`post_call.py`:**
- `call_queue` table: `cart_id UNIQUE`, `cart_value REAL`, `status` (pending/in_progress/done/failed), `scheduled_at` (UTC for calling-hours scheduling)
- Index: `(status, cart_value DESC, scheduled_at)` for fast priority reads
- `enqueue_call()` — upserts via `ON CONFLICT(cart_id) DO UPDATE` so retries reset the row to 'pending'
- `dequeue_next_call()` — atomically marks row 'in_progress' before returning (prevents double-dispatch)
- `mark_queue_done()` / `mark_queue_failed()`

**`main.py`:**
- Webhook no longer dispatches directly — always enqueues
- Outside calling hours: `scheduled_at = next_calling_window()` (next 9 AM IST as UTC) instead of dropping the call
- `queue_worker()` background task: polls every 10s, dispatches highest `cart_value` pending call
- `_dispatch_and_dial()`: extracted dispatch + SIP-dial logic; marks queue done/failed
- Queue worker started in `lifespan`, cancelled on shutdown

**Load test results (5 concurrent, 2026-03-27 11:04 IST):**
- 5/5 webhooks accepted in 5–7ms each (wall clock 10ms total) — SQLite enqueue is essentially free
- Dispatch order: ₹4998 → ₹4498 → ₹3998 → ₹3498 → ₹2998 — PRD §5.5 satisfied
- 3 simultaneous Gemini Live sessions with no quota errors confirmed
- `[QUEUE]` logs go to `webhook.log`, not `agent.log`

### Load test script (load_test.py)
- `python load_test.py 5` — fires N concurrent webhooks, prints priority table + room token curl commands + live /metrics snapshot
- `--stagger 0.5` flag for staggered testing
- Correct log tail command: `tail -f logs/webhook.log | grep '\[QUEUE\]'`

### Known remaining issues
| Issue | Status |
|---|---|
| First response cold start 7–15s | Not fixed — Gemini Live WebSocket cold-start. Workaround: IVR hold message for phone mode |
| Twilio SMS 400 on test phone numbers | Expected — free trial only sends to verified numbers. MSG91 is production path |
| `TRANSFERRED`, `CALLBACK`, `BOOKED` in metrics | Legacy disposition strings from calls before schema migration. Not harmful — just old data |
| `send_booking_link` fires after `apply_discount` | Fixed in docstring; model should not call both now |
| `load_test.py` | Generic concurrent load tester: configurable N webhooks with varied cart values, measures queue acceptance rate |

---

## Known Limitations (Production Gaps)

| Issue | Current | Production Fix |
|---|---|---|
| DND list | Hardcoded set | TRAI NDNC API with Redis cache |
| SMS template | Twilio free trial | MSG91 with DLT-approved template |
| Retry timer | 30 seconds | 7200 seconds (2 hours) |
| Database | SQLite | PostgreSQL |
| CRM integration | SQLite mock | Zoho CRM API (`log_to_zoho()`) |
| `schedule_callback` | Log only | DynamoDB / Step Functions scheduler |
| `mark_not_interested` | Log only | CRM do-not-call flag |
| `apply_discount` | Log only | Imagicaa booking API promo code |
| Webhook auth | None | HMAC-SHA256 signature header |
| Calling hours queue | Enqueue for next 9 AM window | ✅ Fixed — `scheduled_at` stored in queue, worker skips until window opens |
| Cold-start silence | 10–18s of dead air | Pre-warm connection or IVR hold message |
| Barge-in from SIP echo | Priya cuts mid-sentence on phone calls | ✅ Fixed — BVC noise cancellation on incoming SIP audio |
| SIP trial accounts | Can only dial verified caller IDs | Upgrade Twilio account or verify each number in console |
| Concurrent calls — agent crash on no-join | `RuntimeError: room disconnected` if SIP fails | ✅ Fixed — `asyncio.wait_for(wait_for_participant(), timeout=60)` exits cleanly |

---

## Concurrent Call Testing

`concurrent_test.py` fires 5 simultaneous webhooks with the following fixed phone numbers and cart values:

| Priority | Customer | Phone | Cart Value |
|---|---|---|---|
| 1 (highest) | Bhavesh | +919913874598 | ₹9,994 |
| 2 | Bhavya | +919510785512 | ₹6,996 |
| 3 | Vimal | +916353119347 | ₹4,997 |
| 4 | Arjav | +919875275353 | ₹2,998 |
| 5 (lowest) | Ratish | +918320999207 | ₹1,499 |

The queue worker dispatches in cart value order (highest first), one every 10 seconds.

```bash
python concurrent_test.py            # browser mode — no SIP dials
python concurrent_test.py --mode phone  # phone mode — real SIP calls
```

**SIP trial limitation:** Twilio trial accounts can only dial verified caller IDs. Unverified numbers get a 400 SIP error and the agent exits cleanly with `NO_ANSWER`. To test all 5 calls, verify all numbers in the Twilio console or upgrade to a paid account.
