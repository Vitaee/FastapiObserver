# 0016: Prove Python operations through existing seams

**Status:** accepted, for the opt-in experiment · **Reviewed:** 2026-11-04

## Context

The SQLAlchemy and HTTP helpers already count calls without tracing, but they store the counts on a FastAPI request's state. A job, a CLI command or a message handler outside a request got nothing. A second logging and telemetry pipeline for that code would repeat redaction, correlation, lifecycle and call counting.

## Decision

- `fastapiobserver.experimental.observe_operation()` reuses the request state and the client hooks ([`experimental.py`](https://github.com/Vitaee/FastapiObserver/blob/v2.3.0/src/fastapiobserver/experimental.py)). The 27 root exports stay as they are.
- It works with `with` and `async with`, and nests. Each scope resets its context variables with their tokens on exit. A child's counts are its own. A closed operation refuses late writes and drops its totals, fingerprints, held records and user context.
- It borrows logging, clients and providers. The terminal record gets its correlation fields before any queue. The application owns handlers, sanitizing and the final flush.
- Root exports that need FastAPI load on first access, and the plugin module imports framework types for type checking only. Installation dependencies and the fork-reset module paths stay the same.
- It ships as an alpha in the existing `fastapi-observer` distribution. A separate package name, more adapters and a portable contract are later decisions.

## Evidence

- [`test_release_compatibility.py`](https://github.com/Vitaee/FastapiObserver/blob/v2.3.0/tests/test_release_compatibility.py) compares the public interface, configuration and representative FastAPI output with a fixture taken from the `v2.2.1` tag.
- [`test_core_imports.py`](https://github.com/Vitaee/FastapiObserver/blob/v2.3.0/tests/test_core_imports.py): `test_experimental_operation_never_imports_frameworks_or_optional_clients` fails if an operation imports a framework or an optional client, and `test_python_operation_example_uses_real_sql_and_loopback_http_without_frameworks` runs the SQLite and HTTP example.
- [`test_experimental_operations.py`](https://github.com/Vitaee/FastapiObserver/blob/v2.3.0/tests/test_experimental_operations.py) covers nesting, threads, concurrent tasks, cancellation, late completion, log sampling and failures. `test_existing_tracing_bridges_do_not_change_native_operation_totals` runs at trace sampling ratios 0, 0.1 and 1.

## What it cost

- Operations reuse `RequestState`, which is named for requests. Moving every module waits for a second real consumer.
- A normal install still pulls in FastAPI and Starlette.
- The operation schema, `0.1.0`, is experimental. Operations get no slow-request promotion, no N+1 warnings and no lifecycle or provider API.
- The bundled example is the only application validated.
