# Session Context Dump — Kaya Clinic Voice Agent
_Last updated: 2026-06-04_

## What was built across sessions

Added a full second voice agent campaign (Kaya Clinic) to the existing Imagicaa multi-tenant architecture. Both agents share the same FastAPI server, Twilio number, and WebSocket bridge. Routing is determined by `agent_type` in the cart dict.

Across three sessions: debugged the full call path end-to-end, made multiple live test calls, improved prompt quality (Hinglish, phonetics, email handling, language mirroring, time slot acceptance, domain spelling), added backend email correction helpers, rebuilt both frontend pages per the DevX Doctrine design system, and added hosting guidance.

---

## Current state

### Server
- **Entry point:** `python main.py` (port 8000)
- **Tunnel:** `ngrok http --url=redressable-spectrochemical-aarav.ngrok-free.dev 8000` (must run alongside server)
- **Status:** ✅ Fully working — multiple live test calls completed, bookings saved to DB

### Calling hours
- `CALLING_HOURS_START = 9` (9 AM IST)
- `CALLING_HOURS_END = 23` ← **DEV ONLY** — revert to `21` before production

### Call flow — verified working
```
POST /webhook/kaya-lead
  → enqueue_call(agent_type="kaya", cart_value=0)
    → queue worker picks up in ~10s
      → _dispatch_and_dial builds kaya cart dict (branches on agent_type)
        → cart_sessions[cart_id] = cart
        → dial_customer → twilio_client.calls.create(url=answer_url?cart_id=X&agent_type=kaya)
          → Twilio dials customer
            → customer answers → AMD fires
              → POST /twilio/answer?cart_id=X&agent_type=kaya
                → call_sessions[callSid] = cart_id
                → returns <Stream url="wss://ngrok/twilio/media-stream"> TwiML
                  → Twilio opens WebSocket (no query params)
                    → server accept() first → reads until "start" event → gets callSid
                    → looks up cart via call_sessions[callSid] → cart_sessions[cart_id]
                      → media_stream_handler(agent_type="kaya", preread=preread)
                        → sends session_init with dynamic_variables + pcm_16000 format
                        → connects to ELEVENLABS_KAYA_AGENT_ID
                        → audio bridge runs
                          → tool calls: get_closest_branches, book_appointment, end_call
                            → email: _normalize_email → _fuzzy_correct_email before saving
                            → post_call: log_call + log_kaya_booking (if CONVERTED)
```

---

## Bugs fixed across sessions

### 1. WebSocket 403 — Twilio strips query params from WebSocket URL
**Fix:** WebSocket handler accepts first, reads until "start" event, looks up cart via `call_sessions[callSid]`.

### 2. ElevenLabs 1008 — `first_message` / `prompt` override not allowed
**Fix:** Removed both from `session_init`. Managed entirely in ElevenLabs dashboard.

### 3. ElevenLabs 1008 — missing required dynamic variable
**Fix:** `session_init` sends `dynamic_variables`: `customer_phone`, `customer_name`, `city`, `call_type`.

### 4. Retry firing on answered calls
**Fix:** `_post_call()` upgrades `NO_ANSWER → CALL_COMPLETED_NO_OUTCOME` if `duration > 60s`. Retry only fires if `duration_sec <= 60`.

### 5. `/webhook/call-ended` returning 404
**Fix:** Added `POST /webhook/call-ended` endpoint — logs event, returns 204.

### 6. `/kaya` route 404 after latest commit
**Fix:** Restored `GET /kaya`, `POST /webhook/kaya-lead`, `POST /webhook/call-ended`, and `KayaLeadPayload` model that were dropped.

### 7. `_dispatch_and_dial` KeyError on `visit_date`
**Fix:** Added `agent_type` branching — kaya builds a slim cart dict, imagica uses the original.

### 8. WebSocket 403 (second occurrence) — `cart_id: str = Query(...)` rejected
**Fix:** Restored accept-first / callSid-lookup pattern (lost in a commit). Also fixed `dial_customer` to include `agent_type` in the answer URL, and `twilio_answer` to accept `agent_type` query param.

### 9. Fuzzy email corrector over-correcting digits
**Fix:** Lowered Levenshtein threshold from 3 → 2. `bhavesh369tank` (3 deletions) no longer stripped to `bhaveshtank`.

### 10. `[Warmly]`, `[Patiently]` spoken aloud by agent
**Fix:** Added guardrail to prompt: bracketed emotion/tone labels must never be spoken.

### 11. Agent switching to Hinglish for English-speaking customers
**Fix:** Language rule changed — mirror the customer's language; Hinglish only triggered by customer using Hindi.

### 12. Agent offering 7:00/7:30 alternatives when customer said "7 o'clock"
**Fix:** Step 5 updated — accept exact hours/half-hours directly; only offer alternatives for off-boundary times.

### 13. Voicemail (AMD machine_start) not retried
**Note:** AMD `machine_start` was Twilio's trial-plan message, not a real customer voicemail. Voicemail retry logic was added and then removed. Retry fires only on `no-answer` or `busy`.

### 14. Relative dates ("this Saturday") unresolvable (2026-06-03 Gladston call)
**Fix:** Added `{{system__time}}` to prompt Context — it's an ElevenLabs **built-in system variable** (outputs "Wednesday, 11:45 3 June 2026" style, localized via agent timezone), auto-provided, needs NO allowlist entry and NO session_init change. Step 5 now resolves relative dates against it, confirms the computed calendar date back, and handles Sunday (closed) → offer Saturday/Monday.

### 15. Agent not switching to Hinglish despite full-Hindi sentences (Gladston call)
**Fix:** Old Language rule was self-defeating — "never impose a language unprompted" + "do NOT switch on your own" suppressed the one-Hindi-word trigger, so Priya stayed English until explicitly asked. Rewritten: ANY Hindi (word, phrase, or Devanagari sentence) → switch to Hinglish from the very next reply, automatically and silently.

### 16. Dead-air pauses after Step 2 anchor and Step 6 fee (Gladston call)
**Fix:** New Tone rule — never end a reply on a bare statement that invites no response. Step 2 anchor and Step 6 fee now fold into the next question in the same reply (Step 7 no longer re-asks for email; fee line had even repeated itself on the call).

### 17. `book_appointment` called with mis-heard ASR email, not the spelled-confirmed one (Gladston call)
Customer spelled S-E-Q-U-I-R-A, Priya read back the correct version, but the tool received `gladstonsquare-98@gmail.com` — booking id=4 saved with a wrong email. Backend fuzzy correction (Levenshtein ≤ 2) cannot catch a gap that large.
**Fix:** Step 7 + tool spec rule — pass the spelled-and-confirmed value to `book_appointment`; spelled letters always win over the first transcription. Email param: normalize "at"→@, "dot"→., lowercase, no spaces.

---

## Files modified

### `main.py`
- WebSocket handler: accept-first → read until `"start"` event → lookup cart via `call_sessions[callSid]`
- `dial_customer`: stores cart in `cart_sessions`, includes `agent_type` in answer URL
- `twilio_answer`: accepts `agent_type` query param, logs it
- `_dispatch_and_dial`: branches on `agent_type` (kaya vs imagica cart dict)
- `KayaLeadPayload` model added (fields: `customer_name`, `customer_phone`, `cart_id`, `call_type`, `attempt_number`, `city`)
- `POST /webhook/kaya-lead`: enqueues Kaya calls
- `POST /webhook/call-ended`: ElevenLabs post-call webhook
- `GET /kaya/appointments`, `GET /kaya/transcripts`: frontend pages
- `GET /api/kaya/appointments`: returns all kaya_bookings as JSON
- `GET /api/kaya/transcripts`: returns kaya call list as JSON
- `GET /api/kaya/transcripts/{id}`: returns full transcript + metadata for one call
- `CALLING_HOURS_END = 23` (dev — revert to 21 for production)
- Retry: fires on `no-answer` or `busy` only (voicemail retry removed)

### `voice_agent.py`
- `media_stream_handler`: removed `websocket.accept()`, added `preread` param, replays preread messages
- `session_init`: `tts.output_format` + `dynamic_variables` only (`customer_phone`, `customer_name`, `city`, `call_type`)
- `_post_call`: upgrades `NO_ANSWER → CALL_COMPLETED_NO_OUTCOME` if `duration > 60s`
- Added `_normalize_email()`: fixes spoken domain patterns (`atgmail` → `@gmail`, `dotcom` → `.com`)
- Added `_levenshtein()`: proper Wagner-Fischer edit distance
- Added `_fuzzy_correct_email()`: corrects ASR letter substitutions if edit dist ≤ 2
- ASR keyword boosting (STT biasing): gated behind `KAYA_ASR_KEYWORDS=1` env flag — **default OFF, working pipeline untouched**. When on, sends `conversation_config_override.asr.keywords` (field path verified against installed `elevenlabs` SDK v2.50.0: `List[str]`, "keywords to boost prediction probability for"). List is proper nouns only (Kaya, Priya, branch/city names) — a static list cannot fix per-call unknowns like surnames/emails (that's what spell-back is for). Requires the `asr.keywords` override toggle in dashboard Security/Overrides FIRST, else session init dies with 1008 on every call.

### `kaya_prompt.py`
- **Personality**: added ASR-awareness note — silently correct obvious mis-transcriptions from context; confirm critical data by read-back
- **Context**: added `{{system__time}}` dynamic variable for current date/time (Asia/Kolkata)
- **Tone**: added dead-air rule — never end a reply on a bare statement; always pair info with the next question
- **Language**: English-only customers get English; ANY Hindi (word, phrase, or Devanagari sentence) → switch to Hinglish from the very next reply, automatic and silent — never wait to be asked, never announce. (Old "never impose unprompted" framing removed — it was suppressing the switch, see bug #15)
- **Guardrails**: explicit ban on `[Warmly]`, `[Patiently]`, `[Empathetically]`, `[confident]` being spoken
- **Step 2**: concern acknowledgement + next question in the same reply — no standalone anchor turn
- **Step 5**: resolves relative dates (`today`, `tomorrow`, `this Saturday`, Hinglish relative days) against `{{system__time}}` without asking customer; accepts exact hours/half-hours directly; alternatives only for off-boundary times; Sunday handling
- **Step 6**: fee + email ask in one reply — no dead air between fee and next question
- **Step 7**: phonetics only for ambiguous letter clusters; non-standard domains spelled letter by letter; corrections re-read only the changed segment; max 2 passes; email passed to `book_appointment` MUST be the spelled-and-confirmed version — spelled letters always win over the first transcription
- **Tools / book_appointment**: email param — normalize spoken "at"→@, "dot"→., lowercase, no spaces
- **Step 8**: pincode fallback — use area name as branch_name if customer knows area but not pincode; never end call for missing pincode; referral question in English or Hinglish depending on language mode
- Linter repeatedly adds blank lines — file is the source of truth; paste `_TEMPLATE` into dashboard as-is

### `.env`
- `ELEVENLABS_KAYA_AGENT_ID=agent_6701kspntnxqebat1ywc9qw3spxt`

### New files created
| File | Purpose |
|---|---|
| `kaya_appointments.html` | Appointments dashboard — stat row + table, rebuilt per DevX Doctrine |
| `kaya_transcripts.html` | Transcript viewer — split pane, call list + chat bubbles, rebuilt per DevX Doctrine |
| `kaya_branches.py` | City/pincode → branch lookup (used by `get_closest_branches` tool) |
| `kaya_prompt.py` | Kaya agent system prompt (`_TEMPLATE` to paste into ElevenLabs dashboard) |
| `kaya_kb_branches.txt` | Branch names per city, Confirm Address cities, fees, rules (KB: prompt mode) |
| `kaya_kb_services.txt` | All skin/hair/body treatments + concern→treatment guide (KB: auto) |
| `kaya_kb_general.txt` | Company info, products, FAQs, contact details (KB: auto) |
| `devx-doctrine (2).md` | DevX Doctrine design system — reference for all frontend work |

### UI design system
Both HTML pages follow the **DevX Doctrine** (`devx-doctrine (2).md`):
- Fonts: Inter Tight (UI), Source Serif 4 italic (headline emphasis), JetBrains Mono (labels/eyebrows/metadata)
- Colors: ink-on-paper palette, single accent `#1E6FFF`, no teal/gradients/shadows
- Containers: `1px solid #E5E5E5`, sharp corners (`border-radius: 0`), no shadows
- Stat row: 4 large numerics under 1px ink top rule with hairline vertical dividers
- Disposition tags: monospace, borderlined, color-coded with `--ok`/`--warn`/`--muted`

---

## ElevenLabs Kaya Agent config

- **Agent ID:** `agent_6701kspntnxqebat1ywc9qw3spxt`

### Dashboard settings
- System prompt: paste `_TEMPLATE` from `kaya_prompt.py`
- Knowledge base: upload the 3 KB files with usage modes as above
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
ELEVENLABS_KAYA_AGENT_ID=agent_6701kspntnxqebat1ywc9qw3spxt  # Kaya
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

Pages:
- `http://localhost:8000/kaya` — request a call form
- `http://localhost:8000/kaya/appointments` — appointments dashboard
- `http://localhost:8000/kaya/transcripts` — call transcript viewer

---

## Known issues / TODOs

### Before production
- [ ] `CALLING_HOURS_END = 23` — revert to `21`
- [ ] `RETRY_DELAY_SECONDS=30` in `retry.py` — change to `7200`
- [ ] DND list is hardcoded — needs live registry API
- [ ] Kaya SMS confirmation not implemented (`sms.py` still sends Imagicaa booking link)
- [ ] ElevenLabs quota exhausted — upgrade plan
- [ ] SQLite → Postgres before any cloud deploy (Railway/Render filesystem is ephemeral)

### ElevenLabs dashboard (manual steps pending)
- [ ] **Re-paste latest `_TEMPLATE` from `kaya_prompt.py` into system prompt — HIGHEST PRIORITY.** All prompt fixes (bugs 14–17, STT framing) are inert until this is done. Evidence from the 2026-06-03 Gladston call suggests the deployed prompt is STALE — already-"fixed" bugs #10 (bracketed tone labels) and #11 (language mirroring) both regressed on that call. Diff dashboard vs file while in there.
- [ ] Upload `kaya_kb_branches.txt` (prompt mode), `kaya_kb_services.txt` (auto), `kaya_kb_general.txt` (auto)
- [ ] Configure RAG settings (see table above)
- [ ] `{{system__time}}` needs NO dynamic-variable registration — it's a built-in system variable, auto-provided. Just verify the agent timezone is set to Asia/Kolkata so it renders IST.
- [ ] Update `book_appointment` tool — mark `dob` and `city` as required (currently optional)
- [ ] Mirror normalization hints in `book_appointment` **param descriptions** (email: lowercase/normalized spelled-confirmed value; pincode: digits only; dates: YYYY-MM-DD) — tool args are biased mainly by the dashboard schemas, not the prompt's `# Tools` section
- [ ] (Optional, for ASR keyword boosting) Enable the `asr.keywords` override toggle in Security/Overrides → set `KAYA_ASR_KEYWORDS=1` in `.env` → verify on ONE test call before relying on it. Do NOT set the flag before the toggle: session init will 1008 on every call.

### Hosting
- Pages are live at ngrok URL while server + tunnel are running
- For permanent hosting: Railway is fastest (connect GitHub repo, add env vars, ~15 min)
- SQLite must be migrated to Postgres or a mounted volume before Railway deploy
