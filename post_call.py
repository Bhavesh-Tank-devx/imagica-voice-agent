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

# Disposition constants — map to Zoho Lead_Status values in production
DISPOSITION_BOOKED = "BOOKED"
DISPOSITION_CALLBACK = "CALLBACK"
DISPOSITION_NOT_INTERESTED = "NOT_INTERESTED"
DISPOSITION_TRANSFERRED = "TRANSFERRED"
DISPOSITION_NO_ANSWER = "NO_ANSWER"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS call_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cart_id TEXT NOT NULL,
            customer_name TEXT,
            customer_phone TEXT,
            disposition TEXT,          -- BOOKED / CALLBACK / NOT_INTERESTED / TRANSFERRED / NO_ANSWER
            transcript TEXT,           -- full conversation as JSON array
            summary TEXT,              -- 1-2 line outcome summary
            discount_applied INTEGER,  -- 0 or percent (5 or 10)
            attempt_number INTEGER,
            called_at TEXT,
            ended_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def log_call(
    cart: dict,
    disposition: str,
    transcript: list,
    summary: str,
    discount: int = 0,
    called_at: str | None = None,
):
    """
    Write a call outcome record to the local SQLite DB.

    Args:
        cart: The cart_data dict used during the call.
        disposition: One of the DISPOSITION_* constants above.
        transcript: List of {"role": "agent"/"user", "text": "..."} dicts.
        summary: Short human-readable outcome, e.g. "Customer agreed to book, link sent."
        discount: Discount percent applied (0 if none, 5 or 10 if applied).
        called_at: ISO timestamp when call started (defaults to now).
    """
    init_db()
    now = datetime.now().isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT INTO call_logs
        (cart_id, customer_name, customer_phone, disposition, transcript,
         summary, discount_applied, attempt_number, called_at, ended_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        ),
    )
    conn.commit()
    conn.close()
    logger.info(f"[CRM] Logged: {cart['cart_id']} → {disposition} | {summary}")


def get_call_logs(cart_id: str | None = None) -> list[dict]:
    """Fetch call log rows, optionally filtered by cart_id. Used for inspection/debugging."""
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
