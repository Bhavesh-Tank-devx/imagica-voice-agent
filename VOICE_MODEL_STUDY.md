# Voice Model Study — ElevenLabs vs. Alternatives

_A general, reusable reference for choosing a voice-AI stack for future projects · Compiled 2026-06-03_

This is an **architect's / buyer's reference** for building voice agents — not scoped to any single product. It has three parts:
1. **Context** — the voice-agent pipeline, the layers you choose at, and the use-case categories that drive the decision.
2. **Metric framework** — what to measure, split into vendor-comparable metrics vs. deployment-dependent outcome metrics (and why that distinction matters).
3. **Vendor comparison study** — per-layer, sourced comparison tables and a ranked recommendation per use-case category. (Populated by a fact-checked research pass; every quantitative cell is sourced or marked "not published".)

---

## Part 1 — Context: the voice-agent pipeline & use cases

### The pipeline you're choosing components for
A real-time voice agent is a loop of distinct layers. You can buy them **bundled** (one platform does everything) or **assemble best-of-breed** (pick the strongest option per layer and orchestrate them yourself).

```
caller audio ──► [STT/ASR] ──► [LLM + tools + RAG] ──► [TTS] ──► agent audio
       ▲                                                            │
       └──────────── turn-taking / VAD / barge-in ◄────────────────┘
                     (the ORCHESTRATOR / PLATFORM ties it together
                      + telephony, dashboards, post-call webhooks)

  Alternative: an END-TO-END REALTIME SPEECH MODEL collapses STT+LLM+TTS
  into one model (e.g. OpenAI Realtime, Gemini Live, AWS Nova Sonic).
```

| Layer | What it does | Why the choice matters |
|---|---|---|
| **STT / ASR** | Transcribe caller speech | Accent/language WER, latency, streaming, telephony (8kHz) robustness |
| **LLM** | Reason, call tools, ground on RAG, persona | Tool-calling reliability, multilingual reasoning, latency, cost |
| **TTS** | Speak the response | Naturalness/MOS, prosody, latency (TTFB), voice cloning, languages |
| **Realtime speech model** | STT+LLM+TTS in one | Lowest latency + most natural turn-taking; less control per-layer |
| **Orchestrator / Platform** | Turn-taking, telephony, tools, RAG, analytics | Time-to-ship vs. control; managed black box vs. self-hosted |

### Use-case categories — the decision differs by category
There is no single "best" voice stack; the right answer depends on the use case. This study gives a recommendation **per category**:

| # | Category | What dominates the decision |
|---|---|---|
| **A** | **Real-time phone agents** (inbound + outbound, Twilio/SIP, µ-law 8kHz) | End-to-end latency, barge-in, AMD, **cost/min** at scale, telephony integration |
| **B** | **Real-time web/app assistants** (WebRTC, browser/mobile) | Latency + naturalness; no PSTN constraints |
| **C** | **Multilingual / Indian-language agents** (Hindi, Hinglish code-switching, Indic languages) | **Language coverage + accent WER**; may force India-specific providers |
| **D** | **Async / non-realtime** (voiceover, IVR prompts, audiobooks, dubbing) | **Naturalness/MOS + cost**; latency irrelevant |

### The core architectural decision: bundle vs. best-of-breed
- **Bundled platform** (ElevenLabs Conversational AI, Vapi, Retell, Bland): fastest to ship, managed turn-taking/telephony, less per-layer control, can be costlier and harder to tune for edge languages.
- **Best-of-breed assembled** (e.g. Deepgram STT + an LLM + Cartesia TTS, orchestrated on LiveKit Agents or Pipecat): maximum control over latency, cost, and per-layer quality (e.g. swap in an Indian-language STT), at the cost of more engineering.
- **End-to-end realtime model**: lowest latency and most natural, but youngest tooling and least per-layer control.

The study's recommendation section maps each use-case category to one of these patterns with evidence.

---

## Part 2 — Metric Framework

The requested metrics fall into **two fundamentally different classes**. Conflating them produces tables of invented numbers — so they are handled separately.

### Class A — Vendor-comparable (intrinsic) metrics
Properties of the model/provider; comparable across vendors **with a source**.

| Metric | Definition | Reporting notes |
|---|---|---|
| **WER** (Word Error Rate) | ASR errors / words | Report **per language/accent** (English, Hindi, Indian-English, multilingual). Indian-English/Hindi WER ≫ US-English WER. Cite dataset + benchmark. |
| **TTFT / TTFB** | Time-to-First-Token / -Byte of the response | The latency the user actually feels first. Separate TTS TTFB from end-to-end. |
| **End-to-end turn latency** | User stops speaking → agent audio starts | Sum of VAD + STT + LLM + TTS + network. < ~800ms feels natural on a call. |
| **Barge-in / interruption** | Agent stops when interrupted; recovery | Supported / not + quality. |
| **Cost** | $/min, or per-1M chars (TTS) / per-1M tokens (LLM) / per-hour (STT) | Separate platform fee vs LLM passthrough vs telephony. |
| **MOS** (Mean Opinion Score) | Perceived naturalness, 1–5 | Rarely published comparably per-provider — **expect gaps, mark N/A**. |
| **Speech naturalness / prosody** | Expressiveness, code-switch handling | Qualitative where no MOS. |
| **Language coverage** | Supported languages incl. Indic | Hard filter for Category C. |
| **Voice cloning** | Custom-voice support + data needed | Matters for branded voiceover (D). |
| **Tool calling / RAG / telephony / self-host** | Capability flags | Drive platform fit. |

### Class B — Deployment-dependent (outcome) metrics — **no vendor number exists**
**Task Completion Rate, Intent Recognition Accuracy, Average Handle Time (AHT), Silence Rate, overall Error Rate** are functions of **how the agent was built, prompted, and which tools it has** — not of the vendor. There is no vendor number for them; putting them in a vendor-comparison column = inventing data.

The valid way to measure these is from **your own call logs**, and the only valid way to compare vendors on them is an **A/B pilot**: run the same prompt/tools/script on two stacks over N matched calls and recompute. A typical call-log schema captures everything needed:

| Outcome metric | How to compute from call logs |
|---|---|
| **Task Completion Rate** | `# success-disposition calls ÷ # connected calls` |
| **Average Handle Time (AHT)** | mean call `duration` over connected calls |
| **Time to First Response** | logged per call (first-response ms) |
| **Per-turn latency** | logged latency array per call |
| **Intent Recognition Accuracy** | manual/LLM grade of transcript: right intent/tool routed? |
| **Silence Rate** | % of call with dead air above a threshold, from turn timestamps |
| **Error Rate** | failed tool calls + wrong-language/wrong-outcome turns ÷ turns |

> Bottom line: **benchmark Class A from sources; measure Class B from your own pilot.** This study fills Class A and gives the Class B methodology — it does not fabricate Class B vendor columns.

---

## Part 3 — Vendor Comparison Study

> **Methodology — read this before trusting any cell.** Compiled via a multi-agent research pass (June 2026): 5 parallel search angles → 26 source extractions → adversarial 3-vote verification. **The verification phase terminated early: only 14 of 26 extracted claim-sets were adversarially verified** (13 survived; 1 was killed and corrected below — the AssemblyAI multilingual WER ranking). The remaining figures are **single-source extractions, not adversarially confirmed**. The headline English STT table was additionally spot-checked against the live Artificial Analysis page on 2026-06-04 (Scribe v2 2.2%, AssemblyAI 3.1%, GPT-4o Transcribe 4.0%, Amazon 4.1%, Nova-3 5.2% all confirmed). **Labels:** `[I]` = independent benchmark · `[V]` = vendor-claimed · unlabeled qualitative = practitioner/blog reporting — labels describe the *source type*, not a verification guarantee. Where no figure exists, the cell says **not published** — nothing is interpolated. Pricing/latency figures are dated 2025–2026 and change fast — re-verify before contracting.

### 3.1 Layer 1 — STT / ASR

**English accuracy (independent — Artificial Analysis AA-WER, ~8h across AgentTalk/VoxPopuli/Earnings22; English-only benchmark):**

| Model | WER `[I]` | Price /1000 min | Streaming latency | Languages |
|---|---|---|---|---|
| **ElevenLabs Scribe v2 (Realtime)** | **2.2–2.3%** | $3.67 (~$0.28/hr) | ~150 ms `[V]` | 90 |
| Azure MAI-Transcribe-1.5 | 2.4% | — | not published | — |
| Gemini 3 Pro | ~2.9% | (Gemini 2.0 Flash Lite: $0.19 — cheapest) | not published | — |
| **AssemblyAI Universal-3 Pro** | 3.1–3.2% | $2.50–3.50 (~$0.15/hr) | ~300 ms `[V]` | 99+ (code-switching, diarization) |
| OpenAI GPT-4o Transcribe | 4.0% | $6.00 | not published | — |
| Amazon Transcribe | 4.1% | $24.00 | not published | — |
| Whisper Large v3 (Fireworks) | 4.6% | $1.00 | batch-oriented | ~100 |
| **Deepgram Nova-3 / Flux** | 5.2% | $4.30 ($0.0077/min) | **sub-300 ms; Flux ~260 ms with built-in end-of-turn detection** `[V]` | multilingual weaker (see below) |

**Vendor cross-bench (AssemblyAI's own page, Feb 2026 `[V]` — adversarially re-verified against the live page):** English WER — AssemblyAI 5.9%, Whisper 6.5%, ElevenLabs 6.5%, Azure 7.5%, Amazon 7.6%, Deepgram 8.1%. **Multilingual WER — Whisper 7.4% (best), ElevenLabs 8.1%, AssemblyAI 8.7%, Amazon 10.1%, Deepgram Nova-3 10.8%, Azure 11.1%.** _(An earlier extracted claim that "Deepgram leads multilingual at 6.8%" was refuted 3/3 on verification — misread of the source.)_

**Indian languages / Hinglish (the gap Western benchmarks don't cover — AA-WER is English-only):**

| Model | Indic performance | Latency | Price | Languages |
|---|---|---|---|---|
| **Sarvam Saaras V3** (Feb 2026) | **~19.3% WER on IndicVoices 10-language subset — beats Gemini 3 Pro, GPT-4o Transcribe, Nova-3, Scribe v2 on the same bench; lowest WER on Svarah (Indian-accented English)** `[V]` | streaming TTFT < 150 ms `[V]` | STT ₹30/hr ≈ **$0.006/min** `[V]` | 22 scheduled + English; **code-mixing (Hinglish) is a first-class architectural feature** |
| Sarvam Saarika v2.5 | WER not published | REST, <30 s clips (no live streaming on this endpoint) | per-minute `[V]` | 12 |
| AI4Bharat IndicASR (open source / Bhashini) | Hindi WER ~12–18% clean, **22–30% on telephony audio** | self-host-dependent | free / OSS; Bhashini API discounted commercial | 22 |
| Reverie / Krutrim | not published (no comparable public WER found in this pass) | not published | not published | Indic-focused |

**Takeaway:** ElevenLabs Scribe v2 and AssemblyAI lead independent *English* accuracy; Deepgram trades accuracy for the lowest-latency agent-native streaming (Flux). For *Indic + Hinglish*, Sarvam Saaras V3 is the standout claim — note ~19% WER on Indic speech vs ~2–5% English numbers tells you Indic ASR is still an order harder, and **telephony audio degrades Indic WER further** (AI4Bharat 22–30%).

### 3.2 Layer 2 — LLM (for tool-calling voice agents)

The LLM is usually **the largest line in the latency budget** (~400 ms of an ~800 ms target turn).

| Model | TTFT (independent bench, 2026) | Voice-agent fit |
|---|---|---|
| **Claude Haiku 4.5** | **597–639 ms, stable p95** `[I]` | Recommended fast tier |
| GPT-4.1 | < 900 ms short prompts `[I]` | **Most-used LLM on Retell (40M+ calls/mo): balance of latency, 1M context, reliable function calling** `[V]` |
| GPT-4.1 mini | > 2 s short prompts `[I]` (surprisingly worse than full 4.1) | Verify before assuming "mini = faster" |
| GPT-4o-mini | 150–250 ms first-token (practitioner stack report) | The 2026 best-of-breed default |
| Gemini 2.5/3.0 Flash | highest throughput ~173 tok/s `[I]`; tool calling "improved but still lags OpenAI/Anthropic for production voice" | Cheapest capable tier |
| Sarvam-M | not published | Indic-reasoning option (hosted via Together) |
| Krutrim LLM | not published | — |

**Tool-calling architecture finding:** cascaded STT→LLM→TTS pipelines reach **~98% tool-call reliability** (at 1.4–1.7 s median turn), vs **75–88%** for speech-to-speech models (sub-300–500 ms turns) — the central trade-off of layer 2 vs layer 4. Latency feel: **<500 ms natural · 500–800 ms workable · >800 ms awkward silence.**

### 3.3 Layer 3 — TTS

| Model | TTFB/latency | Price /1M chars | Languages | Cloning | Notes |
|---|---|---|---|---|---|
| **ElevenLabs Flash v2.5** | **~75 ms** `[V]` (model inference only) | $50–60 | 32 (Eleven v3: 70+) | yes (3–4k+ voices) | Quality/prosody leader; blind test 8.6/10 naturalness |
| **Cartesia Sonic-3** | **40 ms (Turbo) / 90 ms TTFA** `[V]` | $38 | sources conflict: 15+–40+ | from 3 s audio | Fastest credible TTFA |
| **Deepgram Aura-2** | 90 ms optimized / sub-200 ms `[V]` | $30 ($27 Growth) | mainly EN+ES | — | Cheapest of the fast tier |
| Rime (Mist v2 / Arcana) | sub-200 ms (sub-100 on-prem) `[V]` | $20 / $30 | — | — | On-prem option |
| OpenAI TTS (tts-1/HD) | ~200–320 ms; **no official latency spec** | $15 / $30 | 50+ | no | |
| Azure Neural | not published | $12–16 | **140+ langs, 400+ voices (incl. Indic)** | yes | Broadest coverage |
| Google Neural/WaveNet | ~200 ms TTFA | $12–16 ($4 standard) | 50+ | yes | |
| Amazon Polly | not published | $4 std / $16 neural | — | — | Cheapest at scale |
| **Sarvam Bulbul-V2** | **P90 0.398 s; claims ~2× faster than ElevenLabs** `[V]` | ₹15/10K chars ≈ **~$18/1M** | **11 Indian languages** | yes | **Native 8 kHz output — telephony-friendly**; pitch/pace/loudness controls |
| AI4Bharat IndicTTS (OSS) | self-host | free | 22 Indic | — | **MOS ~3.6–3.9** (one of the few published MOS figures) |
| Inworld TTS-2 | — | — | — | — | claims #1 on AA Realtime TTS Arena `[V]` (self-citation by Inworld) |

**MOS reality check:** comparable cross-vendor MOS is essentially **not published** in 2026 — the few public numbers (IndicTTS 3.6–3.9; NVIDIA PersonaPlex 3.90 vs Gemini Live 3.72 `[V]`) aren't from one common protocol. Use the AA Realtime TTS Arena + your own blind listening for naturalness ranking.

### 3.4 Layer 4 — End-to-end realtime speech models (S2S)

| Model | Latency | Cost | Tool calling | Telephony | Languages/notes |
|---|---|---|---|---|---|
| **OpenAI gpt-realtime** (GA Aug 2025) | ~200–500 ms; AWS's bench: 1.18 s perceived `[V-competitor]` | $32/1M audio-in ($0.40 cached) + $64/1M audio-out ≈ **$0.06/min in + $0.24/min out** `[V]` | **Best S2S: 66.5% ComplexFuncBench, 82.8% Big Bench Audio** `[V]`; async function calls; MCP | **native SIP** | Mid-sentence language switching; best barge-in reliability (practitioner head-to-head) |
| **Google Gemini Live** | 300–500 ms, TTFT ~960 ms | **~$0.018/min — cheapest, 7–12× cheaper at volume** | native function calling; tool calling lags OpenAI for production voice | via bridge | |
| **ElevenLabs Conversational AI** | 400–800 ms (it's a managed *pipeline*, not native S2S) | **$0.08–0.10/min after Feb 2026 ~50% price cut** | depends on chosen LLM | native + Twilio | **Best naturalness in Q1-2026 blind test: 8.6/10 vs OpenAI 7.8, Gemini 7.5** |
| **AWS Nova 2 Sonic** (Dec 2025) | claimed 1.09 s perceived (vs 1.18 OpenAI, 1.41 Gemini Flash 2.0) `[V]` | **~$0.017/min — cheapest with OpenAI-class features** | async tool calling; claims BFCL lead `[V]` | **direct: Twilio, Vonage, Amazon Connect, LiveKit, Pipecat** | **EN/FR/IT/DE/ES/PT + Hindi, voices code-switch mid-conversation**; barge-in; configurable VAD; HIPAA |
| NVIDIA PersonaPlex | full-duplex | free (OSS, self-host) | — | — | 100% interruption-handling success; MOS 3.90 `[V]` |

### 3.5 Layer 5 — Platforms / orchestrators

| Platform | Real cost/min | Latency | Open/self-host | Telephony | Notes |
|---|---|---|---|---|---|
| **ElevenLabs Conv AI 2.0** | $0.08–0.10 (post-Feb-2026 cut) | 400–800 ms | no | native + Twilio | Bundled STT+LLM+TTS+RAG+dashboard; fastest to ship with premium voice |
| **Vapi** | headline $0.05 orchestration; **real-world BYOK $0.23–0.33** (modeled decomposition $0.144) | reports conflict: ~420 ms … ~1450 ms w/ default VAD | no | Twilio/Vonage first-class | The canonical "headline price ≠ real price" case |
| **Retell AI** | $0.07–0.31 component-billed | ~280–500 ms RT (+50–100 ms platform overhead) | no | yes | 40M+ calls/mo scale reference |
| **Bland AI** | $0.11–0.14 all-in bundled | not published | no | bundled | Simplest pricing |
| Telnyx Voice AI | $0.05–0.08 bundled | not published | no | own carrier network | |
| Deepgram / AssemblyAI Voice Agent APIs | $0.050–0.163 / $4.50/hr flat (~$0.075/min) | low (vendor STT-native) | no | via integration | |
| **LiveKit Agents** | **Cloud $0.01/min + components at cost** | ~sub-1 s achievable; turn-detection model 86% precision / 100% recall `[V]` | **Apache-2.0, fully self-hostable** | **SIP GA (2025): PSTN in/out, DTMF, REFER, transfer** | Plugin model for every STT/LLM/TTS |
| **Pipecat** | **free (BSD-2) + components; Pipecat Cloud ~$0.01/min** | ~300 ms endpointing default | **yes** | via Daily/Twilio | |
| Sarvam stack / Bhashini | components (₹-priced) | not published e2e | Bhashini/AI4Bharat OSS | assemble yourself | The Indic-first path |

**Cost anatomy (per-minute, practitioner-published ranges):** STT $0.0015–0.024 · TTS $0.005–0.048 · LLM $0.005–0.05 (×~1.8 reality factor for context growth/interrupts/tool calls) · telephony $0.005–0.025 · platform $0.01–0.05. **Assembled stacks land $0.05–0.10/min; premium managed $0.30+.** Reference 2026 best-of-breed build: Deepgram Nova-3 + GPT-4o-mini + Cartesia Sonic-3 on LiveKit ≈ **$0.083/min**.

**Latency budget (target ~800 ms):** VAD ~50 + STT ~150 + LLM TTFT ~400 + TTS ~150 + network ~50. Industry production median is actually **1.4–1.7 s (P99 3–5 s)** — sub-500 ms requires disciplined streaming end-to-end. >3 s feels broken.

**Outbound telephony specifics (Twilio):** AMD claims 94% accuracy (US/CA) `[V]`, configurable `MachineDetectionTimeout` 3–59 s (default 30 s), sync vs async modes, `DetectMessageEnd` for voicemail-beep drops; **AMD is unavailable on Elastic SIP Trunking** (bypasses Programmable Voice) — a real constraint when choosing SIP-first platforms.

---

### 3.6 Recommendations per use-case category

**A — Real-time phone agents (inbound/outbound, Twilio/SIP):**
1. **>10K min/month + engineering team → best-of-breed on LiveKit Agents (or Pipecat):** Deepgram Flux (end-of-turn-native) or Scribe v2 Realtime + GPT-4.1/4o-mini-class LLM + Cartesia Sonic-3 or Aura-2 ≈ $0.05–0.10/min — undercuts managed platforms 60–80% at scale, full control of AMD/retry/latency.
2. **<10K min/month or <1-month timeline → managed:** ElevenLabs Conv AI ($0.08–0.10/min post-cut, best voice) or Retell ($0.07+, proven at 40M calls/mo). Watch Vapi's real-world $0.23–0.33/min.
3. Dark horse: **AWS Nova 2 Sonic** (~$0.017/min, direct Twilio/LiveKit/Pipecat integration) if S2S tool-call reliability (75–88%) is acceptable for your flows.

**B — Real-time web/app assistants (WebRTC):**
1. **OpenAI gpt-realtime** — fastest first audio, most reliable barge-in, best S2S function calling (66.5%).
2. **LiveKit (WebRTC-native) pipeline** when you need control or custom components.
3. **ElevenLabs Conv AI** when brand voice quality is the differentiator (8.6/10 blind-test naturalness).
4. Cost-sensitive high-volume: **Gemini Live** (~$0.018/min, 7–12× cheaper).

**C — Multilingual / Indian-language agents (Hindi, Hinglish, Indic):**
1. **Sarvam Saaras V3 STT is the headline finding** — claims to beat Gemini 3 Pro, GPT-4o Transcribe, Nova-3 and Scribe v2 on IndicVoices and Svarah, with Hinglish code-mixing as a first-class feature, <150 ms streaming TTFT, at ~$0.006/min. Pair with **Bulbul-V2 TTS** (11 Indic languages, native 8 kHz telephony output, ~$18/1M chars) on a LiveKit/Pipecat pipeline.
2. **AWS Nova 2 Sonic** is the only major S2S model with Hindi + mid-conversation code-switching voices — the bundled alternative.
3. ElevenLabs covers Hindi in TTS (v3, 70+ langs) and Scribe narrative, but publishes **no Indic WER**; treat Hinglish ASR quality as unverified until piloted.
4. Sovereignty/self-host: **AI4Bharat/Bhashini** — but Hindi telephony WER of 22–30% means human-review workflows, not autonomous booking agents. Reverie/Krutrim: no comparable public benchmarks found — demand WER evidence before adoption.
5. **Caveat:** all Indic WER figures here are vendor-claimed; no Artificial-Analysis-style independent Indic benchmark exists yet. Pilot before committing.

**D — Async voiceover / IVR prompts / audiobooks / dubbing:**
1. Quality ceiling: **ElevenLabs v3** (70+ languages, cloning, top blind-test prosody) — premium $50–60/1M chars.
2. Volume economics: **Azure/Google Neural $12–16/1M** (Azure: 140+ languages incl. Indic) or **Polly $4/1M standard**.
3. Indic voiceover: **Bulbul-V2** (~$18/1M) or free **IndicTTS** (MOS 3.6–3.9).
4. Latency is irrelevant here — never pay the realtime premium (Flash/Sonic tiers) for batch work.

### 3.7 Decision framework

```
START: What are you building?
│
├─ Async content (D)? → Pick TTS only, by quality-vs-$/1M chars. Done.
│
├─ Indic/Hinglish required (C)?
│    ├─ Yes, and quality matters → Sarvam STT (+Bulbul TTS) in a
│    │   LiveKit/Pipecat pipeline, or Nova 2 Sonic (bundled S2S w/ Hindi).
│    │   Run a WER pilot on YOUR telephony audio first.
│    └─ Mild (Hindi TTS only) → ElevenLabs/Azure cover it.
│
├─ Volume < 10K min/month OR ship < 1 month?
│    → Managed bundle: ElevenLabs Conv AI (voice quality) /
│      Retell (phone ops) / Bland (simple pricing).
│      Budget the REAL per-minute (incl. passthrough), not the headline.
│
├─ Volume > 10K min/month + engineering team?
│    → Best-of-breed on LiveKit Agents or Pipecat (~$0.05–0.10/min,
│      60–80% cheaper at scale, self-hostable).
│
└─ Complex mid-call tool flows?
     → Prefer cascaded pipeline (~98% tool reliability) over S2S (75–88%),
       unless sub-500ms latency is worth the tool-call risk
       (then gpt-realtime, which leads S2S function calling).
```

### 3.8 What this study deliberately does NOT score (Class B)
Task Completion Rate, Intent Recognition Accuracy, AHT, Silence Rate, and overall Error Rate appear in no vendor column above because **no vendor number exists** — they are properties of your prompt, tools, and call flow. Measure them from your own call logs (Part 2 table) and compare vendors only via a matched A/B pilot: same prompt/tools/script, N calls per stack, recompute the Part 2 metrics.

---

## Sources

**Independent benchmarks:** [Artificial Analysis STT leaderboard](https://artificialanalysis.ai/speech-to-text) · [LLM API latency benchmarks 2026](https://www.kunalganglani.com/blog/llm-api-latency-benchmarks-2026)
**Primary vendor docs/announcements:** [OpenAI gpt-realtime](https://openai.com/index/introducing-gpt-realtime/) · [AWS Nova 2 Sonic](https://aws.amazon.com/blogs/aws/introducing-amazon-nova-2-sonic-next-generation-speech-to-speech-model-for-conversational-ai/) · [LiveKit Agents docs](https://docs.livekit.io/agents/) · [Twilio AMD docs](https://www.twilio.com/docs/voice/answering-machine-detection) · [AssemblyAI benchmarks](https://www.assemblyai.com/benchmarks) · [Sarvam Saarika docs](https://docs.sarvam.ai/api-reference-docs/models/saarika) · [Sarvam API pricing](https://www.sarvam.ai/api-pricing)
**Practitioner comparisons (2025–2026):** [Softcery: 12 voice platforms 2026](https://softcery.com/lab/choosing-the-right-voice-agent-platform-in-2026) · [Softcery: STT/TTS guide](https://softcery.com/lab/how-to-choose-stt-tts-for-ai-voice-agents-in-2025-a-comprehensive-guide) · [Softcery cost calculator](https://softcery.com/ai-voice-agents-calculator) · [Hamming: voice agent stack framework](https://hamming.ai/resources/best-voice-agent-stack) · [Forasoft: LiveKit production guide](https://www.forasoft.com/learn/livekit-for-ai-agents-guide) · [TokenMix: Realtime vs Gemini Live vs ElevenLabs](https://tokenmix.ai/blog/voice-ai-api-realtime-vs-gemini-live-vs-elevenlabs-2026) · [Ry Walker: agentic voice APIs](https://rywalker.com/research/agentic-voice-apis) · [Inworld: Vapi vs Pipecat vs LiveKit](https://inworld.ai/resources/vapi-vs-pipecat-vs-livekit) · [Retell: pricing comparison](https://www.retellai.com/resources/voice-ai-platform-pricing-comparison-2025) · [Retell: best LLM for voice agents](https://www.retellai.com/blog/best-llm-for-voice-ai-agents) · [Skywork: OpenAI Realtime pricing](https://skywork.ai/blog/agent/openai-realtime-api-pricing-2025-cost-calculator/) · [Deepgram: TTS APIs 2026](https://deepgram.com/learn/best-text-to-speech-apis-2026) · [Deepgram: ElevenLabs alternatives](https://deepgram.com/learn/text-to-speech-elevenlabs-alternatives) · [AssemblyAI: top TTS APIs](https://www.assemblyai.com/blog/top-text-to-speech-apis) · [LavivienPost: TTS comparison](https://www.lavivienpost.com/comparison-of-text-to-speech-tts-models/)
**India-specific:** [Business Standard: Saaras V3 benchmarks](https://www.business-standard.com/technology/tech-news/saaras-v3-beats-gemini-gpt-4o-on-indian-speech-benchmarks-says-sarvam-ai-126021200384_1.html) · [Caller Digital: open-source voice AI India](https://www.caller.digital/blog/open-source-voice-ai-india-sarvam-ai4bharat-bhasini-2026) · [Analytics Vidhya: Bulbul-V2](https://www.analyticsvidhya.com/blog/2025/05/bulbul-v2-by-sarvam/)
