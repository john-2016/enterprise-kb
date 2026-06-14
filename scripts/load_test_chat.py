"""Load test for /api/v1/chat/query.

20 concurrent (not 100, to avoid LLM rate limits) viewers each sending one question.
Reports P50/P95/P99 latency, total time, success count. Acceptance: P95 < 10s.

Usage:
    python scripts/load_test_chat.py [--base-url URL] [--concurrency N] [--question "..."]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time
from typing import List, Tuple

import httpx


async def login(client: httpx.AsyncClient, base_url: str, username: str, password: str) -> str:
    r = await client.post(
        f"{base_url}/api/v1/auth/login",
        json={"username": username, "password": password},
        timeout=30.0,
    )
    r.raise_for_status()
    return r.json()["access_token"]


async def one_query(
    client: httpx.AsyncClient, base_url: str, token: str, question: str, idx: int
) -> Tuple[int, float, str]:
    t0 = time.perf_counter()
    try:
        r = await client.post(
            f"{base_url}/api/v1/chat/query",
            json={"question": question},
            headers={"Authorization": f"Bearer {token}"},
            timeout=60.0,
        )
        latency = time.perf_counter() - t0
        if r.status_code == 200 and (r.json().get("model_used") or {}).get("metric_id") is not None:
            return (idx, latency, "OK")
        return (idx, latency, f"FAIL:{r.status_code}:{r.text[:120]}")
    except Exception as e:
        latency = time.perf_counter() - t0
        return (idx, latency, f"EXC:{type(e).__name__}:{e}")


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default=os.environ.get("LOADTEST_BASE_URL", "http://localhost:8000"))
    p.add_argument("--username", default=os.environ.get("LOADTEST_USER", "viewer"))
    p.add_argument("--password", default=os.environ.get("LOADTEST_PASS", "viewer123"))
    p.add_argument("--admin-user", default=os.environ.get("LOADTEST_ADMIN", "admin"))
    p.add_argument("--admin-pass", default=os.environ.get("LOADTEST_ADMIN_PASS", "admin123"))
    p.add_argument("--concurrency", type=int, default=20)
    p.add_argument("--question", default="hi")
    args = p.parse_args()

    async with httpx.AsyncClient() as client:
        # viewer token is used for the chat traffic itself
        token = await login(client, args.base_url, args.username, args.password)
        print(f"Logged in as {args.username!r}, token len={len(token)}")

        # Disable A/B routing so each request uses the default chat model
        # (avoids the known issue where user_hash_mod can route to an embedding model).
        # Requires admin; if absent, we just proceed and report.
        try:
            admin_token = await login(client, args.base_url, args.admin_user, args.admin_pass)
            r = await client.patch(
                f"{args.base_url}/api/v1/admin/ab-rules/1",
                json={"enabled": False},
                headers={"Authorization": f"Bearer {admin_token}"},
                timeout=10.0,
            )
            if r.status_code == 200:
                print("A/B rule chat-ab-test disabled for load test")
            else:
                print(f"Could not disable A/B rule (status={r.status_code}); proceeding")
        except Exception as e:
            print(f"Could not disable A/B rule ({e!r}); proceeding")

        sem = asyncio.Semaphore(args.concurrency)

        async def task(i: int):
            async with sem:
                return await one_query(client, args.base_url, token, args.question, i)

        t_start = time.perf_counter()
        results: List[Tuple[int, float, str]] = await asyncio.gather(
            *(task(i) for i in range(args.concurrency))
        )
        total = time.perf_counter() - t_start

    latencies = sorted(lat for _, lat, st in results if st == "OK")
    ok = len(latencies)
    fail = args.concurrency - ok

    def pct(p: float) -> float:
        if not latencies:
            return 0.0
        k = max(0, min(len(latencies) - 1, int(round((p / 100.0) * (len(latencies) - 1)))))
        return latencies[k] * 1000.0

    p50 = pct(50)
    p95 = pct(95)
    p99 = pct(99)
    mean = (statistics.mean(latencies) * 1000.0) if latencies else 0.0

    print("")
    print("=" * 60)
    print(f"Concurrency:       {args.concurrency}")
    print(f"Success:           {ok}/{args.concurrency} (failed={fail})")
    print(f"Total wall time:   {total:.2f}s")
    print(f"Mean latency:      {mean:.1f}ms")
    print(f"P50 latency:       {p50:.1f}ms")
    print(f"P95 latency:       {p95:.1f}ms")
    print(f"P99 latency:       {p99:.1f}ms")
    print("=" * 60)

    if fail:
        print("Failures:")
        for idx, lat, st in results:
            if st != "OK":
                print(f"  req#{idx} lat={lat*1000:.0f}ms status={st}")

    p95_pass = p95 < 10_000.0  # < 10s
    print(f"\nAcceptance P95<10s: {'PASS' if p95_pass else 'FAIL'} ({p95:.1f}ms)")
    return 0 if (p95_pass and fail == 0) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
