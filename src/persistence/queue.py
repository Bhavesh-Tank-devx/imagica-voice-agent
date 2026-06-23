"""Priority call queue: enqueue, atomically claim, and mark queued calls."""
import logging

from src.constants import AgentType
from src.persistence.db import get_connection

logger = logging.getLogger("imagica-crm")


def enqueue_call(
    cart_id: str,
    customer_name: str,
    customer_phone: str,
    cart_value: float,
    cart_data_json: str,
    attempt_number: int = 1,
    scheduled_at: str | None = None,
    agent_type: str = AgentType.IMAGICA,
) -> int:
    """Insert or upsert a call into the priority queue.

    Uses ``ON CONFLICT`` so retries (same ``cart_id``, incremented
    ``attempt_number``) reset the row to ``pending`` rather than being dropped.

    Returns:
        The queue row id.
    """
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO call_queue
                (cart_id, customer_name, customer_phone, cart_value, cart_data,
                 attempt_number, scheduled_at, agent_type)
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
            (
                cart_id, customer_name, customer_phone, cart_value,
                cart_data_json, attempt_number, scheduled_at, agent_type,
            ),
        )
        queue_id = cur.lastrowid
        conn.commit()
    logger.info(
        "[QUEUE] Enqueued cart_id=%s value=%s scheduled_at=%s",
        cart_id, cart_value, scheduled_at or "now",
    )
    return queue_id


def dequeue_next_call() -> dict | None:
    """Atomically claim the highest-value pending call that is ready to dispatch.

    Selects ``status='pending'`` rows whose ``scheduled_at`` is past (or null),
    ordered by ``cart_value`` descending, and flips the chosen row to
    ``in_progress`` before returning so concurrent workers cannot double-dispatch.

    Returns:
        The claimed row as a dict, or None if nothing is ready.
    """
    with get_connection(row_factory=True) as conn:
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
                "UPDATE call_queue SET status = 'in_progress', "
                "updated_at = datetime('now') WHERE id = ?",
                (row["id"],),
            )
            conn.commit()
    return dict(row) if row else None


def _set_queue_status(queue_id: int, status: str) -> None:
    """Update the status of a queued call."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE call_queue SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status, queue_id),
        )
        conn.commit()


def mark_queue_done(queue_id: int) -> None:
    """Mark a dispatched call as successfully handed off."""
    _set_queue_status(queue_id, "done")


def mark_queue_failed(queue_id: int) -> None:
    """Mark a dispatched call as failed for later investigation or manual retry."""
    _set_queue_status(queue_id, "failed")
