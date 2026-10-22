"""Latency check for ``POST /v1/reports`` (M08 definition of done: p95 < 100 ms locally).

Sends N sequential reports to a running API and reports percentiles of the server-side time
(``Server-Timing: app;dur=…``) and of the client round trip.

    make up migrate seed && make api            # in one terminal
    uv --directory backend run python ../tools/bench_reports.py [--n 200] [--url http://localhost:8000]

Reporters are rotated every 25 reports (the per-reporter limit is 30/hour), each signing in from a
different X-Forwarded-For address in the 198.18.0.0/15 benchmarking range (the auth limit is per IP).
"""

import argparse
import math
import random
import re
import statistics
import sys
import time
import uuid
from datetime import UTC, datetime

import httpx

REPORTS_PER_REPORTER = 25
SHOPS = ["shop-a", "shop-b", "shop-c", "shop-d", "shop-e"]
_DUR = re.compile(r"app;dur=([0-9.]+)")


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


def sign_in(client: httpx.Client) -> str:
    ip = f"198.18.{random.randint(0, 255)}.{random.randint(1, 254)}"
    response = client.post(
        "/v1/auth/device", json={"device_id": str(uuid.uuid4())}, headers={"x-forwarded-for": ip}
    )
    response.raise_for_status()
    return str(response.json()["token"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--n", type=int, default=200)
    parser.add_argument("--p95-budget-ms", type=float, default=100.0)
    args = parser.parse_args()

    server_ms: list[float] = []
    client_ms: list[float] = []
    statuses: dict[int, int] = {}
    with httpx.Client(base_url=args.url, timeout=10) as client:
        token = ""
        for i in range(args.n):
            if i % REPORTS_PER_REPORTER == 0:
                token = sign_in(client)
            body = {
                "client_report_id": str(uuid.uuid4()),
                "product": "onion",
                "shop_id": SHOPS[i % len(SHOPS)],
                # 40 + i for 500 g: always > 1% apart, so never a duplicate.
                "price": 40 + i % REPORTS_PER_REPORTER,
                "quantity": 500,
                "unit": "g",
                "observed_at": datetime.now(UTC).isoformat(),
            }
            started = time.perf_counter()
            response = client.post(
                "/v1/reports", json=body, headers={"authorization": f"Bearer {token}"}
            )
            client_ms.append((time.perf_counter() - started) * 1000)
            statuses[response.status_code] = statuses.get(response.status_code, 0) + 1
            match = _DUR.search(response.headers.get("server-timing", ""))
            if match:
                server_ms.append(float(match.group(1)))

    print(f"{args.n} sequential POST /v1/reports -> status counts {statuses}")
    for label, values in (("server (Server-Timing)", server_ms), ("client round trip", client_ms)):
        print(
            f"  {label:24s} p50 {statistics.median(values):6.1f} ms   "
            f"p95 {percentile(values, 95):6.1f} ms   p99 {percentile(values, 99):6.1f} ms   "
            f"max {max(values):6.1f} ms"
        )
    p95 = percentile(server_ms, 95)
    ok = statuses.get(202, 0) == args.n and p95 < args.p95_budget_ms
    print(f"{'PASS' if ok else 'FAIL'}: server p95 {p95:.1f} ms (budget {args.p95_budget_ms:.0f} ms)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
