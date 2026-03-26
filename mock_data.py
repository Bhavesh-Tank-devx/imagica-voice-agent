# mock_data.py
# Simulates what the Imagica booking engine would send via webhook
#
# Scenario: Neha Joshi is planning a surprise birthday trip to Imagicaa for her husband
# Arjun's 35th birthday. She added tickets for 2 adults and 2 kids but dropped off at
# checkout — possibly hesitating on the total price.

CART_DATA = {
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