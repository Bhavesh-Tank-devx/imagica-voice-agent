"""
benchmark/export_gold_templates.py — seed the judge-calibration gold set from
REAL prior-call transcripts already in data/post_call.db (zero spend).

The judge must be calibrated against HUMAN labels before its intent/goal rankings
are trusted. This exports up to N real conversations into benchmark/gold/ as
templates with `human_goal_achieved` left null for you to fill in. A `_suggested`
field (heuristic from the recorded disposition) is provided only as a starting
hint — you MUST review each transcript and set the true label yourself, or the
calibration measures the judge against a heuristic instead of a human.

Run:  python -m benchmark.export_gold_templates --n 30
Then: edit each benchmark/gold/*.json, set "human_goal_achieved" true/false,
      and run judge.calibrate().
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

GOLD_DIR = Path(__file__).parent / "gold"

# disposition → (inferred caller goal, heuristic goal-achieved hint)
_GOAL_HINT = {
    "CONVERTED": ("book an appointment", True),
    "INTERESTED_LINK_SENT": ("get a booking link / proceed", True),
    "CALLBACK_SCHEDULED": ("arrange a callback", True),
    "TRANSFERRED_TO_HUMAN": ("reach a human agent", True),
    "NOT_INTERESTED": ("decline / opt out", False),
    "WRONG_NUMBER": ("end — wrong person", False),
    "CALL_COMPLETED_NO_OUTCOME": ("(unclear — no decisive outcome)", False),
    "NO_ANSWER": ("(no real conversation)", False),
    "TECHNICAL_FAILURE": ("(call dropped)", False),
}


def export(n: int = 30, db_path: str = "data/post_call.db") -> int:
    GOLD_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, agent_type, disposition, summary, transcript FROM call_logs "
        "WHERE transcript IS NOT NULL AND transcript != '[]' "
        "AND (stack IS NULL OR stack = '') "  # real calls only, not benchmark rows
        "ORDER BY id DESC"
    ).fetchall()
    conn.close()

    written = 0
    for r in rows:
        if written >= n:
            break
        try:
            transcript = json.loads(r["transcript"])
        except Exception:
            continue
        if len(transcript) < 2:
            continue
        goal, suggested = _GOAL_HINT.get(r["disposition"], ("(infer from transcript)", None))
        payload = {
            "_source_call_id": r["id"],
            "_recorded_disposition": r["disposition"],
            "_suggested": suggested,  # HINT ONLY — verify and overwrite human_goal_achieved
            "goal": goal,
            "human_goal_achieved": None,  # <-- YOU set this after reading the transcript
            "transcript": transcript,
        }
        (GOLD_DIR / f"gold_{r['id']:05d}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        written += 1
    print(f"Wrote {written} gold templates to {GOLD_DIR}")
    print("Next: set 'human_goal_achieved' true/false in each, then run judge.calibrate().")
    return written


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--db", default="data/post_call.db")
    args = ap.parse_args()
    export(args.n, args.db)
