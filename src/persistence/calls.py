"""Call-log reads and writes, plus aggregate metrics.

Covers the production call path (``log_call``), the benchmark path
(``log_benchmark_call`` / ``get_benchmark_rows``), single/list reads, and the
``/metrics`` aggregation.
"""
import json
import logging
from datetime import datetime

from src.constants import AgentType
from src.persistence.db import get_connection, init_db

logger = logging.getLogger("imagica-crm")

_JSON_FIELDS = ("transcript", "tool_calls", "latency_per_turn")


def _parse_json_fields(row: dict) -> dict:
    """Decode JSON-encoded columns in a row dict in place, defaulting to ``[]``."""
    for field in _JSON_FIELDS:
        try:
            row[field] = json.loads(row[field]) if row.get(field) else []
        except (json.JSONDecodeError, TypeError):
            row[field] = []
    return row


def log_call(
    cart: dict,
    disposition: str,
    transcript: list,
    summary: str,
    discount: int = 0,
    called_at: str | None = None,
    call_placed_at: str | None = None,
    call_connected_at: str | None = None,
    first_response_ms: int | None = None,
    tool_calls: list | None = None,
    language_detected: str | None = None,
    latency_per_turn: list | None = None,
    duration_sec: int = 0,
    agent_type: str | None = None,
) -> None:
    """Write a call outcome record to the local SQLite DB.

    Args:
        cart: The cart_data dict used during the call.
        disposition: One of the ``Disposition`` codes.
        transcript: List of ``{"role", "text"}`` dicts.
        summary: Short human-readable outcome.
        discount: Discount percent applied (0 if none).
        called_at: ISO timestamp when the agent entrypoint started.
        call_placed_at: ISO timestamp when the webhook fired.
        call_connected_at: ISO timestamp when the customer joined.
        first_response_ms: Ms from connection to first agent audio.
        tool_calls: List of ``{"tool", "ts", "args"}`` dicts.
        language_detected: hinglish / hindi / english / unknown.
        latency_per_turn: Per-turn end-to-end latencies in ms.
        duration_sec: Total call duration in seconds.
        agent_type: Campaign tag; falls back to the cart's value.
    """
    init_db()
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO call_logs
            (cart_id, customer_name, customer_phone, disposition, transcript,
             summary, discount_applied, attempt_number, called_at, ended_at,
             call_placed_at, call_connected_at, first_response_ms, tool_calls,
             language_detected, latency_per_turn, duration_seconds, agent_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cart["cart_id"],
                cart["customer_name"],
                cart["customer_phone"],
                disposition,
                json.dumps(transcript),
                summary,
                discount,
                cart.get("attempt_number", 1),
                called_at or now,
                now,
                call_placed_at or cart.get("call_placed_at"),
                call_connected_at,
                first_response_ms,
                json.dumps(tool_calls or []),
                language_detected or "unknown",
                json.dumps(latency_per_turn or []),
                duration_sec,
                agent_type or cart.get("agent_type", AgentType.IMAGICA),
            ),
        )
        conn.commit()
    logger.info("[CRM] Logged: %s -> %s | %s", cart["cart_id"], disposition, summary)


def log_benchmark_call(
    *,
    benchmark_run_id: str,
    stack: str,
    scenario_id: str,
    tier: str,
    cart: dict,
    disposition: str,
    transcript: list,
    summary: str = "",
    tool_calls: list | None = None,
    latency_per_turn: list | None = None,
    first_response_ms: int | None = None,
    duration_sec: int = 0,
    language_detected: str | None = None,
    cost_usd: float | None = None,
    wer: float | None = None,
    agent_type: str | None = None,
) -> int:
    """Write one benchmark conversation to ``call_logs``, tagged with run/stack/scenario/tier.

    Kept separate from ``log_call`` so the production path is never touched.

    Returns:
        The new row id.
    """
    init_db()
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO call_logs
            (cart_id, customer_name, customer_phone, disposition, transcript,
             summary, discount_applied, attempt_number, called_at, ended_at,
             first_response_ms, tool_calls, language_detected,
             latency_per_turn, duration_seconds, agent_type,
             benchmark_run_id, stack, scenario_id, tier, cost_usd, wer)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cart.get("cart_id", scenario_id),
                cart.get("customer_name", ""),
                cart.get("customer_phone", ""),
                disposition,
                json.dumps(transcript),
                summary,
                0,
                cart.get("attempt_number", 1),
                now,
                now,
                first_response_ms,
                json.dumps(tool_calls or []),
                language_detected or "unknown",
                json.dumps(latency_per_turn or []),
                duration_sec,
                agent_type or cart.get("agent_type", "benchmark"),
                benchmark_run_id,
                stack,
                scenario_id,
                tier,
                cost_usd,
                wer,
            ),
        )
        row_id = cur.lastrowid
        conn.commit()
    logger.info(
        "[BENCH] run=%s stack=%s scenario=%s tier=%s -> %s (row %s)",
        benchmark_run_id, stack, scenario_id, tier, disposition, row_id,
    )
    return row_id


def get_benchmark_rows(
    benchmark_run_id: str | None = None,
    stack: str | None = None,
) -> list[dict]:
    """Fetch benchmark ``call_logs`` rows (parsed JSON), filtered by run/stack."""
    init_db()
    clauses, params = ["stack IS NOT NULL"], []
    if benchmark_run_id:
        clauses.append("benchmark_run_id = ?")
        params.append(benchmark_run_id)
    if stack:
        clauses.append("stack = ?")
        params.append(stack)
    with get_connection(row_factory=True) as conn:
        rows = conn.execute(
            f"SELECT * FROM call_logs WHERE {' AND '.join(clauses)} ORDER BY id",
            params,
        ).fetchall()
    return [_parse_json_fields(dict(r)) for r in rows]


def get_call_detail(call_id: int) -> dict | None:
    """Fetch a single call log row with parsed JSON fields and latency summary."""
    init_db()
    with get_connection(row_factory=True) as conn:
        row = conn.execute("SELECT * FROM call_logs WHERE id = ?", (call_id,)).fetchone()
    if not row:
        return None

    detail = _parse_json_fields(dict(row))
    turns = detail["latency_per_turn"]
    if turns:
        detail["latency_summary"] = {
            "avg_ms": round(sum(turns) / len(turns)),
            "min_ms": min(turns),
            "max_ms": max(turns),
            "turns": len(turns),
            "per_turn_ms": turns,
        }
    return detail


def get_call_logs(cart_id: str | None = None) -> list[dict]:
    """Fetch call log rows, optionally filtered by ``cart_id`` (newest first)."""
    init_db()
    with get_connection(row_factory=True) as conn:
        if cart_id:
            rows = conn.execute(
                "SELECT * FROM call_logs WHERE cart_id = ? ORDER BY id DESC",
                (cart_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM call_logs ORDER BY id DESC LIMIT 50"
            ).fetchall()
    return [dict(r) for r in rows]


def _latency_metrics(conn) -> dict:
    """Compute per-turn latency stats, falling back to first_response_ms."""
    lat_rows = conn.execute(
        "SELECT latency_per_turn FROM call_logs "
        "WHERE latency_per_turn IS NOT NULL AND latency_per_turn != '[]'"
    ).fetchall()
    all_turns: list[int] = []
    for row in lat_rows:
        try:
            all_turns.extend(json.loads(row[0]))
        except (json.JSONDecodeError, TypeError):
            pass

    if all_turns:
        turns = sorted(all_turns)
        n = len(turns)
        return {
            "avg_ms": round(sum(turns) / n),
            "min_ms": turns[0],
            "max_ms": turns[-1],
            "p50_ms": turns[n // 2],
            "p95_ms": turns[int(n * 0.95)],
            "total_turns_measured": n,
            "under_1s_pct": round(sum(1 for t in turns if t < 1000) / n * 100),
        }

    row = conn.execute(
        "SELECT AVG(first_response_ms), MIN(first_response_ms), MAX(first_response_ms) "
        "FROM call_logs WHERE first_response_ms IS NOT NULL"
    ).fetchone()
    return {
        "avg_ms": round(row[0]) if row[0] else None,
        "min_ms": row[1],
        "max_ms": row[2],
        "note": "no per-turn data yet — restart agent to collect",
    }


def get_metrics() -> dict:
    """Compute PRD evaluation metrics from ``call_logs`` for the ``/metrics`` endpoint."""
    init_db()
    with get_connection(row_factory=True) as conn:
        total = conn.execute("SELECT COUNT(*) FROM call_logs").fetchone()[0]
        if total == 0:
            return {"total_calls": 0}

        disposition_rows = conn.execute(
            "SELECT disposition, COUNT(*) AS cnt FROM call_logs "
            "GROUP BY disposition ORDER BY cnt DESC"
        ).fetchall()
        disposition_dist = {r["disposition"]: r["cnt"] for r in disposition_rows}

        latency = _latency_metrics(conn)

        connected = conn.execute(
            "SELECT COUNT(*) FROM call_logs WHERE call_connected_at IS NOT NULL"
        ).fetchone()[0]
        placed = conn.execute(
            "SELECT COUNT(*) FROM call_logs WHERE call_placed_at IS NOT NULL"
        ).fetchone()[0]
        completed = conn.execute(
            "SELECT COUNT(*) FROM call_logs "
            "WHERE disposition NOT IN ('NO_ANSWER','BUSY','TECHNICAL_FAILURE')"
        ).fetchone()[0]
        retry_positive = conn.execute(
            "SELECT COUNT(*) FROM call_logs WHERE attempt_number > 1 "
            "AND disposition IN ('INTERESTED_LINK_SENT','CONVERTED','CALLBACK_SCHEDULED')"
        ).fetchone()[0]
        retry_total = conn.execute(
            "SELECT COUNT(*) FROM call_logs WHERE attempt_number > 1"
        ).fetchone()[0]
        tool_fired = conn.execute(
            "SELECT COUNT(*) FROM call_logs "
            "WHERE tool_calls != '[]' AND tool_calls IS NOT NULL"
        ).fetchone()[0]
        lang_rows = conn.execute(
            "SELECT language_detected, COUNT(*) AS cnt FROM call_logs "
            "GROUP BY language_detected"
        ).fetchall()
        attempt_rows = conn.execute(
            "SELECT attempt_number, COUNT(*) AS cnt, "
            "SUM(CASE WHEN disposition IN ('INTERESTED_LINK_SENT','CONVERTED') "
            "THEN 1 ELSE 0 END) AS positive "
            "FROM call_logs GROUP BY attempt_number ORDER BY attempt_number"
        ).fetchall()

    return {
        "total_calls": total,
        "disposition_distribution": disposition_dist,
        "voice_latency": latency,
        "call_connection_rate": f"{connected}/{placed}" if placed else "n/a",
        "conversation_completion_rate": (
            f"{completed}/{total} ({round(completed / total * 100)}%)" if total else "n/a"
        ),
        "retry_effectiveness": (
            f"{retry_positive}/{retry_total} retries yielded positive outcome"
            if retry_total else "no retries yet"
        ),
        "tool_fired_rate": (
            f"{tool_fired}/{connected} calls had at least one tool fire"
            if connected else "n/a"
        ),
        "language_distribution": {r["language_detected"]: r["cnt"] for r in lang_rows},
        "by_attempt": [
            {"attempt": r["attempt_number"], "total": r["cnt"], "positive": r["positive"]}
            for r in attempt_rows
        ],
    }
