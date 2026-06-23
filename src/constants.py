"""Cross-cutting enums and constants shared across the whole application.

Disposition codes map 1:1 to Zoho Lead_Status values in production, so the
string values are part of the persisted contract and must not change.
"""
from enum import StrEnum


class AgentType(StrEnum):
    """Campaign a call belongs to. Drives prompt, agent ID, and DB tagging."""

    IMAGICA = "imagica"
    KAYA = "kaya"


class Disposition(StrEnum):
    """Final (and internal-retry) outcome codes for a call.

    Members whose value differs from the name are noted inline. Internal
    retry states (``NO_ANSWER`` / ``BUSY``) are never persisted as a final
    disposition; they are mapped to ``UNREACHABLE`` after the last attempt.
    """

    # --- Final dispositions (persisted to the CRM) ---
    INTERESTED_LINK_SENT = "INTERESTED_LINK_SENT"   # positive; booking link sent
    CONVERTED = "CONVERTED"                          # booking confirmed
    CALLBACK_SCHEDULED = "CALLBACK_SCHEDULED"        # customer asked to be called later
    PRICE_OBJECTION = "PRICE_OBJECTION"              # price concern, no commitment yet
    DATE_CHANGE = "DATE_CHANGE"                      # wants a different visit date
    NOT_INTERESTED = "NOT_INTERESTED"                # explicit refusal; stop retrying
    UNREACHABLE = "UNREACHABLE"                       # NO_ANSWER/BUSY after all attempts
    TRANSFERRED = "TRANSFERRED_TO_HUMAN"             # escalated to a human agent
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"          # call dropped / model error
    WRONG_NUMBER = "WRONG_NUMBER"                     # customer confirmed wrong number
    DND_BLOCKED = "DND_BLOCKED"                        # suppressed before dial
    CALL_COMPLETED_NO_OUTCOME = "CALL_COMPLETED_NO_OUTCOME"  # >60s, no tool outcome

    # --- Internal retry states (mapped to UNREACHABLE on the last attempt) ---
    NO_ANSWER = "NO_ANSWER"
    BUSY = "BUSY"


# Module-level aliases preserve the historic ``DISPOSITION_*`` names that the
# rest of the codebase (and the benchmark harness) imports. They are the same
# objects as the enum members, so ``Disposition.CONVERTED == DISPOSITION_CONVERTED``.
DISPOSITION_INTERESTED_LINK_SENT = Disposition.INTERESTED_LINK_SENT
DISPOSITION_CONVERTED = Disposition.CONVERTED
DISPOSITION_CALLBACK_SCHEDULED = Disposition.CALLBACK_SCHEDULED
DISPOSITION_PRICE_OBJECTION = Disposition.PRICE_OBJECTION
DISPOSITION_DATE_CHANGE = Disposition.DATE_CHANGE
DISPOSITION_NOT_INTERESTED = Disposition.NOT_INTERESTED
DISPOSITION_UNREACHABLE = Disposition.UNREACHABLE
DISPOSITION_TRANSFERRED = Disposition.TRANSFERRED
DISPOSITION_TECHNICAL_FAILURE = Disposition.TECHNICAL_FAILURE
DISPOSITION_WRONG_NUMBER = Disposition.WRONG_NUMBER
DISPOSITION_DND_BLOCKED = Disposition.DND_BLOCKED
DISPOSITION_CALL_COMPLETED_NO_OUTCOME = Disposition.CALL_COMPLETED_NO_OUTCOME
DISPOSITION_NO_ANSWER = Disposition.NO_ANSWER
DISPOSITION_BUSY = Disposition.BUSY

# Human-readable one-line outcome per disposition, written to the CRM summary.
DISPOSITION_SUMMARIES: dict[str, str] = {
    Disposition.INTERESTED_LINK_SENT: "Customer showed interest; booking link sent via SMS.",
    Disposition.CONVERTED: "Booking confirmed by customer.",
    Disposition.CALLBACK_SCHEDULED: "Customer requested callback at a later time.",
    Disposition.PRICE_OBJECTION: "Customer raised price concern; no commitment yet.",
    Disposition.DATE_CHANGE: "Customer wants a different visit date.",
    Disposition.NOT_INTERESTED: "Customer not interested; no further calls.",
    Disposition.UNREACHABLE: "Customer unreachable after all attempts.",
    Disposition.TRANSFERRED: "Call transferred to human agent.",
    Disposition.TECHNICAL_FAILURE: "Call dropped due to technical issue.",
    Disposition.WRONG_NUMBER: "Customer confirmed wrong number.",
    Disposition.NO_ANSWER: "Call ended with no conclusive outcome.",
    Disposition.BUSY: "Customer was busy; retry scheduled.",
    Disposition.CALL_COMPLETED_NO_OUTCOME: (
        "Call answered and conversation held; no booking or tool outcome recorded."
    ),
}


def disposition_summary(disposition: str) -> str:
    """Return the one-line CRM summary for ``disposition`` (fallback generic)."""
    return DISPOSITION_SUMMARIES.get(disposition, "Call ended.")
