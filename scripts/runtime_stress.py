#!/usr/bin/env python3
"""Advanced runtime stress harness for fastapi-observer.

This script validates high-risk runtime behavior that unit tests cannot fully
cover, including:
- repeated install/lifespan teardown cycles
- request-path load with bounded queue pressure
- overflow policy behavior under slow sinks
- thread contention for setup/shutdown idempotency
- post-fork queue listener reinitialization (duplicate replay prevention)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import warnings
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from typing import Any

from fastapi import FastAPI

from fastapiobserver import ObservabilitySettings, install_observability
from fastapiobserver.logging import get_log_queue_stats, setup_logging, shutdown_logging
import fastapiobserver.logging.state as logging_state

try:
    import httpx
except ImportError as exc:  # pragma: no cover - runtime dependency guard
    raise SystemExit(
        "Missing dependency `httpx`. Install dev dependencies before running:\n"
        "  python -m pip install -e '.[dev,prometheus,otel]'"
    ) from exc


@dataclass(frozen=True)
class StressConfig:
    lifecycle_iterations: int
    reinstall_cycles: int
    request_count: int
    request_concurrency: int
    overflow_messages: int
    overflow_queue_size: int
    overflow_sink_delay_ms: float
    concurrent_threads: int
    concurrent_loops: int
    fork_runs: int


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    duration_seconds: float
    detail: str
    metrics: dict[str, Any] | None = None


def _profile_config(profile: str) -> StressConfig:
    profile_map: dict[str, StressConfig] = {
        "quick": StressConfig(
            lifecycle_iterations=80,
            reinstall_cycles=40,
            request_count=900,
            request_concurrency=48,
            overflow_messages=2_000,
            overflow_queue_size=20,
            overflow_sink_delay_ms=2.0,
            concurrent_threads=6,
            concurrent_loops=80,
            fork_runs=8,
        ),
        "standard": StressConfig(
            lifecycle_iterations=300,
            reinstall_cycles=120,
            request_count=3_000,
            request_concurrency=128,
            overflow_messages=5_000,
            overflow_queue_size=24,
            overflow_sink_delay_ms=2.0,
            concurrent_threads=10,
            concurrent_loops=200,
            fork_runs=40,
        ),
        "deep": StressConfig(
            lifecycle_iterations=900,
            reinstall_cycles=300,
            request_count=8_000,
            request_concurrency=256,
            overflow_messages=20_000,
            overflow_queue_size=32,
            overflow_sink_delay_ms=1.5,
            concurrent_threads=12,
            concurrent_loops=450,
            fork_runs=80,
        ),
    }
    if profile not in profile_map:
        expected = ", ".join(sorted(profile_map))
        raise ValueError(f"Unknown profile '{profile}'. Expected one of: {expected}")
    return profile_map[profile]


def _build_settings(
    *,
    app_name: str,
    queue_size: int = 128,
    overflow_policy: str = "drop_oldest",
    block_timeout_seconds: float = 0.001,
) -> ObservabilitySettings:
    return ObservabilitySettings(
        app_name=app_name,
        service="runtime-stress",
        environment="test",
        metrics_enabled=False,
        log_level="WARNING",
        log_queue_max_size=queue_size,
        log_queue_overflow_policy=overflow_policy,  # type: ignore[arg-type]
        log_queue_block_timeout_seconds=block_timeout_seconds,
    )


def _logging_state_clean() -> tuple[bool, str]:
    if logging_state._QUEUE_LISTENER is not None:
        return False, "queue_listener_not_none"
    if logging_state._MANAGED_OUTPUT_HANDLERS:
        return False, "managed_output_handlers_not_empty"
    if logging_state._MANAGED_HANDLERS:
        return False, "managed_handlers_not_empty"
    return True, "clean"


def _log_runtime_noise_reduction() -> None:
    """Keep stress harness output concise in CI logs."""
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("fastapiobserver.middleware").setLevel(logging.WARNING)


def _run_sync_check(
    name: str,
    fn: Callable[..., tuple[str, dict[str, Any]]],
    *args: Any,
) -> CheckResult:
    start = time.perf_counter()
    try:
        detail, metrics = fn(*args)
        return CheckResult(
            name=name,
            passed=True,
            duration_seconds=time.perf_counter() - start,
            detail=detail,
            metrics=metrics,
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name=name,
            passed=False,
            duration_seconds=time.perf_counter() - start,
            detail=repr(exc),
        )


async def _run_async_check(
    name: str,
    fn: Callable[..., Awaitable[tuple[str, dict[str, Any]]]],
    *args: Any,
) -> CheckResult:
    start = time.perf_counter()
    try:
        detail, metrics = await fn(*args)
        return CheckResult(
            name=name,
            passed=True,
            duration_seconds=time.perf_counter() - start,
            detail=detail,
            metrics=metrics,
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name=name,
            passed=False,
            duration_seconds=time.perf_counter() - start,
            detail=repr(exc),
        )


async def _check_lifecycle_churn(iterations: int) -> tuple[str, dict[str, Any]]:
    settings = _build_settings(app_name="stress-lifecycle", queue_size=128)

    for i in range(iterations):
        app = FastAPI()
        install_observability(app, settings, metrics_enabled=False)
        async with app.router.lifespan_context(app):
            pass
        clean, reason = _logging_state_clean()
        if not clean:
            raise RuntimeError(f"lifecycle_leak iteration={i} reason={reason}")

    return f"iterations={iterations}", {"iterations": iterations}


async def _check_same_app_reinstall(cycles: int) -> tuple[str, dict[str, Any]]:
    app = FastAPI()
    settings = _build_settings(app_name="stress-reinstall", queue_size=96)

    for i in range(cycles):
        install_observability(app, settings, metrics_enabled=False)
        count = 0
        for middleware in app.user_middleware:
            if getattr(middleware.cls, "__name__", "") == "RequestLoggingMiddleware":
                count += 1
        if count != 1:
            raise RuntimeError(
                f"request_logging_middleware_count={count} cycle={i}"
            )
        async with app.router.lifespan_context(app):
            pass

    return f"cycles={cycles}", {"cycles": cycles}


async def _check_request_stress(
    request_count: int,
    request_concurrency: int,
) -> tuple[str, dict[str, Any]]:
    app = FastAPI()

    @app.get("/items/{item_id}")
    async def items(item_id: int) -> dict[str, int]:
        return {"id": item_id}

    settings = _build_settings(app_name="stress-requests", queue_size=32)
    install_observability(app, settings, metrics_enabled=False)

    transport = httpx.ASGITransport(app=app)

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            semaphore = asyncio.Semaphore(request_concurrency)

            async def _request(i: int) -> int:
                async with semaphore:
                    response = await client.get(f"/items/{i}")
                    return response.status_code

            statuses = await asyncio.gather(*(_request(i) for i in range(request_count)))

    non_200 = sum(1 for status_code in statuses if status_code != 200)
    queue_stats = dict(get_log_queue_stats())
    shutdown_logging()

    if non_200:
        raise RuntimeError(f"non_200_responses={non_200}")

    return (
        f"requests={request_count} non_200=0 dropped_total={queue_stats.get('dropped_total', 0)}",
        {
            "requests": request_count,
            "concurrency": request_concurrency,
            "non_200": non_200,
            "queue_stats": queue_stats,
        },
    )


class _SlowHandler(logging.Handler):
    def __init__(self, delay_seconds: float) -> None:
        super().__init__()
        self.delay_seconds = delay_seconds
        self.seen = 0

    def emit(self, record: logging.LogRecord) -> None:
        self.seen += 1
        time.sleep(self.delay_seconds)


def _check_overflow_policies(
    overflow_messages: int,
    queue_size: int,
    sink_delay_ms: float,
) -> tuple[str, dict[str, Any]]:
    details: list[str] = []
    metrics: dict[str, Any] = {}

    for policy in ("drop_oldest", "drop_newest", "block"):
        settings = _build_settings(
            app_name=f"stress-overflow-{policy}",
            queue_size=queue_size,
            overflow_policy=policy,
            block_timeout_seconds=0.0005,
        )
        slow = _SlowHandler(delay_seconds=sink_delay_ms / 1000.0)

        setup_logging(
            settings,
            force=True,
            logs_mode="otlp",
            extra_handlers=[slow],
        )
        logger = logging.getLogger(f"stress.overflow.{policy}")

        emit_start = time.perf_counter()
        for i in range(overflow_messages):
            logger.warning("overflow", extra={"event": {"i": i, "policy": policy}})
        emit_elapsed_seconds = time.perf_counter() - emit_start

        time.sleep(0.7)
        stats = dict(get_log_queue_stats())
        shutdown_logging()

        dropped_total = int(stats["dropped_total"])
        blocked_total = int(stats["blocked_total"])
        if policy in ("drop_oldest", "drop_newest") and dropped_total <= 0:
            raise RuntimeError(
                f"policy={policy} expected dropped_total>0 got={dropped_total}"
            )
        if policy == "block" and blocked_total <= 0:
            raise RuntimeError(
                f"policy={policy} expected blocked_total>0 got={blocked_total}"
            )

        policy_summary = (
            f"{policy}: enqueued={stats['enqueued_total']}, dropped={dropped_total}, "
            f"blocked={blocked_total}, seen={slow.seen}, emit_s={emit_elapsed_seconds:.3f}"
        )
        details.append(policy_summary)
        metrics[policy] = {
            "queue_stats": stats,
            "slow_handler_seen": slow.seen,
            "emit_elapsed_seconds": round(emit_elapsed_seconds, 6),
        }

    return " | ".join(details), metrics


def _check_concurrent_setup_shutdown(
    threads: int,
    loops: int,
) -> tuple[str, dict[str, Any]]:
    settings = _build_settings(app_name="stress-threads", queue_size=24)
    failures: list[str] = []

    def _worker(thread_id: int) -> None:
        logger = logging.getLogger(f"stress.thread.{thread_id}")
        for i in range(loops):
            try:
                setup_logging(
                    settings,
                    force=True,
                    logs_mode="otlp",
                    extra_handlers=[logging.NullHandler()],
                )
                logger.warning("thread-check", extra={"event": {"thread": thread_id, "iter": i}})
            except Exception as exc:  # noqa: BLE001
                failures.append(f"thread={thread_id} iter={i} exc={exc!r}")
            finally:
                with suppress(Exception):
                    shutdown_logging()

    workers = [threading.Thread(target=_worker, args=(i,), daemon=True) for i in range(threads)]
    start = time.perf_counter()
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    elapsed = time.perf_counter() - start

    if failures:
        raise RuntimeError(f"concurrency_failure sample={failures[0]}")

    clean, reason = _logging_state_clean()
    if not clean:
        raise RuntimeError(f"state_not_clean_after_concurrency reason={reason}")

    return (
        f"threads={threads} loops={loops} elapsed_s={elapsed:.3f}",
        {"threads": threads, "loops": loops, "elapsed_seconds": round(elapsed, 6)},
    )


def _check_fork_duplication_impl(runs: int) -> tuple[str, dict[str, Any]]:
    if not hasattr(os, "fork"):
        return "skipped (os.fork unavailable on this platform)", {"skipped": True}

    duplicate_parent_before_runs = 0
    missing_child_runs = 0

    for _ in range(runs):
        fd, path = tempfile.mkstemp(prefix="fastapiobserver_fork_", suffix=".ndjson")
        os.close(fd)

        handler = logging.FileHandler(path)
        settings = _build_settings(app_name="stress-fork", queue_size=128)
        setup_logging(settings, force=True, logs_mode="otlp", extra_handlers=[handler])
        logger = logging.getLogger("stress.fork")

        logger.warning("parent-before-fork", extra={"event": {"phase": "parent-before"}})

        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r".*multi-threaded, use of fork\(\).*deadlocks.*",
                category=DeprecationWarning,
            )
            child_pid = os.fork()
        if child_pid == 0:
            try:
                logger.warning("child-after-fork", extra={"event": {"phase": "child"}})
                time.sleep(0.12)
            finally:
                shutdown_logging()
            os._exit(0)

        os.waitpid(child_pid, 0)
        logger.warning("parent-after-fork", extra={"event": {"phase": "parent-after"}})
        time.sleep(0.2)
        shutdown_logging()
        handler.close()

        with open(path, "r", encoding="utf-8") as file_obj:
            messages: list[str] = []
            for raw in file_obj:
                line = raw.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                message = payload.get("message")
                if isinstance(message, str):
                    messages.append(message)

        if messages.count("parent-before-fork") > 1:
            duplicate_parent_before_runs += 1
        if "child-after-fork" not in messages:
            missing_child_runs += 1

        with suppress(FileNotFoundError):
            os.remove(path)

    if duplicate_parent_before_runs or missing_child_runs:
        raise RuntimeError(
            "fork_check_failed "
            f"runs={runs} duplicate_parent_before_runs={duplicate_parent_before_runs} "
            f"missing_child_runs={missing_child_runs}"
        )

    return (
        f"runs={runs} duplicate_parent_before_runs=0 missing_child_runs=0",
        {
            "runs": runs,
            "duplicate_parent_before_runs": duplicate_parent_before_runs,
            "missing_child_runs": missing_child_runs,
        },
    )


def _run_fork_check_subprocess(runs: int) -> tuple[str, dict[str, Any]]:
    if not hasattr(os, "fork"):
        return "skipped (os.fork unavailable on this platform)", {"skipped": True}

    cmd = [
        sys.executable,
        os.path.abspath(__file__),
        "--fork-check-child",
        "--fork-runs",
        str(runs),
    ]
    completed = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        timeout=max(60, runs * 2),
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        detail = stderr or stdout or f"child_exit_code={completed.returncode}"
        raise RuntimeError(f"fork_subprocess_failed: {detail}")

    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("fork_subprocess_failed: no child output")

    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"fork_subprocess_failed: invalid child json payload={lines[-1]!r}"
        ) from exc

    detail = payload.get("detail")
    metrics = payload.get("metrics")
    if not isinstance(detail, str) or not isinstance(metrics, dict):
        raise RuntimeError(f"fork_subprocess_failed: unexpected child payload={payload!r}")
    return detail, metrics


def _run_fork_check_child(runs: int) -> int:
    try:
        detail, metrics = _check_fork_duplication_impl(runs)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": repr(exc)}))
        return 1

    print(json.dumps({"detail": detail, "metrics": metrics}, sort_keys=True))
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run advanced runtime stress checks for fastapi-observer.",
    )
    parser.add_argument(
        "--profile",
        choices=("quick", "standard", "deep"),
        default="standard",
        help="Stress profile that controls iteration/load levels (default: standard).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit final machine-readable JSON report in addition to text summary.",
    )
    parser.add_argument(
        "--fork-check-child",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--fork-runs",
        type=int,
        default=0,
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


async def _run(config: StressConfig) -> list[CheckResult]:
    results: list[CheckResult] = []

    results.append(
        await _run_async_check(
            "lifecycle_churn",
            _check_lifecycle_churn,
            config.lifecycle_iterations,
        )
    )
    results.append(
        await _run_async_check(
            "same_app_reinstall",
            _check_same_app_reinstall,
            config.reinstall_cycles,
        )
    )
    results.append(
        await _run_async_check(
            "request_stress",
            _check_request_stress,
            config.request_count,
            config.request_concurrency,
        )
    )
    results.append(
        _run_sync_check(
            "overflow_policies",
            _check_overflow_policies,
            config.overflow_messages,
            config.overflow_queue_size,
            config.overflow_sink_delay_ms,
        )
    )
    results.append(
        _run_sync_check(
            "concurrent_setup_shutdown",
            _check_concurrent_setup_shutdown,
            config.concurrent_threads,
            config.concurrent_loops,
        )
    )
    results.append(
        _run_sync_check("fork_duplication", _run_fork_check_subprocess, config.fork_runs)
    )
    return results


def _print_summary(results: list[CheckResult]) -> None:
    print("=== Runtime Stress Validation ===")
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(
            f"{result.name}: {status} | "
            f"duration_s={result.duration_seconds:.3f} | {result.detail}"
        )


def _as_report(results: list[CheckResult], profile: str) -> dict[str, Any]:
    return {
        "profile": profile,
        "timestamp_epoch_seconds": time.time(),
        "all_passed": all(result.passed for result in results),
        "results": [asdict(result) for result in results],
    }


def main() -> None:
    args = _parse_args()
    if args.fork_check_child:
        raise SystemExit(_run_fork_check_child(args.fork_runs))

    config = _profile_config(args.profile)

    _log_runtime_noise_reduction()
    results = asyncio.run(_run(config))

    _print_summary(results)

    report = _as_report(results, args.profile)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))

    if not report["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
