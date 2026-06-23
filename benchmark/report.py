"""
benchmark/report.py — aggregate scored conversations into a leaderboard.

Honesty rules enforced here:
  - Completion is reported per-tier (T1 ceiling vs T3 truth) and NEVER merged.
  - Silence Rate only appears for T3; WER only for T2.
  - Small-N tiers (T3) are labeled qualitative; T2 is the powered comparison.
  - Confidence intervals (Wilson) accompany completion proportions.
  - Corpus provenance (synthetic vs real) is surfaced for any WER number.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict


def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (round(max(0, center - half), 3), round(min(1, center + half), 3))


def aggregate(scored: list[dict]) -> dict:
    """scored: list of scorers.score_row outputs. Group by (stack, tier)."""
    groups: dict[tuple, list] = defaultdict(list)
    for s in scored:
        groups[(s["stack"], s["tier"])].append(s)

    out = {}
    for (stack, tier), rows in sorted(groups.items()):
        n = len(rows)
        comp = sum(1 for r in rows if r["completion"])
        lo, hi = wilson_ci(comp, n)
        ahts = [r["aht_seconds"] for r in rows if r["aht_seconds"]]
        errs = [r["error_rate"] for r in rows if r["error_rate"] is not None]
        sils = [r["silence_rate"] for r in rows if r.get("silence_rate") is not None]
        wers = [r["wer"] for r in rows if r.get("wer") is not None]
        out[f"{stack} / {tier}"] = {
            "stack": stack, "tier": tier, "n": n,
            "completion": f"{comp}/{n}",
            "completion_pct": round(comp / n * 100, 1) if n else 0,
            "completion_ci95": [lo, hi],
            "completion_meaning": "ceiling" if tier != "T3" else "truth",
            "aht_sec_median": round(statistics.median(ahts), 1) if ahts else None,
            "error_rate_mean": round(statistics.mean(errs), 4) if errs else None,
            "silence_rate_mean": round(statistics.mean(sils), 4) if sils else ("n/a" if tier != "T3" else None),
            "wer_mean": round(statistics.mean(wers), 4) if wers else ("n/a" if tier != "T2" else None),
            "powered": tier == "T2",
            "note": ("powered comparison" if tier == "T2"
                     else "qualitative, small-N — do not rank on this" if tier == "T3"
                     else "ceiling (clean-intent upper bound)"),
        }
    return out


def render_markdown(agg: dict, calibration: dict | None = None, corpus_provenance: str = "unknown") -> str:
    lines = ["# Benchmark Results — Class B Metrics", ""]
    if calibration:
        st = calibration.get("status", "?")
        lines += [
            f"**Judge calibration:** {st}"
            + (f" (κ={calibration.get('cohen_kappa')}, agreement={calibration.get('agreement')}, n={calibration.get('n')})"
               if calibration.get("n") else f" — {calibration.get('note', calibration.get('status'))}"),
            "",
        ]
        if st == "FAIL":
            lines += ["> ⚠️ Judge agreement below threshold — intent/goal rankings are UNRELIABLE.", ""]
    lines += ["| Stack / Tier | n | Completion (95% CI) | Meaning | AHT med (s) | Err rate | Silence | WER | Note |",
              "|---|---|---|---|---|---|---|---|---|"]
    for _, m in agg.items():
        ci = m["completion_ci95"]
        lines.append(
            f"| {m['stack']} / {m['tier']} | {m['n']} | {m['completion']} "
            f"({m['completion_pct']}%, [{ci[0]}–{ci[1]}]) | {m['completion_meaning']} | "
            f"{m['aht_sec_median']} | {m['error_rate_mean']} | {m['silence_rate_mean']} | "
            f"{m['wer_mean']} | {m['note']} |"
        )
    lines += ["",
              f"_WER corpus provenance: **{corpus_provenance}**_  "
              "(synthetic TTS validates the pipeline only — real accented telephony audio required for valid WER claims)",
              "",
              "**Reading rules:** T2 is the powered, apples-to-apples comparison (real audio, large-N). "
              "T1 completion is an upper bound (clean intent). T3 is qualitative validation (small-N) — "
              "Silence Rate and true barge-in live only here. Completion is never merged across tiers."]
    return "\n".join(lines)
