# Stream timing and diagnostic events: local validation

Reviewed 5 November 2026. Prepared version: `2.3.0a4` (not published by this
change). Runtime source: `56166a3ce85e4191630ee89d295492193ed3f6e9`.
Subsequent CI/documentation changes do not alter that runtime source.

## Compatibility and resource cleanup

The full suite passed **532 tests**, with **93.05% coverage**, on Python 3.14.7.
`ResourceWarning` and `PytestUnraisableExceptionWarning` were treated as errors.
Ruff, strict mypy, strict documentation build and workflow lint passed. The
dependency audit found no known vulnerabilities in third-party packages; the
unpublished local package version is skipped by the PyPI audit. The frozen
2.2.1 fixture retains its existing fields; new timing fields
have separate deterministic ASGI and real-server assertions.

SQLite warnings originated in the synchronous integration case: its pool kept
connections opened on the TestClient thread, then disposal attempted to close
them on the main thread. `NullPool` closes each connection at context exit on
its owning thread. The sync/async tests assert two connections opened and two
closed. No warning suppression was added. Sink-test handlers also close
explicitly. A Prometheus cleanup test's import-order failure was reproduced
against stable source and fixed by importing the client before changing its
temporary multiprocess directory.

## CPU overhead

The first candidate passed the rolling comparison but failed the cumulative
stable comparison. Profiling identified nested-mapping copies/runtime typing
checks in log formatting, repeated request-state lookups and disabled
metrics/hook work. The final source removes that work and reuses the existing
elapsed time for a completed single-body response. Sanitizers and metadata
remain enabled.

Nine alternating pairs per mode used the existing harness on the same Apple
ARM64/macOS runner, Python 3.14.7, FastAPI 0.162.0, Starlette 1.10.0 and
pydantic-settings 2.16.2. Values below are microseconds of CPU overhead per
request, including queued formatting. Paired differences need not equal the
difference between the two medians.

| Baseline | Logging | Baseline median | Candidate median | Paired change | Existing budget | Result |
|---|---|---:|---:|---:|---:|---|
| Stable 2.2.1 | Default | 49.6265 | 55.1765 | +5.3755 | 5.6736 | Pass |
| Stable 2.2.1 | Application-owned | 34.3970 | 38.4640 | +3.5210 | 4.9594 | Pass |
| Alpha 3 | Default | 55.5470 | 55.0950 | -0.0195 | 6.5866 | Pass |
| Alpha 3 | Application-owned | 37.3850 | 38.2975 | +1.1295 | 4.3486 | Pass |

All comparisons satisfied the existing reliability check. Budgets retain the
10% baseline allowance plus estimated paired noise; a pass is not a claim of
zero cost or an application latency SLA. Shared validation now retains both
rolling and stable comparisons and their raw samples.

```bash
PYTHONPATH=src python scripts/compare_overhead.py --baseline v2.2.1 --output overhead-stable.json
PYTHONPATH=src python scripts/compare_overhead.py --baseline v2.3.0a3 --output overhead-alpha.json
```

## Installed distribution and backends

The wheel and sdist were built once from the runtime revision and passed Twine
checks. Wheel integration tests ran outside the checkout:

| Environment | Selected versions | Result |
|---|---|---|
| Python 3.14.7, current dependencies | FastAPI 0.162.0 / Starlette 1.10.0 / SQLAlchemy 2.0.57 | 163 passed |
| Python 3.11, current dependencies | FastAPI 0.162.0 / Starlette 1.10.0 / SQLAlchemy 2.0.57 | 163 passed |
| Python 3.11, lowest direct dependencies | FastAPI 0.133.0 / Starlette 1.3.1 / SQLAlchemy 2.0.0 | 163 passed |

The minimum set emitted Starlette's existing BlockingPortal deprecation warning;
no resource or unraisable warnings occurred. Requirements were supplied directly
alongside the wheel, so `lowest-direct` tested the declared floors. A separate
installation with only the wheel and pydantic-settings ran plain operations
without FastAPI, Starlette, SQLAlchemy, OpenTelemetry, Hypothesis or new threads.

The exact wheel passed the three-service Compose lab with tracing enabled and
disabled. Loki retained response/stream/background timing and native counts;
Prometheus retained response status/metrics. Tempo received both requests' N+1
events despite process-wide warning suppression. Real SDK tests cover the
three-event cap, concurrent admission, pool snapshots, safe attributes, ended
spans and failing diagnostic callbacks. The standard runtime stress profile
passed lifecycle churn, reinstallation, concurrent requests, queue overflow,
concurrent logging setup/shutdown and fork scenarios. Validation containers and
volumes were removed afterward.

| Distribution | SHA-256 |
|---|---|
| `fastapi_observer-2.3.0a4-py3-none-any.whl` | `f62ad32191ade42d44d8e9619ba46c95e3ddba8bb3e240ad1bbc992eca4b9959` |
| `fastapi_observer-2.3.0a4.tar.gz` | `d81aa4cf2db345b3b7858545d45121e82d13fa41a9718fde99389eb6e52438d4` |

This is bundled-application evidence, not independent adopter validation.
Timing measures ASGI sends, not client receipt. Pool pressure does not establish
request wait time. Existing providers retain sampling/export ownership; the
experimental core's generic lifecycle, payload byte bounds and portable-contract
work remain open in the roadmap.
