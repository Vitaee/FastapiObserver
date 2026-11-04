# Experimental core validation — 4 November 2026

Implementation source: `f297d2f6559022f92be23f52c7697914da595fe7` on
`feature/experimental-python-core`. The follow-up commit records this report and
corrects dependency-floor resolution; it does not change runtime code.
Released comparison source: `v2.2.1`.

## Local evidence

| Check | Result |
|---|---|
| Full source suite, Python 3.14.7, fixed random seed 20261104 | 441 passed; 92.85% branch-aware coverage |
| Ruff, strict mypy, strict documentation build, workflow actionlint | Passed |
| Dependency audit | No known dependency vulnerabilities; unpublished local project version skipped by registry audit |
| Tagged compatibility probe | All 27 root exports, function parameters/defaults, configuration defaults/aliases and representative FastAPI records match 2.2.1 |
| Bundled application with released source | Real Uvicorn, concurrent requests, failure sanitization and retained access logging passed |
| Installed wheel, Python 3.14.7/current dependencies | 55 integration checks passed, including the same bundled application |
| Installed wheel, Python 3.11.16/current dependencies | 55 integration checks passed |
| Installed wheel, Python 3.11.16/explicit lower bounds | 55 integration checks passed; FastAPI 0.133.0, Starlette 1.3.1, SQLAlchemy 2.0.0 |
| Isolated wheel separation | With only Pydantic settings dependencies installed, manual operation and JSON output pass; no frameworks, clients, drivers, exporters or new threads |
| Python-only example | Two real SQLite queries, one loopback HTTP call, forwarded request ID and correlated JSON completion |
| Standard runtime stress | All six scenarios passed: 300 lifespans, 120 reinstalls, 3,000 requests, overflow policies, 10-thread setup/shutdown and 40 fork checks |
| Built-wheel Compose export, tracing enabled | Response/background timing, HTTP totals/errors/streaming, cross-service correlation, Loki, Prometheus and Tempo passed |
| Built-wheel Compose export, tracing disabled | Same native diagnostics/log delivery and metrics checks passed without trace IDs |

The full suite emitted two SQLite `ResourceWarning`s during existing tests
(`test_sqlalchemy_integration.py` and `test_dx_parity.py`). Their allocation
origin was not established here; they are recorded rather than described as
new failures or silently suppressed. The lower-bound run also emitted the
older Starlette TestClient's AnyIO `BlockingPortal` deprecation warning.

## Paired CPU comparison

Nine alternating pairs per mode on the same macOS ARM64 runner, Python 3.14.7,
with identical FastAPI 0.162.0, Starlette 1.10.0 and Pydantic Settings 2.16.2.
Both runs met the existing reliability and acceptance criteria.

| Logging mode | Baseline median | Candidate median | Median paired difference | Acceptance budget |
|---|---:|---:|---:|---:|
| Default | 50.044 µs/request | 50.092 µs/request | +0.318 µs | 5.720 µs |
| Application-owned | 34.685 µs/request | 34.288 µs/request | −0.324 µs | 4.108 µs |

Differences are calculated per pair; their median need not equal the difference
between the two medians. These are controlled-workload regression checks, not
universal performance guarantees.

## Review artifacts and publication

The local wheel and source archive are in `dist/experimental-final/`, built from
the implementation commit. Their filenames and SHA-256 hashes are:

```text
60b1b8acd2ea5dc9ad0a14ecff8002f1631b8791b15a8db2f1fefdbe1a43aea3  fastapi_observer-2.3.0a1-py3-none-any.whl
ed9a7b43656f4bb5156037f61c35d5936f4061edffb7eea858fdb1fbecd8c6ad  fastapi_observer-2.3.0a1.tar.gz
```

These are local review artifacts, not a PyPI publication. The release workflow
must build and validate its own immutable artifact from the merged tagged
commit before publication. Prereleases do not replace the latest stable GitHub
release. No release tag has been created by this change.

The maintainer selected the bundled application for runnable validation after
reporting previous real FastAPI use. No independent deployed-app canary is
claimed. Normal installation still includes FastAPI and Starlette; the
experimental module is not promotion of the full generic-core roadmap.

Remote PR checks must pass before merge/release. Local validation does not stand
in for the Linux/Python 3.11–3.15 wheel matrix or its complete lower-bound suite.
See [PR #58](https://github.com/Vitaee/FastapiObserver/pull/58), the
[baseline inventory](../baseline.md) and [experimental contract](../experimental-core.md).
