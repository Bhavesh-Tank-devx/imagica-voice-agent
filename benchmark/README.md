# Voice-Agent Benchmark Harness — Class B Metrics

Measures the **deployment-dependent** metrics that `docs/VOICE_MODEL_STUDY.md` left blank
(no vendor publishes them): **Task Completion Rate, Intent Accuracy, AHT, Silence
Rate, Error Rate**, plus **WER** on telephony-grade audio. Runs the *same* scenarios
across multiple voice stacks and scores them apples-to-apples.

## Why three tiers (each metric measured where it's valid)

| Tier | What runs | Cost | Valid metrics |
|---|---|---|---|
| **T1** interactive sim, codec-degraded | `SimUser` (LLM) holds a dialogue; its TTS is pushed through the production µ-law 8 kHz codec before the stack's STT | API only | Task Completion **(ceiling)**, dialogue logic, response-latency slice |
| **T2** fixed audio replay | one frozen corpus replayed identically against every stack's STT | cheap, large-N | **WER, Intent, ASR-error** — *the powered comparison* |
| **T3** live phone calls | real Twilio calls (small N) | $$ | **Silence Rate**, barge-in, true AHT — *qualitative* |

Reporting rules (enforced in `report.py`): completion is **never merged** across
tiers (T1 ceiling vs T3 truth); Silence Rate is **T3-only**; WER is **T2-only**;
T2 carries statistical weight, T3 is qualitative.

## Layout
```
benchmark/
  providers/base.py          VoiceStack interface + normalized events + MockStack
  providers/elevenlabs_stack.py  ElevenLabs Conv AI adapter (baseline)
  scenarios/                 16 scenarios (8 Kaya-Hinglish + 8 generic-English) w/ gold labels
  sim_user.py                LLM-as-user + TTS + telephony codec degrade
  audio_corpus.py            T2 corpus loader + (synthetic) starter generator
  scorers.py                 deterministic + ground-truth Class B scorers, WER
  judge.py                   LLM judge for intent + Cohen's-κ human calibration
  runner.py                  orchestration; reuses voice_agent.execute_tool
  report.py                  per-tier leaderboard with Wilson CIs
  verify_pipeline.py         zero-spend end-to-end self-test (MockStack)
  gold/                      hand-labeled calibration conversations (add 25–30)
  corpus/                    T2 audio + manifests
```

## Reuse (no duplication)
- **Codec** `voice_agent.mulaw_to_pcm16k` / `pcm16k_to_mulaw`
- **Tools** `voice_agent.execute_tool` — identical booking writes & email correction
- **Ground truth** `kaya_branches.CITY_BRANCHES` — scorer asserts the *correct* branch
- **Logging** `post_call.log_benchmark_call` → `call_logs` (new cols: `benchmark_run_id, stack, scenario_id, tier, cost_usd, wer`)
- **Isolation** `runner.use_benchmark_db()` points all writes at `data/benchmark_runs.db` so production `data/post_call.db` is never touched.

## Run

Zero-spend pipeline self-test (no keys, no telephony):
```bash
python -m benchmark.verify_pipeline
```

Real T1/T2 (needs `ELEVENLABS_API_KEY`, `ANTHROPIC_API_KEY` for the sim user/judge):
```python
import asyncio
from benchmark import scenarios
from benchmark.runner import use_benchmark_db, new_run_id, run_conversation
from benchmark.providers.elevenlabs_stack import ElevenLabsStack

async def go():
    use_benchmark_db()
    rid = new_run_id()
    for sc in scenarios.by_task("kaya"):
        await run_conversation(ElevenLabsStack(), sc, run_id=rid, tier="T1", drive_user=True)
asyncio.run(go())
```

## Judge calibration is a gate
Before trusting any intent/goal ranking, hand-label 25–30 conversations into
`gold/*.json` (`{transcript, goal, human_goal_achieved}`) and run
`judge.calibrate()`. If Cohen's κ < 0.6 the report prints **FAIL** and rankings are
flagged unreliable — fix the judge prompt or expand the gold set first.

## Status — be precise about what's verified

**Built and self-tested zero-spend (MockStack):** the measurement *spine* —
consume-loop, shared tool dispatch, all scorers (incl. correct-branch ground
truth), WER, multi-utterance replay (reuse-after-close regression-guarded),
report with Wilson CIs, κ math. The ElevenLabs adapter compiles and imports.

**NOT yet exercised (require real keys / one watched call):**
- ElevenLabs WebSocket against the live endpoint; `sim_user` LLM turns;
  `tts_telephony`; the codec on real audio; the `drive_user=True` path (`driver()`).
- The judge against a real model.
- **Zero real Class B numbers exist yet**, and judge calibration is **not done**
  (`calibrate()` returns `NO_GOLD` until you label the exported templates).

**Do next, in order:**
1. **Close the judge gate (zero spend):** `python -m benchmark.export_gold_templates --n 30`,
   hand-label `human_goal_achieved` in `gold/*.json`, run `judge.calibrate()`; require κ ≥ 0.6.
2. **Watch ONE real T1 call** (`drive_user=True`) end-to-end and confirm the final
   `book_appointment` lands before the `FIRST_COMPLETED` race cancels `consume()`.
3. **One real T2 replay** across ≥2 utterances on a real stack; confirm WER is sane.
4. Then batch T1/T2 on ElevenLabs → first real Class B numbers.

**Phase B/C/D (after baseline is real):**
- **B:** `pipeline_stack.py` — best-of-breed (Deepgram/Sarvam STT + LLM +
  Cartesia/Bulbul TTS) on LiveKit/Pipecat; ablate one layer at a time.
- **C:** `sarvam_stack.py` — Saaras V3 STT + Bulbul-V2 TTS.
- **D:** fold measured Class B numbers back into `docs/VOICE_MODEL_STUDY.md`.

## Honesty caveats
- T1 completion is an **upper bound** (clean intent), not the real number.
- WER is only valid on **real accented telephony audio**; the synthetic starter
  corpus validates the *code*, not the stacks — provenance is shown in the report.
- T3 is small-N: it validates, it does not rank.
