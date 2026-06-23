"""
benchmark/scorers.py — Class B metric scorers.

Operate on a parsed call_logs row (dict) + the Scenario gold. Deterministic and
ground-truth-based wherever possible; the LLM judge (judge.py) covers only what
deterministic rules can't (intent nuance), and is calibrated against humans.

Tier validity (enforced by callers / report.py):
  - task_completion: T1 (ceiling), T3 (truth)
  - aht_seconds:     T3 (truth), T1 (proxy)
  - silence_rate:    T3 ONLY  (a harness artifact in T1)
  - wer:             T2 ONLY
  - error_rate:      all tiers
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


# ---------------------------------------------------------------------------
# Text / WER helpers
# ---------------------------------------------------------------------------

def _norm_words(text: str) -> list[str]:
    keep = "".join(c.lower() if c.isalnum() or c.isspace() else " " for c in text)
    return keep.split()


def word_error_rate(hypothesis: str, reference: str) -> float:
    """Standard WER = (S+D+I)/N via word-level Levenshtein. 0.0 = perfect."""
    hyp, ref = _norm_words(hypothesis), _norm_words(reference)
    if not ref:
        return 0.0 if not hyp else 1.0
    m, n = len(ref), len(hyp)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, n + 1):
            cur = dp[j]
            dp[j] = prev if ref[i - 1] == hyp[j - 1] else 1 + min(prev, dp[j], dp[j - 1])
            prev = cur
    return round(dp[n] / m, 4)


# ---------------------------------------------------------------------------
# Slot extraction from tool calls
# ---------------------------------------------------------------------------

def _decisive_tool_args(tool_calls: list, tool_name: str) -> dict | None:
    for tc in tool_calls:
        if tc.get("tool") == tool_name:
            return tc.get("args", {})
    return None


def _any_tool_fired(tool_calls: list, name: str) -> bool:
    return any(tc.get("tool") == name for tc in tool_calls)


# ---------------------------------------------------------------------------
# Scorers
# ---------------------------------------------------------------------------

@dataclass
class CompletionResult:
    completed: bool
    reasons: list[str]


def score_task_completion(row: dict, scenario) -> CompletionResult:
    """Deterministic completion: right disposition AND (if applicable) the
    decisive tool fired with the correct branch + correct slot values."""
    gold = scenario.gold
    reasons: list[str] = []

    # Negative-goal scenarios (wrong number / not interested / no-outcome):
    if not gold.should_complete:
        ok = (gold.expected_disposition is None or row.get("disposition") == gold.expected_disposition)
        if gold.expected_tool:
            ok = ok and _any_tool_fired(row.get("tool_calls", []), gold.expected_tool)
        reasons.append(f"negative-goal: disposition={row.get('disposition')} expected={gold.expected_disposition}")
        return CompletionResult(ok, reasons)

    # Positive-goal scenarios:
    completed = True
    if gold.expected_disposition and row.get("disposition") != gold.expected_disposition:
        completed = False
        reasons.append(f"disposition {row.get('disposition')} != {gold.expected_disposition}")

    if gold.expected_tool:
        args = _decisive_tool_args(row.get("tool_calls", []), gold.expected_tool)
        if args is None:
            completed = False
            reasons.append(f"decisive tool '{gold.expected_tool}' never fired")
        else:
            # branch correctness (ground truth from kaya_branches)
            if gold.expected_branch:
                got = (args.get("branch_name") or "").strip().lower()
                if got != gold.expected_branch.strip().lower():
                    completed = False
                    reasons.append(f"branch '{args.get('branch_name')}' != '{gold.expected_branch}'")
            # slot correctness
            for k, want in gold.expected_slots.items():
                got = str(args.get(k, "")).strip().lower()
                if got != str(want).strip().lower():
                    completed = False
                    reasons.append(f"slot {k}='{args.get(k)}' != '{want}'")
    if completed:
        reasons.append("all gold conditions met")
    return CompletionResult(completed, reasons)


def score_aht_seconds(row: dict) -> int:
    return int(row.get("duration_seconds") or 0)


def score_silence_rate(row: dict, gap_threshold_sec: float = 1.5) -> float | None:
    """T3 only. Fraction of inter-turn gaps exceeding the dead-air threshold,
    using transcript timestamps. Returns None if timestamps are unusable."""
    transcript = row.get("transcript", [])
    ts = []
    for t in transcript:
        raw = t.get("ts")
        if not raw:
            continue
        try:
            ts.append(datetime.fromisoformat(raw))
        except Exception:
            return None
    if len(ts) < 2:
        return None
    gaps = [(ts[i + 1] - ts[i]).total_seconds() for i in range(len(ts) - 1)]
    long_gaps = [g for g in gaps if g > gap_threshold_sec]
    return round(len(long_gaps) / len(gaps), 4)


def score_error_rate(row: dict, scenario) -> float:
    """Errors per turn: failed tool calls + wrong-slot captures + wrong-language
    turns, divided by number of agent turns."""
    tool_calls = row.get("tool_calls", [])
    transcript = row.get("transcript", [])
    agent_turns = max(1, sum(1 for t in transcript if t.get("role") == "agent"))

    errors = 0
    # failed tool executions (runner records ok=False on exceptions)
    errors += sum(1 for tc in tool_calls if tc.get("ok") is False or tc.get("is_error"))
    # wrong-slot captures vs gold (only count if the decisive tool fired)
    gold = scenario.gold
    if gold.expected_tool and gold.expected_slots:
        args = _decisive_tool_args(tool_calls, gold.expected_tool)
        if args is not None:
            for k, want in gold.expected_slots.items():
                if str(args.get(k, "")).strip().lower() != str(want).strip().lower():
                    errors += 1
    # wrong-language turns (expected hinglish/hindi but logged english, or vice versa)
    expected_lang = scenario.persona.language
    detected = (row.get("language_detected") or "").lower()
    if expected_lang in ("hinglish", "hindi") and detected == "english":
        errors += 1
    return round(errors / agent_turns, 4)


def score_row(row: dict, scenario, tier: str) -> dict:
    """Compute all applicable metrics for one conversation, honoring tier validity."""
    comp = score_task_completion(row, scenario)
    out = {
        "scenario_id": scenario.id,
        "task": scenario.task,
        "category": scenario.category,
        "tier": tier,
        "stack": row.get("stack"),
        "completion": comp.completed,
        "completion_reasons": comp.reasons,
        "aht_seconds": score_aht_seconds(row),
        "error_rate": score_error_rate(row, scenario),
        "first_response_ms": row.get("first_response_ms"),
        "latency_per_turn": row.get("latency_per_turn", []),
        "wer": row.get("wer"),  # populated by T2 path
    }
    out["silence_rate"] = score_silence_rate(row) if tier == "T3" else None
    # Annotate tier-validity caveats so the report can't misuse a number.
    out["_validity"] = {
        "completion": "truth" if tier == "T3" else "ceiling",
        "aht_seconds": "truth" if tier == "T3" else "proxy",
        "silence_rate": "valid" if tier == "T3" else "n/a (harness artifact)",
        "wer": "valid" if tier == "T2" else "n/a",
    }
    return out
