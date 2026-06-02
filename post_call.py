"""
post_call.py — Simulated Zoho CRM using SQLite (POC)
Logs call outcomes after each Priya conversation ends.

Production swap: replace log_call() body with log_to_zoho() call.
Schema is intentionally flat so fields map 1:1 to Zoho Lead fields.
"""
import json
import logging
import sqlite3
from datetime import datetime

logger = logging.getLogger("imagica-crm")

DB_PATH = "post_call.db"

# Full PRD disposition codes — map to Zoho Lead_Status values in production
DISPOSITION_INTERESTED_LINK_SENT = "INTERESTED_LINK_SENT"   # positive signal, booking link sent
DISPOSITION_CONVERTED = "CONVERTED"                         # booking confirmed (future: webhook from Imagica)
DISPOSITION_CALLBACK_SCHEDULED = "CALLBACK_SCHEDULED"       # customer asked to be called back later
DISPOSITION_PRICE_OBJECTION = "PRICE_OBJECTION"             # price concern raised, no commitment yet
DISPOSITION_DATE_CHANGE = "DATE_CHANGE"                     # customer wants a different visit date
DISPOSITION_NOT_INTERESTED = "NOT_INTERESTED"               # explicit refusal, stop retrying
DISPOSITION_UNREACHABLE = "UNREACHABLE"                     # NO_ANSWER/BUSY after all attempts exhausted
DISPOSITION_TRANSFERRED = "TRANSFERRED_TO_HUMAN"            # escalated to human agent
DISPOSITION_TECHNICAL_FAILURE = "TECHNICAL_FAILURE"         # call dropped / Gemini error
DISPOSITION_WRONG_NUMBER = "WRONG_NUMBER"                   # customer confirmed wrong number
DISPOSITION_DND_BLOCKED = "DND_BLOCKED"                     # suppressed before dial (DND/calling hours)
DISPOSITION_CALL_COMPLETED_NO_OUTCOME = "CALL_COMPLETED_NO_OUTCOME"  # call lasted >60s but no booking/tool outcome

# Internal retry states — used by retry.py to decide whether to retry.
# These are never logged to the CRM as final dispositions; they get
# mapped to UNREACHABLE after the last attempt.
DISPOSITION_NO_ANSWER = "NO_ANSWER"
DISPOSITION_BUSY = "BUSY"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS call_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cart_id TEXT NOT NULL,
            customer_name TEXT,
            customer_phone TEXT,
            disposition TEXT,           -- see DISPOSITION_* constants above
            transcript TEXT,            -- full conversation as JSON array
            summary TEXT,               -- 1-2 line outcome summary
            discount_applied INTEGER,   -- 0 or percent (5 or 10)
            attempt_number INTEGER,
            called_at TEXT,             -- ISO: when agent entrypoint started
            ended_at TEXT,              -- ISO: when call ended
            call_placed_at TEXT,        -- ISO: when webhook fired (E2E latency start)
            call_connected_at TEXT,     -- ISO: when customer participant joined
            first_response_ms INTEGER,  -- ms: user stopped → agent started (first turn, from ChatMessage.metrics)
            tool_calls TEXT,            -- JSON array of tools fired during call
            language_detected TEXT,     -- 'hinglish' / 'hindi' / 'english' / 'unknown'
            latency_per_turn TEXT,      -- JSON array of e2e_latency ms per turn
            duration_seconds INTEGER    -- total call duration in seconds
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS call_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cart_id TEXT UNIQUE NOT NULL,
            customer_name TEXT,
            customer_phone TEXT,
            cart_value REAL NOT NULL,          -- priority key (total_amount)
            cart_data TEXT NOT NULL,           -- full JSON payload for dispatch
            status TEXT DEFAULT 'pending',     -- pending | in_progress | done | failed
            attempt_number INTEGER DEFAULT 1,
            scheduled_at TEXT,                 -- UTC ISO, NULL means dispatch immediately
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_queue_priority "
        "ON call_queue(status, cart_value DESC, scheduled_at)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS call_sessions (
            conversation_id TEXT PRIMARY KEY,
            cart_data       TEXT NOT NULL,   -- JSON cart dict
            initiated_at    TEXT NOT NULL,
            tool_calls      TEXT DEFAULT '[]',
            discount        REAL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kaya_bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cart_id TEXT NOT NULL,
            customer_first_name TEXT,
            customer_last_name TEXT,
            customer_phone TEXT,
            customer_email TEXT,
            dob TEXT,
            pincode TEXT,
            city TEXT,
            branch_name TEXT,
            appointment_date TEXT,
            appointment_time TEXT,
            concern_summary TEXT,
            booked_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    _migrate_db(conn)
    conn.close()


def _migrate_db(conn: sqlite3.Connection) -> None:
    """Add any new columns to existing tables. Safe to run on every startup."""
    call_logs_columns = [
        ("call_placed_at", "TEXT"),
        ("call_connected_at", "TEXT"),
        ("first_response_ms", "INTEGER"),
        ("tool_calls", "TEXT"),
        ("language_detected", "TEXT"),
        ("latency_per_turn", "TEXT"),
        ("duration_seconds", "INTEGER"),
        ("agent_type", "TEXT DEFAULT 'imagica'"),
    ]
    for col, col_type in call_logs_columns:
        try:
            conn.execute(f"ALTER TABLE call_logs ADD COLUMN {col} {col_type}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

    call_queue_columns = [
        ("agent_type", "TEXT DEFAULT 'imagica'"),
    ]
    for col, col_type in call_queue_columns:
        try:
            conn.execute(f"ALTER TABLE call_queue ADD COLUMN {col} {col_type}")
            conn.commit()
        except sqlite3.OperationalError:
            pass


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
):
    """
    Write a call outcome record to the local SQLite DB.

    Args:
        cart: The cart_data dict used during the call.
        disposition: One of the DISPOSITION_* constants above.
        transcript: List of {"role": "agent"/"user", "text": "..."} dicts.
        summary: Short human-readable outcome.
        discount: Discount percent applied (0 if none).
        called_at: ISO timestamp when agent entrypoint started.
        call_placed_at: ISO timestamp when webhook fired (from cart metadata).
        call_connected_at: ISO timestamp when customer participant joined.
        first_response_ms: Ms from call_connected to first Priya audio.
        tool_calls: List of {"tool", "ts", "args"} dicts.
        language_detected: 'hinglish' / 'hindi' / 'english' / 'unknown'.
    """
    init_db()
    now = datetime.now().isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT INTO call_logs
        (cart_id, customer_name, customer_phone, disposition, transcript,
         summary, discount_applied, attempt_number, called_at, ended_at,
         call_placed_at, call_connected_at, first_response_ms, tool_calls, language_detected,
         latency_per_turn, duration_seconds, agent_type)
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
            agent_type or cart.get("agent_type", "imagica"),
        ),
    )
    conn.commit()
    conn.close()
    logger.info(f"[CRM] Logged: {cart['cart_id']} → {disposition} | {summary}")


def get_call_detail(call_id: int) -> dict | None:
    """Fetch a single call log row with parsed JSON fields."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM call_logs WHERE id = ?", (call_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    for field in ("transcript", "tool_calls", "latency_per_turn"):
        try:
            d[field] = json.loads(d[field]) if d.get(field) else []
        except Exception:
            d[field] = []
    # Compute latency summary inline
    turns = d["latency_per_turn"]
    if turns:
        n = len(turns)
        d["latency_summary"] = {
            "avg_ms": round(sum(turns) / n),
            "min_ms": min(turns),
            "max_ms": max(turns),
            "turns": n,
            "per_turn_ms": turns,
        }
    return d


def get_call_logs(cart_id: str | None = None) -> list[dict]:
    """Fetch call log rows, optionally filtered by cart_id."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if cart_id:
        rows = conn.execute(
            "SELECT * FROM call_logs WHERE cart_id = ? ORDER BY id DESC", (cart_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM call_logs ORDER BY id DESC LIMIT 50"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def enqueue_call(
    cart_id: str,
    customer_name: str,
    customer_phone: str,
    cart_value: float,
    cart_data_json: str,
    attempt_number: int = 1,
    scheduled_at: str | None = None,
    agent_type: str = "imagica",
) -> int:
    """Insert or upsert a call into the priority queue. Returns the queue row id.

    Uses ON CONFLICT upsert so retries (same cart_id, incremented attempt_number)
    reset the row back to 'pending' rather than being silently dropped.
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        """
        INSERT INTO call_queue
            (cart_id, customer_name, customer_phone, cart_value, cart_data, attempt_number, scheduled_at, agent_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cart_id) DO UPDATE SET
            cart_data      = excluded.cart_data,
            attempt_number = excluded.attempt_number,
            cart_value     = excluded.cart_value,
            status         = 'pending',
            scheduled_at   = excluded.scheduled_at,
            agent_type     = excluded.agent_type,
            updated_at     = datetime('now')
        """,
        (cart_id, customer_name, customer_phone, cart_value, cart_data_json, attempt_number, scheduled_at, agent_type),
    )
    queue_id = cur.lastrowid
    conn.commit()
    conn.close()
    logger.info(f"[QUEUE] Enqueued cart_id={cart_id} value=₹{cart_value} scheduled_at={scheduled_at or 'now'}")
    return queue_id


def dequeue_next_call() -> dict | None:
    """Atomically claim the highest-value pending call that is ready to dispatch.

    Selects by: status='pending' AND (scheduled_at IS NULL OR scheduled_at <= now),
    ordered by cart_value DESC. Marks the row 'in_progress' before returning
    so concurrent workers cannot double-dispatch the same call.
    Returns the full row as a dict, or None if nothing is ready.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT * FROM call_queue
        WHERE status = 'pending'
          AND (scheduled_at IS NULL OR scheduled_at <= datetime('now'))
        ORDER BY cart_value DESC
        LIMIT 1
        """
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE call_queue SET status = 'in_progress', updated_at = datetime('now') WHERE id = ?",
            (row["id"],),
        )
        conn.commit()
    conn.close()
    return dict(row) if row else None


def mark_queue_done(queue_id: int) -> None:
    """Mark a dispatched call as successfully handed off to LiveKit."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE call_queue SET status = 'done', updated_at = datetime('now') WHERE id = ?",
        (queue_id,),
    )
    conn.commit()
    conn.close()


def mark_queue_failed(queue_id: int) -> None:
    """Mark a dispatched call as failed so it can be investigated or retried manually."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE call_queue SET status = 'failed', updated_at = datetime('now') WHERE id = ?",
        (queue_id,),
    )
    conn.commit()
    conn.close()


def get_metrics() -> dict:
    """
    Compute PRD evaluation metrics from the call_logs table.
    Returns a dict suitable for the /metrics API endpoint.
    """
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) FROM call_logs").fetchone()[0]
    if total == 0:
        conn.close()
        return {"total_calls": 0}

    # Disposition distribution
    disposition_rows = conn.execute(
        "SELECT disposition, COUNT(*) as cnt FROM call_logs GROUP BY disposition ORDER BY cnt DESC"
    ).fetchall()
    disposition_dist = {r["disposition"]: r["cnt"] for r in disposition_rows}

    # Per-turn e2e latency — collect all turns across all calls, compute stats
    lat_rows = conn.execute(
        "SELECT latency_per_turn FROM call_logs WHERE latency_per_turn IS NOT NULL AND latency_per_turn != '[]'"
    ).fetchall()
    all_turns = []
    for r in lat_rows:
        try:
            all_turns.extend(json.loads(r[0]))
        except Exception:
            pass

    if all_turns:
        all_turns_sorted = sorted(all_turns)
        n = len(all_turns_sorted)
        latency = {
            "avg_ms": round(sum(all_turns_sorted) / n),
            "min_ms": all_turns_sorted[0],
            "max_ms": all_turns_sorted[-1],
            "p50_ms": all_turns_sorted[n // 2],
            "p95_ms": all_turns_sorted[int(n * 0.95)],
            "total_turns_measured": n,
            "under_1s_pct": round(sum(1 for t in all_turns_sorted if t < 1000) / n * 100),
        }
    else:
        # Fall back to first_response_ms if no per-turn data yet
        latency_row = conn.execute(
            "SELECT AVG(first_response_ms), MIN(first_response_ms), MAX(first_response_ms) "
            "FROM call_logs WHERE first_response_ms IS NOT NULL"
        ).fetchone()
        latency = {
            "avg_ms": round(latency_row[0]) if latency_row[0] else None,
            "min_ms": latency_row[1],
            "max_ms": latency_row[2],
            "note": "no per-turn data yet — restart agent to collect",
        }

    # Call connection rate: rows with call_connected_at / total
    connected = conn.execute(
        "SELECT COUNT(*) FROM call_logs WHERE call_connected_at IS NOT NULL"
    ).fetchone()[0]
    placed = conn.execute(
        "SELECT COUNT(*) FROM call_logs WHERE call_placed_at IS NOT NULL"
    ).fetchone()[0]

    # Conversation completion rate: ended cleanly = disposition not NO_ANSWER/BUSY/TECHNICAL_FAILURE
    completed = conn.execute(
        "SELECT COUNT(*) FROM call_logs "
        "WHERE disposition NOT IN ('NO_ANSWER','BUSY','TECHNICAL_FAILURE')"
    ).fetchone()[0]

    # Retry effectiveness: attempt 2+ with positive outcome (link sent / converted)
    retry_positive = conn.execute(
        "SELECT COUNT(*) FROM call_logs "
        "WHERE attempt_number > 1 AND disposition IN ('INTERESTED_LINK_SENT','CONVERTED','CALLBACK_SCHEDULED')"
    ).fetchone()[0]
    retry_total = conn.execute(
        "SELECT COUNT(*) FROM call_logs WHERE attempt_number > 1"
    ).fetchone()[0]

    # Tool call accuracy: calls where at least one tool fired / calls where customer answered
    tool_fired = conn.execute(
        "SELECT COUNT(*) FROM call_logs WHERE tool_calls != '[]' AND tool_calls IS NOT NULL"
    ).fetchone()[0]

    # Language distribution
    lang_rows = conn.execute(
        "SELECT language_detected, COUNT(*) as cnt FROM call_logs GROUP BY language_detected"
    ).fetchall()

    # Attempt distribution
    attempt_rows = conn.execute(
        "SELECT attempt_number, COUNT(*) as cnt, "
        "SUM(CASE WHEN disposition IN ('INTERESTED_LINK_SENT','CONVERTED') THEN 1 ELSE 0 END) as positive "
        "FROM call_logs GROUP BY attempt_number ORDER BY attempt_number"
    ).fetchall()

    conn.close()

    return {
        "total_calls": total,
        "disposition_distribution": disposition_dist,
        "voice_latency": latency,
        "call_connection_rate": f"{connected}/{placed}" if placed else "n/a",
        "conversation_completion_rate": f"{completed}/{total} ({round(completed/total*100)}%)" if total else "n/a",
        "retry_effectiveness": f"{retry_positive}/{retry_total} retries yielded positive outcome" if retry_total else "no retries yet",
        "tool_fired_rate": f"{tool_fired}/{connected} calls had at least one tool fire" if connected else "n/a",
        "language_distribution": {r["language_detected"]: r["cnt"] for r in lang_rows},
        "by_attempt": [
            {"attempt": r["attempt_number"], "total": r["cnt"], "positive": r["positive"]}
            for r in attempt_rows
        ],
    }


# ---------------------------------------------------------------------------
# Call session persistence (survives server restarts)
# ---------------------------------------------------------------------------

def save_session(conversation_id: str, cart: dict, initiated_at: str) -> None:
    """Persist a new call session to SQLite when the call is initiated."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT OR REPLACE INTO call_sessions
            (conversation_id, cart_data, initiated_at, tool_calls, discount)
        VALUES (?, ?, ?, '[]', 0)
        """,
        (conversation_id, json.dumps(cart), initiated_at),
    )
    conn.commit()
    conn.close()


def append_tool_call(conversation_id: str, tool_entry: dict) -> None:
    """Add a tool call record to an existing session."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT tool_calls, discount FROM call_sessions WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if not row:
        conn.close()
        return
    tool_calls = json.loads(row[0])
    tool_calls.append(tool_entry)
    discount = tool_entry.get("discount_percent", row[1])
    conn.execute(
        "UPDATE call_sessions SET tool_calls = ?, discount = ? WHERE conversation_id = ?",
        (json.dumps(tool_calls), discount, conversation_id),
    )
    conn.commit()
    conn.close()


def get_session(conversation_id: str) -> dict | None:
    """Load a session dict; returns None if not found."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM call_sessions WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "cart": json.loads(row["cart_data"]),
        "initiated_at": row["initiated_at"],
        "tool_calls": json.loads(row["tool_calls"]),
        "discount": row["discount"],
    }


def delete_session(conversation_id: str) -> None:
    """Remove a session after the call has been logged."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "DELETE FROM call_sessions WHERE conversation_id = ?",
        (conversation_id,),
    )
    conn.commit()
    conn.close()


def log_kaya_booking(
    cart_id: str,
    customer_phone: str,
    first_name: str,
    last_name: str,
    email: str,
    pincode: str,
    branch_name: str,
    appointment_date: str,
    appointment_time: str,
    dob: str = "",
    city: str = "",
    concern_summary: str = "",
) -> int:
    """Write a confirmed Kaya appointment to kaya_bookings. Returns the new row id."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        """
        INSERT INTO kaya_bookings
            (cart_id, customer_first_name, customer_last_name, customer_phone,
             customer_email, dob, pincode, city, branch_name,
             appointment_date, appointment_time, concern_summary)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            cart_id, first_name, last_name, customer_phone,
            email, dob, pincode, city, branch_name,
            appointment_date, appointment_time, concern_summary,
        ),
    )
    booking_id = cur.lastrowid
    conn.commit()
    conn.close()
    logger.info(
        f"[KAYA BOOKING] id={booking_id} cart_id={cart_id} "
        f"branch={branch_name} date={appointment_date} {appointment_time}"
    )
    return booking_id
