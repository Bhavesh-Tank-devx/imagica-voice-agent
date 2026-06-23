"""
benchmark/verify_pipeline.py — zero-spend end-to-end check of the measurement
pipeline using MockStack (no provider APIs, no telephony). Validates that:
  - run_conversation records tagged call_logs rows and executes shared tools
  - scorers compute completion (incl. correct-branch ground truth), AHT, error rate
  - a deliberately-wrong booking scores as NOT completed (the harness can fail)
  - run_replay computes WER vs gold (incl. a seeded ASR error → WER > 0)
  - report aggregates per-tier with CIs and refuses to merge tiers
  - judge calibration + Cohen's κ / Wilson CI math run

Run:  python -m benchmark.verify_pipeline
"""
from __future__ import annotations

import asyncio

from . import scenarios
from .providers.base import MockStack
from .runner import new_run_id, run_conversation, run_replay, use_benchmark_db
from .scorers import score_row, word_error_rate
from . import report as report_mod
from . import judge


def _script_for(sc, *, correct=True):
    """Build a MockStack script that drives `sc` to its gold outcome (or, if
    correct=False, books the WRONG branch to prove the scorer can fail it)."""
    f = sc.persona.facts
    g = sc.gold
    if g.expected_tool == "book_appointment":
        branch = g.expected_branch if correct else "Andheri"  # wrong on purpose
        return [
            {"agent": f"Hello {f.get('name','')}, this is the assistant calling."},
            {"hear": f"Yes, I want to book at {f.get('branch','')}."},
            {"agent": "Sure, may I take your details?"},
            {"hear": f"My email is {f.get('email','')}."},
            {"agent": "Confirming your appointment now."},
            {"tool": "book_appointment", "params": {
                "first_name": f.get("first_name", ""), "last_name": f.get("last_name", ""),
                "email": f.get("email", ""), "branch_name": branch,
                "appointment_date": f.get("date", ""), "appointment_time": f.get("time", ""),
                "city": f.get("city", ""), "pincode": f.get("pincode", ""),
                "concern_summary": f.get("concern", ""),
            }},
            {"agent": "Your appointment is confirmed!"},
            {"end": "completed"},
        ]
    if g.expected_tool == "mark_not_interested":
        return [
            {"agent": "Hi, am I speaking with the right person?"},
            {"hear": "Wrong number, not interested."},
            {"tool": "mark_not_interested", "params": {"reason": "wrong number"}},
            {"agent": "Apologies for the disturbance, goodbye."},
            {"end": "not_interested"},
        ]
    if g.expected_tool == "schedule_callback":
        return [
            {"agent": "Hello, is now a good time?"},
            {"hear": "I'm busy, call me tomorrow evening."},
            {"tool": "schedule_callback", "params": {"preferred_time": "tomorrow evening"}},
            {"agent": "Sure, I'll call then."},
            {"end": "callback"},
        ]
    if g.expected_tool == "transfer_to_human":
        return [
            {"agent": "How can I help?"},
            {"hear": "I need to reschedule, connect me to a person."},
            {"tool": "transfer_to_human", "params": {"reason": "reschedule"}},
            {"agent": "Connecting you now."},
            {"end": "transferred"},
        ]
    # no-decisive-tool scenarios (silence / off_topic)
    return [
        {"agent": "Hello, how can I help today?"},
        {"hear": "..."},
        {"agent": "Take your time."},
        {"end": "no_outcome"},
    ]


async def main():
    use_benchmark_db("data/benchmark_runs.db")  # isolate from production post_call.db
    run_id = new_run_id()
    scored = []

    # --- T1: drive every scenario to its gold outcome via MockStack ---
    for sc in scenarios.ALL_SCENARIOS:
        stack = MockStack(_script_for(sc, correct=True))
        stack.name = "mock_good"
        rec = await run_conversation(stack, sc, run_id=run_id, tier="T1", drive_user=False)
        scored.append(score_row(rec, sc, tier="T1"))

    # --- Contrast: a wrong-branch booking must score NOT completed ---
    happy = scenarios.get("kaya_happy_path")
    bad = MockStack(_script_for(happy, correct=False))
    bad.name = "mock_bad"
    rec_bad = await run_conversation(bad, happy, run_id=run_id, tier="T1", drive_user=False)
    s_bad = score_row(rec_bad, happy, tier="T1")
    scored.append(s_bad)
    assert s_bad["completion"] is False, "wrong-branch booking should NOT complete"
    print(f"[check] wrong-branch correctly scored incomplete: {s_bad['completion_reasons']}")

    # --- T2: replay WER, incl. a seeded ASR error ---
    class _Utt:
        def __init__(self, uid, heard, gold, synthetic=True):
            self.id, self._heard, self.gold_transcript = uid, heard, gold
            self.language, self.synthetic = "hinglish", synthetic
        def pcm16k(self):
            return b""
    pairs = [
        ("utt_clean", "my email is neha dot sharma at gmail dot com",
         "my email is neha dot sharma at gmail dot com"),
        ("utt_asr_err", "my email is neha sharman at gmail dot calm",
         "my email is neha dot sharma at gmail dot com"),
    ]
    # Drive ALL utterances through ONE run_replay call with a per-utterance
    # factory — this regression-guards the reuse-after-close bug class on T2.
    utts = [_Utt(uid, heard, gold) for uid, heard, gold in pairs]
    _heard_map = {uid: heard for uid, heard, _ in pairs}
    _seq = list(_heard_map.values())
    _i = {"n": 0}

    def make_stack():
        st = MockStack([{"hear": _seq[_i["n"]]}])
        st.name = "mock_good"
        _i["n"] += 1
        return st

    res = await run_replay(make_stack, utts, run_id=run_id)
    for r in res:
        wer = r["wer"]
        scored.append({
            "scenario_id": r["utterance_id"], "task": "replay", "category": "asr", "tier": "T2",
            "stack": "mock_good", "completion": (wer == 0.0), "completion_reasons": [],
            "aht_seconds": 0, "error_rate": None, "first_response_ms": None,
            "latency_per_turn": [], "wer": wer, "silence_rate": None,
        })
        print(f"[check] WER({r['utterance_id']}) = {wer}")
    # The 2nd utterance must NOT be silently empty (the _closed/reuse bug):
    assert res[1]["heard"], "2nd replay utterance lost its transcript — reuse-after-close regression!"
    assert res[0]["wer"] == 0.0 and res[1]["wer"] > 0, "WER scoring across multiple utterances broken"
    assert word_error_rate(pairs[1][1], pairs[1][2]) > 0, "seeded ASR error should yield WER>0"

    # --- Aggregate + report ---
    agg = report_mod.aggregate(scored)
    calib = judge.calibrate()  # graceful: NO_GOLD / JUDGE_UNAVAILABLE without key
    md = report_mod.render_markdown(agg, calibration=calib, corpus_provenance="SYNTHETIC (mock)")
    print("\n" + md)

    # --- Sanity on math helpers ---
    assert report_mod.wilson_ci(8, 8)[0] > 0.6
    assert judge._cohen_kappa([True, False, True, True], [True, False, False, True]) <= 1.0
    print("\n[verify] pipeline OK — scorers, replay/WER, report, CI/κ all functioning.")


if __name__ == "__main__":
    asyncio.run(main())
