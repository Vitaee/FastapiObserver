# 0016: Prove Python operations through existing seams

**Status:** accepted for the opt-in experiment · **Reviewed:** 2026-11-04

## Context

SQLAlchemy and HTTP helpers already count native calls without tracing, but
their shared state belongs to FastAPI requests. The maintainer requested a
Python-only proof while preserving existing FastAPI behavior and one release
distribution. Extracting a second logging/telemetry pipeline would duplicate
redaction, correlation, lifecycle and count ownership.

## Decision

- Add `fastapiobserver.experimental.observe_operation` using the existing
  shared state and client hooks. Keep all 27 stable root exports unchanged.
- Support sync/async and nested scopes with ContextVar token restoration.
  Child counts are exclusive; closed experimental state rejects late writes
  and releases totals, fingerprints, held logs and user context.
- Borrow logging, clients and providers. Terminal records capture correlation
  before queues; the application owns handlers, sanitization and final flush.
- Load framework root exports lazily and treat plugin framework annotations as
  type-only imports. Keep current installation dependencies and fork-reset paths.
- Release as an alpha in the existing distribution. Separate package naming,
  broad adapters and the portable contract remain later decisions.

## Evidence

`test_release_compatibility.py` compares public interfaces, configuration and
representative FastAPI output with a fixture from the v2.2.1 tag.
`test_core_imports.py` rejects framework imports and runs the real SQLite/HTTP
example. `test_experimental_operations.py` exercises nesting, threads, concurrent
tasks, cancellation, late completion, log sampling/failures and native counts at
trace sampling ratios 0/0.1/1. The existing production gates remain required.

## What it cost

The generic operation still reuses an internally request-named state rather
than moving every module before it has a second real consumer. Framework-free
installation is deferred; normal installs retain the FastAPI dependencies.
Operation schema 0.1.0 is experimental. Automatic slow/N+1 diagnostics and a
portable lifecycle/provider API are outside this first proof. The bundled
application validates compatibility; independent deployed-app validation is
not claimed.
