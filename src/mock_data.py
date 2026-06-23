"""Mock cart payload simulating what the Imagica booking engine would webhook.

Scenario: Neha Joshi planned a surprise birthday trip to Imagicaa for her
husband Arjun's 35th. She added 2 adult + 2 child tickets but dropped off at
checkout — likely hesitating on the total price. Used as a fallback when the
LiveKit worker runs without job metadata (``python -m src.worker.livekit_agent dev``).
"""

CART_DATA: dict = {
    "customer_name": "Neha",
    "customer_phone": "+919913874598",  # test number
    "visit_date": "12 April 2025",
    "tickets": [
        {"type": "Adult", "quantity": 2, "price_per_unit": 1499},
        {"type": "Child", "quantity": 2, "price_per_unit": 899},
    ],
    "total_amount": 5796,
    "cart_id": "CART-NEHA-001",
    "abandoned_at": "2025-03-25T11:15:00+05:30",
    "booking_link": "https://imagicaa.com/book?cart=CART-NEHA-001&token=xyz789",
    "park_name": "Imagicaa Theme Park, Khopoli",
    "attempt_number": 1,
}
