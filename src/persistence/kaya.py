"""Kaya Clinic appointment writes and dashboard reads."""
import json
import logging

from src.constants import AgentType
from src.persistence.db import get_connection, init_db

logger = logging.getLogger("imagica-crm")


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
    """Write a confirmed Kaya appointment to ``kaya_bookings``.

    Returns:
        The new booking row id.
    """
    init_db()
    with get_connection() as conn:
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
    logger.info(
        "[KAYA BOOKING] id=%s cart_id=%s branch=%s date=%s %s",
        booking_id, cart_id, branch_name, appointment_date, appointment_time,
    )
    return booking_id


def get_kaya_appointments() -> list[dict]:
    """Return all Kaya appointments ordered by date and time."""
    init_db()
    with get_connection(row_factory=True) as conn:
        rows = conn.execute(
            "SELECT * FROM kaya_bookings ORDER BY appointment_date, appointment_time"
        ).fetchall()
    return [dict(r) for r in rows]


def get_kaya_transcripts() -> list[dict]:
    """Return Kaya call-log summaries (no transcript body), newest first."""
    init_db()
    with get_connection(row_factory=True) as conn:
        rows = conn.execute(
            "SELECT id, cart_id, customer_name, customer_phone, disposition, "
            "duration_seconds, called_at, agent_type "
            "FROM call_logs WHERE agent_type = ? ORDER BY id DESC",
            (AgentType.KAYA,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_kaya_transcript_detail(call_id: int) -> dict | None:
    """Return a single Kaya call with its parsed transcript, or None."""
    init_db()
    with get_connection(row_factory=True) as conn:
        row = conn.execute(
            "SELECT id, cart_id, customer_name, customer_phone, disposition, "
            "transcript, duration_seconds, called_at "
            "FROM call_logs WHERE id = ? AND agent_type = ?",
            (call_id, AgentType.KAYA),
        ).fetchone()
    if not row:
        return None
    detail = dict(row)
    try:
        detail["transcript"] = json.loads(detail["transcript"] or "[]")
    except (json.JSONDecodeError, TypeError):
        detail["transcript"] = []
    return detail
