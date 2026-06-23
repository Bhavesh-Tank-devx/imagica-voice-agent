"""Call-session persistence — survives server restarts (ElevenLabs-native path).

A session links an ElevenLabs ``conversation_id`` to the cart and the tools the
agent fired, so the post-call webhook can reconstruct the call after a restart.
"""
import json

from src.persistence.db import get_connection, init_db


def save_session(conversation_id: str, cart: dict, initiated_at: str) -> None:
    """Persist a new call session when the call is initiated."""
    init_db()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO call_sessions
                (conversation_id, cart_data, initiated_at, tool_calls, discount)
            VALUES (?, ?, ?, '[]', 0)
            """,
            (conversation_id, json.dumps(cart), initiated_at),
        )
        conn.commit()


def append_tool_call(conversation_id: str, tool_entry: dict) -> None:
    """Append a tool-call record to an existing session (no-op if absent)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT tool_calls, discount FROM call_sessions WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        if not row:
            return
        tool_calls = json.loads(row[0])
        tool_calls.append(tool_entry)
        discount = tool_entry.get("discount_percent", row[1])
        conn.execute(
            "UPDATE call_sessions SET tool_calls = ?, discount = ? "
            "WHERE conversation_id = ?",
            (json.dumps(tool_calls), discount, conversation_id),
        )
        conn.commit()


def get_session(conversation_id: str) -> dict | None:
    """Load a session dict, or None if not found."""
    with get_connection(row_factory=True) as conn:
        row = conn.execute(
            "SELECT * FROM call_sessions WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
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
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM call_sessions WHERE conversation_id = ?",
            (conversation_id,),
        )
        conn.commit()
