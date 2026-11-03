# 0014: Diagnosis works without tracing, and is on where it's cheap

**Status:** accepted · **Reviewed:** 2026-11-01 · Narrows [0006](0006-delegate-to-opentelemetry.md) for SQLAlchemy query counts

## Context

The request line's `db` totals and the `db.n_plus_one` warning came from OpenTelemetry's client spans. They worked only with tracing on, only for sampled requests, and only where SQLAlchemy's instrumentation runs, which is not on SQLAlchemy 2.1 (`opentelemetry-instrumentation-sqlalchemy` 0.66b2 supports `sqlalchemy < 2.1.0`).

The event-loop watchdog and held `DEBUG` logs were opt-in everywhere, so the library reported a blocked loop only to someone who already suspected one.

## Decision

- Queries on the SQLAlchemy engines passed as `db_engine`, or set up with `instrument_sqlalchemy()`, are counted from SQLAlchemy's `before_cursor_execute` and `after_cursor_execute` events ([`db_queries.py`](https://github.com/Vitaee/FastapiObserver/blob/main/src/fastapiobserver/db_queries.py)). The span processor skips spans from SQLAlchemy's instrumentation, so a query counts once. Other clients, such as Redis or HTTP, are still counted from their spans.
- The `production` profile turns the watchdog on at 250 ms.
- Held `DEBUG` logs (`OBS_LOG_LEVEL_ON_ERROR`) stay opt-in, in every profile.

## Evidence

- **Tests:** in [`test_request_insights.py`](https://github.com/Vitaee/FastapiObserver/blob/main/tests/test_request_insights.py), `test_sqlalchemy_queries_are_counted_and_n_plus_one_reported_without_tracing`, `test_async_sqlalchemy_queries_are_counted_without_tracing` and `test_sqlalchemy_queries_count_once_with_tracing_on`; the profile in `test_production_profile_enforces_strict` in [`test_profiles.py`](https://github.com/Vitaee/FastapiObserver/blob/main/tests/test_profiles.py).
- **The watchdog costs nothing measurable per request.** [`request_cpu.py`](https://github.com/Vitaee/FastapiObserver/blob/main/examples/benchmarks/request_cpu.py) `watchdog` against `default`, three runs each on an Apple M1: 51.6 to 52.4 µs of CPU per request against 51.8 to 52.2. Idle, it uses about 2.6 ms of CPU per second, 0.25% of one core per worker, from waking every 125 ms.
- **Each held `DEBUG` record costs about 4 µs.** `request_cpu.py held absolute` against `default absolute`: 75.6 to 82.7 µs with one `DEBUG` line per request, and 81.0 to 158.3 µs with twenty (`DEBUG_LINES=20`), about what `httpcore` writes for two outbound calls. The cost follows how much every library in the app logs at `DEBUG`, which the library can't bound.

## What it cost

- **An engine traced with OpenTelemetry's `SQLAlchemyInstrumentor` directly, and not passed as `db_engine` or through `instrument_sqlalchemy()`, is no longer counted**, since its spans are skipped.
- **The library depends on SQLAlchemy's event names**, which have been stable since 0.9.
- **Production apps run one more thread per worker**, and log `event_loop.blocked` for any stall over 250 ms, including a CPU-heavy endpoint written as `async def`. Setting `OBS_LOOP_BLOCK_THRESHOLD_MS` changes or, with a large value, effectively silences it.
