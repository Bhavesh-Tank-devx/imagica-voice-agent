"""
benchmark/judge.py — LLM-as-judge for Intent Recognition Accuracy, plus the
human-calibration harness that makes the judge a trustworthy instrument.

A benchmark whose judge is itself unvalidated is vibes, not measurement. Before
any judge-produced ranking is trusted, run `calibrate()` against a hand-labeled
gold set (benchmark/gold/*.json) and report agreement (% + Cohen's κ). If judge
error is comparable to the gap between stacks, the ranking is flagged unreliable.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("benchmark.judge")

JUDGE_MODEL = os.getenv("BENCH_JUDGE_MODEL", "claude-opus-4-8")
GOLD_DIR = Path(__file__).parent / "gold"

_JUDGE_SYS = (
    "You are a strict QA evaluator for a voice agent. Given a call transcript and "
    "the caller's goal, judge two things:\n"
    "1. goal_achieved (bool): did the agent actually accomplish the caller's stated goal?\n"
    "2. intent_accuracy (0.0-1.0): across the agent's turns, what fraction correctly "
    "understood what the caller wanted and responded/routed appropriately?\n"
    "Be skeptical: partial or apparent success without the concrete outcome is goal_achieved=false. "
    "Respond ONLY with compact JSON: "
    '{"goal_achieved": bool, "intent_accuracy": number, "reasoning": "one sentence"}'
)


def _client():
    try:
        import anthropic
        if os.getenv("ANTHROPIC_API_KEY"):
            return anthropic.Anthropic()
    except Exception:
        pass
    return None


def _transcript_text(transcript: list) -> str:
    return "\n".join(f"{t.get('role','?').upper()}: {t.get('text','')}" for t in transcript)


def judge_conversation(transcript: list, goal: str, model: str = JUDGE_MODEL) -> dict:
    """Return {goal_achieved, intent_accuracy, reasoning}. Falls back to a
    neutral verdict if no LLM is available (flagged)."""
    client = _client()
    if client is None:
        return {"goal_achieved": None, "intent_accuracy": None,
                "reasoning": "no LLM judge available", "_no_judge": True}
    user = f"CALLER GOAL: {goal}\n\nTRANSCRIPT:\n{_transcript_text(transcript)}"
    try:
        resp = client.messages.create(
            model=model, max_tokens=300, system=_JUDGE_SYS,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        text = text[text.find("{"): text.rfind("}") + 1]
        return json.loads(text)
    except Exception as exc:
        logger.error("[judge] error: %s", exc)
        return {"goal_achieved": None, "intent_accuracy": None, "reasoning": f"judge error: {exc}"}


# ---------------------------------------------------------------------------
# Calibration against human labels
# ---------------------------------------------------------------------------

def _cohen_kappa(a: list[bool], b: list[bool]) -> float:
    """Cohen's κ for two binary raters."""
    n = len(a)
    if n == 0:
        return 0.0
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pa1 = sum(a) / n; pb1 = sum(b) / n
    pe = pa1 * pb1 + (1 - pa1) * (1 - pb1)
    return round((po - pe) / (1 - pe), 4) if pe != 1 else 1.0


def load_gold(gold_dir: Path = GOLD_DIR) -> list[dict]:
    """Each gold file: {transcript:[...], goal:str, human_goal_achieved:bool}."""
    items = []
    for p in sorted(gold_dir.glob("*.json")):
        try:
            items.append(json.loads(p.read_text()))
        except Exception as exc:
            logger.warning("[judge] bad gold file %s: %s", p, exc)
    return items


def calibrate(gold_dir: Path = GOLD_DIR, kappa_threshold: float = 0.6) -> dict:
    """Run the judge on the hand-labeled gold set; report agreement + κ.
    Returns a dict and logs a PASS/FAIL gate."""
    gold = load_gold(gold_dir)
    if not gold:
        return {"n": 0, "status": "NO_GOLD",
                "note": f"add 25-30 hand-labeled conversations to {gold_dir} to calibrate"}
    human, judge = [], []
    for g in gold:
        v = judge_conversation(g["transcript"], g.get("goal", ""))
        if v.get("goal_achieved") is None:
            continue
        human.append(bool(g["human_goal_achieved"]))
        judge.append(bool(v["goal_achieved"]))
    n = len(human)
    if n == 0:
        return {"n": 0, "status": "JUDGE_UNAVAILABLE"}
    agree = sum(1 for x, y in zip(human, judge) if x == y) / n
    kappa = _cohen_kappa(human, judge)
    status = "PASS" if kappa >= kappa_threshold else "FAIL"
    result = {"n": n, "agreement": round(agree, 4), "cohen_kappa": kappa,
              "threshold": kappa_threshold, "status": status}
    logger.info("[judge] calibration: %s", result)
    if status == "FAIL":
        logger.warning("[judge] κ below threshold — judge-derived rankings are UNRELIABLE; "
                       "revise the judge prompt or expand gold set before trusting intent scores.")
    return result
