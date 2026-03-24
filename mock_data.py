# mock_data.py
# Simulates what the Imagica booking engine would send via webhook

CART_DATA = {
    "customer_name": "Bhavesh",
    "customer_phone": "+919913874598",  # test number
    "visit_date": "29 March 2025",
    "tickets": [
        {"type": "Adult", "quantity": 2, "price_per_unit": 1299},
        {"type": "Child", "quantity": 1, "price_per_unit": 799},
    ],
    "total_amount": 3397,
    "cart_id": "CART-2025-001",
    "abandoned_at": "2025-03-23T14:30:00+05:30",
    "booking_link": "https://imagicaa.com/book?cart=CART-2025-001&token=abc123",
    "park_name": "Imagicaa Theme Park, Khopoli",
    "attempt_number": 1,  # 1st of max 3 attempts
}