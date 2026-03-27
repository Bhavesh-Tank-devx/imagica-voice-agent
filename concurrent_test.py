"""
concurrent_test.py — Concurrent call test with specific phone numbers

Fires 5 webhooks simultaneously, one per customer, ordered by cart value
so the priority queue dispatches them highest-value first.

Usage:
    python concurrent_test.py
    python concurrent_test.py --mode phone   # trigger real SIP dials
"""
import argparse
import asyncio
import json
import sys
import time

import httpx

BASE_URL = "http://localhost:8000"

# Carts ordered highest → lowest value (queue dispatches in this priority order).
# Phone numbers are fixed per the test spec.
CARTS = [
    {
        "customer_name": "Bhavesh",
        "customer_phone": "+919913874598",   # highest cart
        "cart_id": "CART-CONCURRENT-001",
        "visit_date": "20 April 2026",
        "tickets": [
            {"type": "Adult", "quantity": 4, "price_per_unit": 1999},
            {"type": "Child", "quantity": 2, "price_per_unit": 999},
        ],
        "total_amount": 9994,
        "attempt_number": 1,
    },
    {
        "customer_name": "Bhavya",
        "customer_phone": "+919510785512",   # second highest
        "cart_id": "CART-CONCURRENT-002",
        "visit_date": "25 April 2026",
        "tickets": [
            {"type": "Adult", "quantity": 3, "price_per_unit": 1999},
            {"type": "Child", "quantity": 1, "price_per_unit": 999},
        ],
        "total_amount": 6996,
        "attempt_number": 1,
    },
    {
        "customer_name": "Vimal",
        "customer_phone": "+916353119347",   # third highest
        "cart_id": "CART-CONCURRENT-003",
        "visit_date": "1 May 2026",
        "tickets": [
            {"type": "Adult", "quantity": 2, "price_per_unit": 1999},
            {"type": "Child", "quantity": 1, "price_per_unit": 999},
        ],
        "total_amount": 4997,
        "attempt_number": 1,
    },
    {
        "customer_name": "Arjav",
        "customer_phone": "+919875275353",   # fourth
        "cart_id": "CART-CONCURRENT-004",
        "visit_date": "10 May 2026",
        "tickets": [
            {"type": "Adult", "quantity": 2, "price_per_unit": 1499},
        ],
        "total_amount": 2998,
        "attempt_number": 1,
    },
    {
        "customer_name": "Ratish",
        "customer_phone": "+918320999207",   # fifth / lowest
        "cart_id": "CART-CONCURRENT-005",
        "visit_date": "15 May 2026",
        "tickets": [
            {"type": "Adult", "quantity": 1, "price_per_unit": 1499},
        ],
        "total_amount": 1499,
        "attempt_number": 1,
    },
]


async def fire_webhook(
    client: httpx.AsyncClient,
    cart: dict,
    mode: str,
    results: list,
) -> None:
    payload = {**cart, "mode": mode}
    t0 = time.perf_counter()
    try:
        r = await client.post(
            f"{BASE_URL}/webhook/cart-abandoned",
            json=payload,
            timeout=30,
        )
        elapsed = time.perf_counter() - t0
        body = r.json()
        status = body.get("status", "?")
        ok = r.status_code == 200 and status == "queued"
        results.append(
            {
                "cart_id": cart["cart_id"],
                "customer": cart["customer_name"],
                "phone": cart["customer_phone"],
                "cart_value": cart["total_amount"],
                "http_status": r.status_code,
                "queue_status": status,
                "elapsed_ms": round(elapsed * 1000),
                "ok": ok,
            }
        )
        flag = "✓" if ok else "✗"
        print(
            f"  [{flag}] {cart['customer_name']:<15}  "
            f"phone={cart['customer_phone']}  "
            f"value=₹{cart['total_amount']:>6}  "
            f"http={r.status_code}  status={status}  "
            f"time={elapsed * 1000:.0f}ms"
        )
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        results.append(
            {
                "cart_id": cart["cart_id"],
                "customer": cart["customer_name"],
                "phone": cart["customer_phone"],
                "cart_value": cart["total_amount"],
                "http_status": None,
                "queue_status": "error",
                "elapsed_ms": round(elapsed * 1000),
                "ok": False,
                "error": str(exc),
            }
        )
        print(
            f"  [✗] {cart['customer_name']:<15}  "
            f"phone={cart['customer_phone']}  ERROR: {exc}"
        )


def print_summary(results: list, total_elapsed: float, mode: str) -> None:
    n = len(results)
    ok = sum(1 for r in results if r["ok"])
    failed = n - ok
    times = [r["elapsed_ms"] for r in results]
    avg = sum(times) / len(times) if times else 0

    print()
    print("=" * 65)
    print(f"CONCURRENT CALL TEST SUMMARY — {n} simultaneous webhooks")
    print("=" * 65)
    print(f"  Mode:                {mode}")
    print(f"  Accepted (queued):   {ok}/{n}")
    print(f"  Failed:              {failed}/{n}")
    print(f"  Webhook latency avg: {avg:.0f}ms")
    print(f"  Wall-clock total:    {total_elapsed * 1000:.0f}ms")
    print()

    sorted_r = sorted(results, key=lambda x: -x["cart_value"])
    print("Priority dispatch order (highest cart value → dispatched first):")
    print(f"  {'#':<3}  {'Customer':<15}  {'Phone':<15}  {'Cart Value':>12}  {'Queued'}")
    print(f"  {'-'*3}  {'-'*15}  {'-'*15}  {'-'*12}  {'-'*6}")
    for idx, r in enumerate(sorted_r, 1):
        queued = "YES" if r["ok"] else "NO"
        print(
            f"  {idx:<3}  {r['customer']:<15}  {r['phone']:<15}  "
            f"₹{r['cart_value']:>10}  {queued}"
        )

    print()
    print("Room tokens to join (highest cart value dispatched first):")
    for r in sorted_r:
        if r["ok"]:
            room = f"imagica-{r['cart_id']}-1"
            print(f"  ₹{r['cart_value']:>6}  {r['customer']:<15}  room={room}")
            print(f"           curl 'http://localhost:8000/token?room={room}&identity=developer'")

    print()
    print("Monitor with:")
    print("  tail -f logs/webhook.log | grep '\\[QUEUE\\]'")
    print("  curl http://localhost:8000/metrics")
    print("  curl http://localhost:8000/calls")

    if failed:
        print()
        print(f"  WARNING: {failed} webhook(s) failed. Check server logs.")


async def check_server() -> bool:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{BASE_URL}/health", timeout=5)
            return r.status_code == 200
    except Exception:
        return False


async def main(mode: str) -> None:
    print("Imagica Voice Agent — Concurrent Call Test")
    print(f"Target:  {BASE_URL}")
    print(f"Mode:    {mode}  {'(real SIP dials)' if mode == 'phone' else '(browser / no SIP)'}")
    print(f"Calls:   {len(CARTS)} simultaneous")
    print()

    if not await check_server():
        print(f"ERROR: Server not reachable at {BASE_URL}")
        print("  Start it with: python main.py")
        sys.exit(1)

    print("Firing all 5 webhooks simultaneously...")
    results = []
    t0 = time.perf_counter()

    async with httpx.AsyncClient() as client:
        await asyncio.gather(
            *[fire_webhook(client, cart, mode, results) for cart in CARTS]
        )

    total_elapsed = time.perf_counter() - t0
    print_summary(results, total_elapsed, mode)

    # Brief wait then poll metrics
    print(f"Waiting 5s for queue worker to begin dispatching...")
    await asyncio.sleep(5)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{BASE_URL}/metrics", timeout=5)
            if r.status_code == 200:
                m = r.json()
                print(f"\nLive /metrics snapshot:")
                print(f"  queued_calls:    {m.get('queued_calls', '?')}")
                print(f"  total_calls:     {m.get('total_calls', '?')}")
                print(f"  connection_rate: {m.get('call_connection_rate', '?')}")
    except Exception:
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Concurrent call test for Imagica Voice Agent")
    parser.add_argument(
        "--mode",
        choices=["browser", "phone"],
        default="browser",
        help="browser = LiveKit room only (default); phone = also place SIP call",
    )
    args = parser.parse_args()
    asyncio.run(main(args.mode))
