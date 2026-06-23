"""Inbound webhook request schemas."""
from src.models import AppModel


class TicketItem(AppModel):
    """A single ticket line in a cart."""

    type: str
    quantity: int
    price_per_unit: int


class CartAbandonedPayload(AppModel):
    """Imagicaa cart-abandonment event from the booking engine."""

    customer_name: str
    customer_phone: str
    cart_id: str
    visit_date: str
    tickets: list[TicketItem]
    total_amount: int
    attempt_number: int = 1


class KayaLeadPayload(AppModel):
    """Kaya Clinic lead intake event."""

    customer_name: str
    customer_phone: str
    cart_id: str
    call_type: str = "OUTBOUND"
    attempt_number: int = 1
    city: str = ""
