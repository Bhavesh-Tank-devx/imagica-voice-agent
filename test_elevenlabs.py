import httpx, asyncio

async def main():
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "http://localhost:8001/webhook/cart-abandoned",
            json={
                "customer_name": "Neha",
                "customer_phone": "+919913874598",
                "cart_id": "CART-NEHA-001",
                "visit_date": "12 April 2025",
                "tickets": [{"type": "Adult", "quantity": 2, "price_per_unit": 1499}],
                "total_amount": 5796,
                "attempt_number": 1,
            },
        )
        print(resp.status_code, resp.json())

asyncio.run(main())
