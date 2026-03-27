"""
load_test.py — Concurrent webhook load tester for Imagica Voice Agent

Fires N webhooks simultaneously and measures:
  - Per-webhook response time and status
  - Queue acceptance rate
  - Dispatch success/failure from agent.log
  - Summary stats at the end

Usage:
    python load_test.py            # default: 5 concurrent
    python load_test.py 10         # 10 concurrent
    python load_test.py 10 --stagger 0.5  # stagger by 0.5s each

All calls use mode=browser so no actual SIP dials are placed.
Cart values are varied so priority-queue ordering is observable in logs.
"""
import argparse
import asyncio
import json
import sys
import time

import httpx

BASE_URL = "http://localhost:8000"


def make_payload(i: int) -> dict:
    """Build a unique CartAbandonedPayload for webhook call #i."""
    # Vary cart value so queue dispatches highest-value first
    cart_value = 2998 + (i * 500)
    return {
        "customer_name": f"LoadUser{i}",
        "customer_phone": f"+9199900{i:05d}",
        "cart_id": f"CART-LOAD-{i:05d}",
        "visit_date": "15 May 2026",
        "tickets": [{"type": "Adult", "quantity": 2, "price_per_unit": 1499}],
        "total_amount": cart_value,
        "attempt_number": 1,
        "mode": "browser",  # never dials real SIP numbers
    }


async def fire_webhook(
    client: httpx.AsyncClient,
    i: int,
    results: list,
    stagger: float = 0.0,
) -> None:
    if stagger:
        await asyncio.sleep(i * stagger)

    payload = make_payload(i)
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
        results.append(
            {
                "i": i,
                "cart_id": payload["cart_id"],
                "cart_value": payload["total_amount"],
                "http_status": r.status_code,
                "queue_status": status,
                "elapsed_ms": round(elapsed * 1000),
                "ok": r.status_code == 200 and status == "queued",
            }
        )
        flag = "✓" if results[-1]["ok"] else "✗"
        print(
            f"  [{flag}] #{i:02d}  cart_id={payload['cart_id']}  "
            f"value=₹{payload['total_amount']}  "
            f"http={r.status_code}  status={status}  "
            f"time={elapsed*1000:.0f}ms"
        )
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        results.append(
            {
                "i": i,
                "cart_id": payload["cart_id"],
                "cart_value": payload["total_amount"],
                "http_status": None,
                "queue_status": "error",
                "elapsed_ms": round(elapsed * 1000),
                "ok": False,
                "error": str(exc),
            }
        )
        print(f"  [✗] #{i:02d}  cart_id={payload['cart_id']}  ERROR: {exc}")


def print_summary(results: list, n: int, total_elapsed: float) -> None:
    ok = sum(1 for r in results if r["ok"])
    failed = n - ok
    times = [r["elapsed_ms"] for r in results]
    avg = sum(times) / len(times) if times else 0
    max_t = max(times) if times else 0
    min_t = min(times) if times else 0

    print()
    print("=" * 60)
    print(f"LOAD TEST SUMMARY — {n} concurrent webhooks")
    print("=" * 60)
    print(f"  Accepted (queued):   {ok}/{n}")
    print(f"  Failed:              {failed}/{n}")
    print(f"  Webhook latency avg: {avg:.0f}ms")
    print(f"  Webhook latency min: {min_t}ms")
    print(f"  Webhook latency max: {max_t}ms")
    print(f"  Wall-clock total:    {total_elapsed*1000:.0f}ms")
    print()

    # Show priority order — highest cart_value should be queued first
    sorted_r = sorted(results, key=lambda x: -x["cart_value"])
    print("Priority order (highest cart value should dispatch first):")
    for r in sorted_r:
        print(
            f"  cart_id={r['cart_id']}  value=₹{r['cart_value']}  "
            f"queued={'yes' if r['ok'] else 'NO'}"
        )

    print()
    # Room tokens — sorted by cart_value DESC (highest dispatched first)
    print("Room tokens to join (copy into LiveKit Playground — highest value dispatched first):")
    for r in sorted_r:
        if r["ok"]:
            attempt = 1  # load test always uses attempt 1
            room = f"imagica-{r['cart_id']}-{attempt}"
            print(f"  ₹{r['cart_value']:>6}  room={room}")
            print(f"           curl 'http://localhost:8000/token?room={room}&identity=developer'")

    print()
    print("What to check now:")
    print("  1. tail -f logs/webhook.log | grep '\\[QUEUE\\]'")
    print("     — [QUEUE] logs come from the webhook server, not agent.log")
    print("     — expect N 'Dispatching cart_id=...' lines, highest value first")
    print("  2. Look for '[QUEUE] Dispatch failed' — Gemini 429 or LiveKit auth error")
    print("  3. GET http://localhost:8000/metrics — check call_connection_rate")
    print("  4. LiveKit dashboard — verify N rooms opened simultaneously")

    if failed:
        print()
        print(f"  WARNING: {failed} webhook(s) were not queued. Check server logs.")


async def check_server() -> bool:
    """Quick health check before running the test."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{BASE_URL}/health", timeout=5)
            return r.status_code == 200
    except Exception:
        return False


async def main(n: int, stagger: float) -> None:
    print(f"Imagica Voice Agent — Load Test")
    print(f"Target: {BASE_URL}")
    print(f"Concurrent webhooks: {n}")
    print(f"Stagger: {stagger}s between each" if stagger else "Stagger: none (true concurrent)")
    print()

    if not await check_server():
        print(f"ERROR: Server not reachable at {BASE_URL}")
        print("  Start it with: python main.py")
        sys.exit(1)

    print(f"Firing {n} webhooks...")
    results = []
    t0 = time.perf_counter()

    # Single shared client — reuses TCP connection pool
    async with httpx.AsyncClient() as client:
        await asyncio.gather(
            *[fire_webhook(client, i, results, stagger) for i in range(n)]
        )

    total_elapsed = time.perf_counter() - t0
    print_summary(results, n, total_elapsed)

    # Wait briefly then poll queue status via metrics
    print(f"Waiting 5s for queue worker to begin dispatching...")
    await asyncio.sleep(5)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{BASE_URL}/metrics", timeout=5)
            if r.status_code == 200:
                m = r.json()
                print(f"\nLive /metrics snapshot:")
                print(f"  total_calls logged: {m.get('total_calls', '?')}")
                print(f"  connection rate: {m.get('call_connection_rate', '?')}")
                print(f"  dispositions: {json.dumps(m.get('disposition_distribution', {}), indent=4)}")
    except Exception:
        pass  # metrics check is best-effort


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load test the Imagica webhook server")
    parser.add_argument(
        "n",
        nargs="?",
        type=int,
        default=5,
        help="Number of concurrent webhooks to fire (default: 5)",
    )
    parser.add_argument(
        "--stagger",
        type=float,
        default=0.0,
        help="Seconds between each webhook (default: 0 = fully concurrent)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.n, args.stagger))
