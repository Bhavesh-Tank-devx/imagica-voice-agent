"""SQLite connection helpers, schema creation, and migrations.

The database is a simulated Zoho CRM (POC). The schema is intentionally flat so
fields map 1:1 to Zoho Lead fields; production swaps the function bodies for
real CRM calls. ``DB_PATH`` is kept stable so existing data and dashboards
continue to work.
"""
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger("imagica-crm")

DB_PATH = "data/post_call.db"

# Columns added to existing tables on startup (safe to run repeatedly).
_CALL_LOGS_MIGRATIONS: list[tuple[str, str]] = [
    ("call_placed_at", "TEXT"),
    ("call_connected_at", "TEXT"),
    ("first_response_ms", "INTEGER"),
    ("tool_calls", "TEXT"),
    ("language_detected", "TEXT"),
    ("latency_per_turn", "TEXT"),
    ("duration_seconds", "INTEGER"),
    ("agent_type", "TEXT DEFAULT 'imagica'"),
    # --- benchmark harness columns (see benchmark/) ---
    ("benchmark_run_id", "TEXT"),   # groups all rows from one benchmark run
    ("stack", "TEXT"),              # voice stack: elevenlabs | pipeline | sarvam | mock
    ("scenario_id", "TEXT"),        # scenario that drove the conversation
    ("tier", "TEXT"),               # T1 (sim) | T2 (replay) | T3 (live)
    ("cost_usd", "REAL"),           # estimated $ cost of this run
    ("wer", "REAL"),                # word error rate vs gold transcript (T2 only)
]
_CALL_QUEUE_MIGRATIONS: list[tuple[str, str]] = [
    ("agent_type", "TEXT DEFAULT 'imagica'"),
]


@contextmanager
def get_connection(*, row_factory: bool = False) -> Iterator[sqlite3.Connection]:
    """Yield a SQLite connection, closing it on exit.

    Args:
        row_factory: If True, rows are returned as ``sqlite3.Row`` (dict-like).

    Yields:
        An open SQLite connection.
    """
    parent = Path(DB_PATH).parent
    if parent and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    if row_factory:
        conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Create all tables and indexes if absent, then apply column migrations."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS call_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cart_id TEXT NOT NULL,
                customer_name TEXT,
                customer_phone TEXT,
                disposition TEXT,           -- see src.constants.Disposition
                transcript TEXT,            -- full conversation as JSON array
                summary TEXT,               -- 1-2 line outcome summary
                discount_applied INTEGER,   -- 0 or percent (5 or 10)
                attempt_number INTEGER,
                called_at TEXT,             -- ISO: when agent entrypoint started
                ended_at TEXT,              -- ISO: when call ended
                call_placed_at TEXT,        -- ISO: when webhook fired (E2E start)
                call_connected_at TEXT,     -- ISO: when customer joined
                first_response_ms INTEGER,  -- ms: user stopped -> agent started
                tool_calls TEXT,            -- JSON array of tools fired
                language_detected TEXT,     -- hinglish / hindi / english / unknown
                latency_per_turn TEXT,      -- JSON array of e2e latency ms per turn
                duration_seconds INTEGER    -- total call duration in seconds
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS call_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cart_id TEXT UNIQUE NOT NULL,
                customer_name TEXT,
                customer_phone TEXT,
                cart_value REAL NOT NULL,          -- priority key (total_amount)
                cart_data TEXT NOT NULL,           -- full JSON payload for dispatch
                status TEXT DEFAULT 'pending',     -- pending|in_progress|done|failed
                attempt_number INTEGER DEFAULT 1,
                scheduled_at TEXT,                 -- UTC ISO, NULL = dispatch now
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_queue_priority "
            "ON call_queue(status, cart_value DESC, scheduled_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS call_sessions (
                conversation_id TEXT PRIMARY KEY,
                cart_data       TEXT NOT NULL,   -- JSON cart dict
                initiated_at    TEXT NOT NULL,
                tool_calls      TEXT DEFAULT '[]',
                discount        REAL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
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
            """
        )
        conn.commit()
        _migrate(conn, "call_logs", _CALL_LOGS_MIGRATIONS)
        _migrate(conn, "call_queue", _CALL_QUEUE_MIGRATIONS)


def _migrate(conn: sqlite3.Connection, table: str,
             columns: list[tuple[str, str]]) -> None:
    """Add each column to ``table`` if it does not already exist."""
    for name, col_type in columns:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
