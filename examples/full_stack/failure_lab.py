"""Trigger the demo's failures: python3 examples/full_stack/failure_lab.py --help."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from urllib.request import urlopen

Request = Callable[[str], str]


def blocked_loop(get: Request) -> None:
    get("/lab/block")
    print("event_loop.blocked: inspect the stack; use await asyncio.sleep as /slow does.")


def exhausted_pool(get: Request) -> None:
    with ThreadPoolExecutor(max_workers=1) as workers:
        held = workers.submit(get, "/lab/db-pool?seconds=2")
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            metrics = get("/metrics")
            if any(
                line.startswith("fastapiobserver_db_pool_checked_out{")
                and float(line.rsplit(" ", 1)[1]) > 0
                for line in metrics.splitlines()
            ):
                # The scrape may have entered before the connection was acquired.
                # A new request now observes the full pool at middleware entry.
                get("/items/1")
                break
            time.sleep(0.02)
        else:
            raise RuntimeError("The demo database pool did not become occupied.")
        held.result()
    print("db.pool_exhausted: one connection is held; release it before unrelated I/O.")


def repeated_queries(get: Request) -> None:
    repeated = json.loads(get("/lab/n-plus-one"))
    batched = json.loads(get("/lab/batched"))
    if repeated != batched or len(batched) != 10:
        raise RuntimeError("The batched example did not return the same ten items.")
    print("db.n_plus_one: compare db.count on /lab/n-plus-one (11) and /lab/batched (1).")


SCENARIOS = {
    "event-loop": blocked_loop,
    "db-pool": exhausted_pool,
    "n-plus-one": repeated_queries,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Trigger failures in the running full-stack demo.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--scenario", choices=["all", *SCENARIOS], default="all")
    args = parser.parse_args()

    def get(path: str) -> str:
        with urlopen(args.base_url.rstrip("/") + path, timeout=10) as response:
            return response.read().decode()

    try:
        get("/health")
        selected = SCENARIOS.values() if args.scenario == "all" else [SCENARIOS[args.scenario]]
        for scenario in selected:
            scenario(get)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Failure lab: {exc}", file=sys.stderr)
        return 1
    print("Read the named warnings in app-a's logs or Grafana Explore → Loki.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
