# Call recording validation — 4 November 2026

Runtime source: `c17eca702b6b8d281d8ece1f1a8733a485f0893a` on
`feature/core-call-recording`, based on released `v2.3.0a1`
(`e7e9617e548486691bf87af397985933e3e6bfc2`). Later evidence-only commits
do not change runtime code.

## Local checks

| Check | Result |
|---|---|
| Full source suite, Python 3.14.7, random seed 20261104 | 473 passed; 92.74% branch-aware coverage |
| Ruff, strict mypy, strict documentation build, workflow actionlint | Passed |
| Dependency audit | No known vulnerabilities; unpublished local project version skipped by registry audit |
| Tagged 2.2.1 compatibility fixture | 27 root exports, function parameters/defaults, settings defaults/aliases and representative FastAPI records unchanged |
| Installed wheel, Python 3.14.7/current dependencies | 86 integration checks passed outside the checkout |
| Installed wheel, Python 3.11.16/current dependencies | 86 integration checks passed outside the checkout |
| Installed wheel, Python 3.11.16/direct dependency floors | 86 integration checks passed; FastAPI 0.133.0, Starlette 1.3.1, SQLAlchemy 2.0.0 |
| Isolated no-deps wheel with Pydantic settings | Manual calls, exactly-once handles, JSON output and real SQLite executor callback pass without frameworks, clients, drivers, exporters or new import/operation threads |
| Standard runtime stress | All six scenarios pass: 300 lifespans, 120 reinstalls, 3,000 requests, overflow policies, 10-thread setup/shutdown and 40 fork checks |
| Built-wheel Compose, tracing enabled | Timing, native HTTP totals/errors/streaming, cross-service correlation, Loki, Prometheus, Tempo and no duplicate client counts pass |
| Built-wheel Compose, tracing disabled | Native diagnostics, log delivery and metrics pass without trace IDs |
| Distribution structure | Wheel/sdist build and Twine checks pass; source archive includes schema, fixtures and compatibility-probe assets |

Regression tests first reproduced wrong-owner span completion, lost completion
on unrelated threads, native HTTP deduplication using the completion context,
and SQL completion credited to another operation. New tests cover origin
ownership, absent owners, expired/copied contexts, exactly-once concurrent
completion, forked locked handles, weak lifetime/capacity reclamation, numeric
overflow and bounded error categories. Existing real SQL/HTTP integration tests
remain required. Schema vectors include invalid identifiers/error codes with
trailing newlines; structural tests complement runtime behavior checks.

The full suite emitted two unclosed SQLite `ResourceWarning`s during the existing
SQL tracing test. Similar warnings were present in the baseline; their allocation
origin was not established here. The lower-bound environment emitted the older
Starlette TestClient's AnyIO `BlockingPortal` deprecation warning. Neither warning
was suppressed or represented as a new failing assertion.

## Paired request CPU gate

Nine alternating pairs per mode, on the same macOS ARM64 runner with Python
3.14.7, FastAPI 0.162.0, Starlette 1.10.0 and Pydantic Settings 2.16.2.
Both modes meet the existing reliability and acceptance criteria against alpha 1.

| Logging mode | Baseline median | Candidate median | Median paired difference | Acceptance budget |
|---|---:|---:|---:|---:|
| Default | 50.031 µs/request | 50.962 µs/request | +0.875 µs | 5.257 µs |
| Application-owned | 34.152 µs/request | 35.149 µs/request | +0.830 µs | 3.664 µs |

Paired differences need not equal differences of medians. These are controlled
regression workloads, not universal performance guarantees.

Because this change adds per-call ownership/locking, the existing HTTP workload
was also compared in seven alternating pairs per mode after backend cleanup.
Both observations meet the same unchanged reliability/budget calculation:

| HTTP workload | Baseline median | Candidate median | Median paired difference | Acceptance budget |
|---|---:|---:|---:|---:|
| Native HTTP | 110.899 µs/request | 115.159 µs/request | +4.180 µs | 12.224 µs |
| Traced HTTP | 271.921 µs/request | 283.040 µs/request | +10.879 µs | 32.626 µs |

## Review artifacts and remote gates

Local review artifacts in `dist/call-recording/` were built once from the runtime
source and reused for installation and backend checks:

```text
c6e9f916e7e55c7c6f0bf06750b80592980b7808cb2e0ba0f399a623f6ee5ce5  fastapi_observer-2.3.0a2-py3-none-any.whl
4055635322876fb79ede534ce9b9f708be162ee44241486fb3d5d4fc1ee90266  fastapi_observer-2.3.0a2.tar.gz
```

The [runtime-source CI run](https://github.com/Vitaee/FastapiObserver/actions/runs/41905072913)
passed its Python 3.11–3.15 wheel matrix, true dependency floors, quality checks,
both backend variants and production stress/CPU gates. Its distributions artifact
ID is `13358359295`. [PR #59](https://github.com/Vitaee/FastapiObserver/pull/59)
must pass checks again after any follow-up commit. Publication must use the
release workflow's validated artifact from the merged/tagged commit; these local
hashes are review evidence, not a claim about future published bytes.

The maintainer selected the bundled application for validation. Independent
deployed-app validation is not claimed. Normal installation retains FastAPI and
Starlette. This slice adds no datastore integrations and does not promote M1/M2
or the incomplete portable contract to stable support.
