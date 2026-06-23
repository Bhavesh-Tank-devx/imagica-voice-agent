"""SQLite-backed persistence (simulated Zoho CRM).

Re-exports the public API so callers use ``from src.persistence import log_call``
without depending on the internal module split.
"""
from src.persistence.calls import (
    get_benchmark_rows,
    get_call_detail,
    get_call_logs,
    get_metrics,
    log_benchmark_call,
    log_call,
)
from src.persistence.db import DB_PATH, get_connection, init_db
from src.persistence.kaya import (
    get_kaya_appointments,
    get_kaya_transcript_detail,
    get_kaya_transcripts,
    log_kaya_booking,
)
from src.persistence.queue import (
    dequeue_next_call,
    enqueue_call,
    mark_queue_done,
    mark_queue_failed,
)
from src.persistence.sessions import (
    append_tool_call,
    delete_session,
    get_session,
    save_session,
)

__all__ = [
    "DB_PATH",
    "get_connection",
    "init_db",
    "log_call",
    "log_benchmark_call",
    "get_benchmark_rows",
    "get_call_detail",
    "get_call_logs",
    "get_metrics",
    "enqueue_call",
    "dequeue_next_call",
    "mark_queue_done",
    "mark_queue_failed",
    "save_session",
    "append_tool_call",
    "get_session",
    "delete_session",
    "log_kaya_booking",
    "get_kaya_appointments",
    "get_kaya_transcripts",
    "get_kaya_transcript_detail",
]
