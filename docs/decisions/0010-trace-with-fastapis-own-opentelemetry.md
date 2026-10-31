# 0010: Trace with FastAPI's own OpenTelemetry

**Status:** accepted · **Reviewed:** 2026-10-31

## Context

FastAPI 0.142.0 ([fastapi/fastapi#16403](https://github.com/fastapi/fastapi/pull/16403)) added native OpenTelemetry. Once a global provider is set, `FastAPI` itself creates request spans and HTTP metrics, and exports log records for validation failures and unhandled exceptions. It steps aside when OpenTelemetry's FastAPI instrumentation is in the middleware stack.

fastapi-observer added that instrumentation only when tracing was on. So with OTLP log export on and tracing off, FastAPI exported its own error logs, with exception messages and stack traces, through the logger provider fastapi-observer had set up. Those logs never passed through fastapi-observer's redaction ([0004](0004-redact-before-anything-leaves.md)), and they duplicated its `request.failed` lines.

## Decision

- On FastAPI 0.142 and later, tracing uses FastAPI's own support instead of `FastAPIInstrumentor`. Excluded URLs, including routes found when the app starts, go to FastAPI's `exclude` hook ([`otel/native.py`](https://github.com/Vitaee/FastapiObserver/blob/main/src/fastapiobserver/otel/native.py)). Sampling, trace IDs in logs, and the query and HTTP-call totals from [0008](0008-one-state-object-per-request.md) work as before, because they live on the tracer provider or in the logs.
- Whenever fastapi-observer exports OTLP logs, it turns FastAPI's own log export off. Our pipeline already logs both events, redacted.
- On older FastAPI, or with `FastAPI(telemetry={"tracing": False})`, tracing uses the instrumentation as before.

## Evidence

- [`test_native_telemetry.py`](https://github.com/Vitaee/FastapiObserver/blob/main/tests/test_native_telemetry.py) checks three things. Requests are traced by FastAPI, with excluded and hidden routes skipped, and the `request.completed` line carries the span's trace ID. The instrumentation is used when FastAPI's tracing is off. No record from FastAPI's own logger reaches the OTLP exporter, with tracing on or off. Before the change, the last test failed with tracing off.
- CPU that fastapi-observer and tracing add to a request, with every request traced and the spans discarded, from [`examples/benchmarks/request_cpu.py`](https://github.com/Vitaee/FastapiObserver/blob/main/examples/benchmarks/request_cpu.py) on an Apple M1, median of 5 runs. It's 174.5 µs with FastAPI's tracing (`traced`) and 187.0 µs with the instrumentation (`traced-instrumentation`), despite three more spans per request. Untraced, it's 50.4 µs, unchanged.

## What it cost

- **Settings go through a private attribute.** FastAPI takes telemetry settings only as `FastAPI(telemetry=...)`, and `install_observability()` runs after the app exists. So it edits `app._telemetry`, which FastAPI reads on every request. If that changes, tracing falls back to the instrumentation, and the tests above fail.
- **Different OTLP HTTP metrics.** They are FastAPI's `http.server.request.duration` in seconds and `http.server.active_requests`. The instrumentation's `http.server.duration` in milliseconds and `http.server.response.size` are gone.
- **No exception event on the server span.** A failed request's span has `error.type` and an `ERROR` status. The exception message and stack trace are only in the redacted log line with the same trace ID.
- **FastAPI's own error logs are off whenever fastapi-observer exports OTLP logs**, even if the app enabled them in `FastAPI(telemetry=...)`.
