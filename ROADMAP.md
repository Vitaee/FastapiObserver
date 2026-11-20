# Roadmap

Reviewed 16 November 2026, after the 2.4.0 release. Items have no dates.
Shipped changes are in the
[changelog](https://github.com/Vitaee/FastapiObserver/blob/main/CHANGELOG.md).

## Where 2.4.0 stands

- `install_observability()` gives a FastAPI app JSON request lines, Prometheus
  metrics and OpenTelemetry traces. The package is classified Beta.
- SQLAlchemy engines passed as `db_engine`, and clients set up with
  `instrument_httpx_client()` or `instrument_requests_session()`, are counted on
  every request with tracing off. Other clients, such as Redis, are counted only
  from sampled OpenTelemetry client spans.
- `fastapiobserver.experimental.observe_operation()` observes code outside a
  request, such as a job or a CLI command. Its `operation.*` events follow the
  draft schema `0.1.0` in
  [`spec/`](https://github.com/Vitaee/FastapiObserver/tree/main/spec). Both can
  change before promotion.
- FastAPI and Starlette are required dependencies. The core and the operation
  API import without them (`tests/test_core_imports.py`), but a normal install
  still pulls them in.

## Open work

- Lifecycle outside FastAPI: there is no setup or shutdown API for
  operations. Code outside a FastAPI lifespan calls `setup_logging()`,
  `flush_logging()` and `shutdown_logging()` itself and owns its OpenTelemetry
  providers.
- Byte limits: a request holds at most 200 records, the queue at most
  `OBS_LOG_QUEUE_MAX_SIZE` records, and a message at most 32 KiB. Objects passed in
  `extra` or as non-string arguments are not measured.
- Operations: writing held records for slow requests, and the
  `db.n_plus_one`, `db.slow_query` and `http.n_plus_one` warnings, run for
  requests only; `observe_operation()` has none of them.
- Datastore adapters: Redis (`redis-py` sync and asyncio), PyMongo and
  Tortoise ORM need native hooks that count calls with tracing off, as the
  SQLAlchemy engine events do, plus tests against real servers. Each one starts
  when a real application needs it.
- Other frameworks: a generic ASGI adapter, Flask (WSGI), Django and one worker
  framework such as Celery. Each starts when a user brings a real workload.
- Every change keeps the 2.2.1 compatibility fixture
  ([baseline](https://vitaee.github.io/FastapiObserver/baseline/)), the
  installed-wheel matrix and the
  [release budgets](https://vitaee.github.io/FastapiObserver/contributing/#release-validation-budgets)
  passing.

## Not scheduled

A framework-free core package with a versioned, language-neutral event
contract, so that TypeScript, Java, Go or Rust implementations can produce the
same events and share dashboards. `spec/` holds the current draft. It needs a
second implementation, a maintainer for each language and real adopters before
it is planned.

## Out of scope

A hosted backend, a custom Collector or transport protocol, a UI, business
audit logs, health-check endpoints, profiling and browser telemetry.
