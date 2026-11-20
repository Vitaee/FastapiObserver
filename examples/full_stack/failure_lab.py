"""Trigger the demo's failures: python3 examples/full_stack/failure_lab.py --help."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

Request = Callable[[str], str]
ROOT = Path(__file__).resolve().parents[2]


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
    print(
        "db.pool_exhausted & db.connection_held_idle: "
        "one connection sat idle; release it before unrelated I/O."
    )


def repeated_queries(get: Request) -> None:
    repeated = json.loads(get("/lab/n-plus-one"))
    batched = json.loads(get("/lab/batched"))
    if repeated != batched or len(batched) != 10:
        raise RuntimeError("The batched example did not return the same ten items.")
    print("db.n_plus_one: compare db.count on /lab/n-plus-one (11) and /lab/batched (1).")


def slow_query(get: Request) -> None:
    get("/lab/slow-query")
    print(
        "db.slow_query: SQL statement exceeded 1s; "
        "check statement_fingerprint and code location."
    )


def db_during_http(get: Request) -> None:
    get("/lab/db-during-http")
    print(
        "db.connection_held_during_http: "
        "DB connection stayed checked out across an outbound HTTP call."
    )


def http_n_plus_one(get: Request) -> None:
    get("/lab/http-n-plus-one")
    print("http.n_plus_one: 10 repeated outbound HTTP calls to the same route pattern.")


def downstream_calls(get: Request) -> None:
    get("/lab/downstream?seconds=0.15&calls=2")
    print("http.count=2: http.ms sums both concurrent calls, even with tracing disabled.")


def validation_error(get: Request) -> None:
    try:
        get("/items/not-an-int")
    except HTTPError as exc:
        if exc.code != 422:
            raise
        print("validation_errors: 422 field locations captured without raw input values.")
        return
    raise RuntimeError("Expected HTTP 422 from /items/not-an-int.")


def background_failure(get: Request) -> None:
    get("/lab/background?seconds=0.3&fail=true")
    print("request.failed: the response was 202; app_duration_ms includes the later failure.")


SCENARIOS = {
    "event-loop": blocked_loop,
    "db-pool": exhausted_pool,
    "n-plus-one": repeated_queries,
    "slow-query": slow_query,
    "db-during-http": db_during_http,
    "http-n-plus-one": http_n_plus_one,
    "downstream": downstream_calls,
    "validation": validation_error,
    "background": background_failure,
}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _local_demo_server() -> Iterator[tuple[str, Path]]:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="fastapiobserver-lab-") as tmp_dir:
        tmp = Path(tmp_dir)
        log_path = tmp / "server.log"
        err_path = tmp / "server.err"
        env = os.environ.copy()
        pythonpath = env.get("PYTHONPATH")
        src_path = str(ROOT / "src")
        env["PYTHONPATH"] = f"{src_path}{os.pathsep}{pythonpath}" if pythonpath else src_path
        env.update(
            {
                "OBS_SERVICE_NAME": "failure-lab",
                "OBS_METRICS_ENABLED": "true",
                "OBS_LOOP_BLOCK_THRESHOLD_MS": "100",
                "OBS_OTEL_ENABLED": "false",
                "OBS_OTEL_LOGS_ENABLED": "false",
                "OBS_OTEL_METRICS_ENABLED": "false",
                "OTEL_SDK_DISABLED": "true",
                "TARGET_B": base_url,
                "TARGET_C": base_url,
            }
        )
        with log_path.open("w", encoding="utf-8") as stdout_file, err_path.open(
            "w", encoding="utf-8"
        ) as stderr_file:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "examples.full_stack.app:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--no-access-log",
                ],
                cwd=ROOT,
                env=env,
                stdout=stdout_file,
                stderr=stderr_file,
            )
            try:
                deadline = time.monotonic() + 10.0
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        stderr_text = err_path.read_text(encoding="utf-8", errors="replace")
                        raise RuntimeError(
                            "Local demo server exited early. Ensure dependencies are installed "
                            '(e.g. `pip install "fastapi-observer[prometheus]" uvicorn '
                            f'sqlalchemy httpx2`).\n{stderr_text}'
                        )
                    try:
                        with urlopen(f"{base_url}/health", timeout=1) as response:
                            if response.status == 200:
                                break
                    except (OSError, URLError):
                        time.sleep(0.05)
                else:
                    raise RuntimeError("Timed out waiting for local demo server on /health.")
                yield base_url, log_path
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)


def _print_local_diagnostics(log_path: Path) -> None:
    print("\n--- Captured JSON warnings, errors, and diagnostic lines ---")
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = payload.get("event")
        has_diagnostic = isinstance(event, dict) and (
            "diagnostics" in event or "validation_errors" in event
        )
        if payload.get("level") in ("WARNING", "ERROR") or has_diagnostic:
            print(line)


def _run_scenarios(base_url: str, scenario_name: str) -> None:
    def get(path: str) -> str:
        with urlopen(base_url.rstrip("/") + path, timeout=10) as response:
            return response.read().decode()

    get("/health")
    selected = SCENARIOS.values() if scenario_name == "all" else [SCENARIOS[scenario_name]]
    for scenario in selected:
        scenario(get)


def main() -> int:
    parser = argparse.ArgumentParser(description="Trigger failures in the running full-stack demo.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--scenario", choices=["all", *SCENARIOS], default="all")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Start an ephemeral local demo server, run scenarios, and print JSON diagnostics.",
    )
    args = parser.parse_args()

    try:
        if args.local:
            with _local_demo_server() as (base_url, log_path):
                _run_scenarios(base_url, args.scenario)
                if args.scenario in ("all", "background"):
                    time.sleep(0.5)
                _print_local_diagnostics(log_path)
            return 0
        _run_scenarios(args.base_url, args.scenario)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Failure lab: {exc}", file=sys.stderr)
        return 1
    print("Read the diagnostics in app-a's logs or Grafana Explore → Loki.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
