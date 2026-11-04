# Benchmarks

This page answers two questions: how much code `fastapi-observer` saves compared with wiring the same observability by hand, and how much CPU it adds to each request.

## Code you don't write

`examples/time_saved/` contains the same service built twice: once by hand (`the_hard_way.py`) and once with `install_observability()` (`the_easy_way.py`). Both produce JSON logs with request IDs, Prometheus request metrics and OpenTelemetry traces. `tests/test_dx_parity.py` checks that the two produce equivalent log fields, trace context and metrics.

| | Hand-wired | `fastapi-observer` |
| :--- | :--- | :--- |
| Lines of code | 161 | 29 |
| Direct dependencies to pin | 6 (`prometheus-client`, `opentelemetry-sdk`, ...) | 1 |

The hand-wired version also has to get these details right, which the library handles for you:

1. **Metric cardinality.** Labelling metrics with `request.url.path` creates a new series for every `/users/123`. The library labels with the route template (`/users/{user_id}`).
2. **Context cleanup.** Request IDs stored in context variables have to be reset in a `finally` block, or they leak into the next request's logs.
3. **Blocking writes.** Writing log lines to stdout from the request path blocks the event loop when the reader is slow (for example a container log driver under load). The library queues records and writes them from a background thread with `logging.handlers.QueueListener`.

## CPU per request, in process

[`examples/benchmarks/request_cpu.py`](https://github.com/Vitaee/FastapiObserver/blob/main/examples/benchmarks/request_cpu.py) calls the app directly, with no server or network, and reports the CPU the library adds to each request: on the request's own thread, and in the whole process once the background thread has written the log line. Apple M1, macOS 15.8.1, Python 3.14.7, FastAPI 0.162.0, Starlette 1.10.0; medians of nine runs of 5,000 requests after warm-up, measured 4 November 2026:

| Mode | CPU added per request | On the request's thread |
|---|---|---|
| `default` | 49.2 µs | 35.2 µs |
| `sampled` (`OBS_LOG_SAMPLE_RATE=0.1`) | 38.3 µs | 34.7 µs |

```bash
python examples/benchmarks/request_cpu.py default > /dev/null
```

Its other modes are listed in the script.

### CI regression gate

```bash
python scripts/compare_overhead.py --baseline origin/main --output overhead.json
```

This uses the current harness for both source versions with the same interpreter
and dependencies, alternates run order, and compares nine baseline/candidate
pairs. It covers the default queue and an application-owned JSON handler. The
budget is 10% of baseline CPU overhead plus three robust standard errors of the
paired differences. Either side with robust relative spread above 10% fails as
an unreliable measurement. The JSON artifact includes raw samples, the resolved
baseline commit, Python/platform and dependency versions. This CPU check runs
alongside real-server failure budgets; it does not measure network tail latency.

### Request timing and HTTP diagnostics

On the same interpreter, the default overhead before these changes (`059399a`)
was 46.3 µs of process CPU and 33.0 µs on the request thread. Separating response
timing from application lifetime and adding the diagnostic state costs 2.9 µs
of process CPU and 2.2 µs on the request thread per request in this measurement.

The `http` mode makes one HTTPX2 call through `MockTransport` per request, without
network I/O. These are **absolute CPU costs for the whole application**, including
the client call and log formatting, rather than overhead against plain FastAPI:

| Client configuration | Without helper | With helper | Added CPU per request |
|---|---:|---:|---:|
| Core package + HTTPX2, without OTel client instrumentation | 151.3 µs | 163.3 µs | 12.0 µs |
| OTel HTTPX instrumentation 0.66b2 installed, tracing disabled | 144.6 µs | 212.0 µs | 67.4 µs |

The second row includes installing OpenTelemetry's client wrapper through the
helper, so its difference is not the cost of the native counter alone. Within
each row, both runs use the same dependencies. The two environments are separate
measurements; compare the columns within a row.

Reproduce the first row in an environment without the optional instrumentation:

```bash
uv venv /tmp/http-counts
uv pip install --python /tmp/http-counts/bin/python -e . httpx2
COUNT_HTTP_CALLS=0 /tmp/http-counts/bin/python examples/benchmarks/request_cpu.py http absolute > /dev/null
/tmp/http-counts/bin/python examples/benchmarks/request_cpu.py http absolute > /dev/null
```

`HTTP_CALLS=5` measures five calls per request; `traced-http` enables server and
client tracing with spans discarded in memory. These CPU measurements exclude
network latency, backend storage, and container overhead. Real-server regression
tests and the [Docker verification](https://github.com/Vitaee/FastapiObserver/tree/main/examples/full_stack#run)
exercise real connections, streaming, timeouts and the exported telemetry.

## Per-request overhead

### Setup

- Measured with fastapi-observer 1.5.0, before 1.12.0 moved tracing to FastAPI's own OpenTelemetry support. The tracing rows (S3 to S5) predate that change.
- Apple M1 (8 cores), Python 3.13.15, FastAPI 0.162.0, Starlette 1.10.0, uvicorn 0.54.0 with 1 worker.
- Load from ApacheBench on the same machine: `ab -k -c 200`, `POST /items` with a small JSON body. The handler validates the body and awaits `asyncio.sleep(0.015)` to stand in for I/O.
- Tracing scenarios export spans over OTLP/gRPC to the Jaeger all-in-one container from `examples/time_saved/docker-compose.yml`.
- Each scenario: 3 s warm-up, then 5 runs of 5 s. Values are the median (± standard deviation) of the 5 runs. `ab` reports latency in whole milliseconds.

Reproduce with the command below. The tracing scenarios (S3, S4, S5, H4) start and stop the collector with Docker; pass scenario names, for example `S0 S1 S2`, to run without Docker.

```bash
python examples/benchmarks/harness.py
```

### Results

| Scenario | What is enabled | Requests/s | p50 | p95 | p99 |
| :--- | :--- | ---: | ---: | ---: | ---: |
| S0 | Plain FastAPI (uvicorn's default plain-text access log) | 5,411 (±81) | 36 ms | 47 ms | 56 ms |
| S1 | `fastapi-observer`, defaults (JSON request logs, request IDs, redaction) | 4,393 (±12) | 44 ms | 56 ms | 63 ms |
| S2 | S1 + Prometheus metrics | 4,249 (±21) | 46 ms | 59 ms | 66 ms |
| S3 | S1 + OpenTelemetry tracing | 2,076 (±45) | 96 ms | 125 ms | 136 ms |
| S4 | All features: metrics, tracing, request and response body capture | 2,020 (±27) | 97 ms | 123 ms | 136 ms |
| S5 | S4 with the collector stopped | 2,135 (±59) | 91 ms | 119 ms | 128 ms |
| H4 | Hand-wired logs, metrics and tracing (`the_hard_way.py`) | 1,544 (±33) | 123 ms | 152 ms | 181 ms |

### Reading the numbers

The handler spends its time waiting, so a single worker is limited by CPU, and requests per second is the inverse of CPU time per request:

| Scenario | CPU per request | Added over S0 |
| :--- | ---: | ---: |
| S0 | 185 µs | — |
| S1 | 228 µs | 43 µs |
| S2 | 235 µs | 51 µs |
| S3 | 482 µs | 297 µs |
| S4 | 495 µs | 310 µs |
| H4 | 648 µs | 463 µs |

- **The added time is a fixed cost per request, not a percentage.** A handler that spends 5 ms on database calls still pays about 45 µs for logging, under 1%. Most of it is building, redacting and serializing the JSON log line. S0 is not free of logging either: uvicorn's plain-text access line, which the library replaces with `request.completed`, costs about 20 µs per request on its own.
- **Tracing is the expensive part, and most of it is OpenTelemetry itself.** In the measured version, its FastAPI instrumentation created three spans per request (the server span plus ASGI receive and send spans), which the SDK serializes and exports. Since 1.12.0, on FastAPI 0.142+, the spans are FastAPI's own: a server span plus spans for dependencies, the endpoint and serialization. A hand-wired OpenTelemetry setup pays the same cost.
- **With every feature on, the library handles 31% more requests than the hand-wired example** (S4 vs H4), even though S4 also captures request and response bodies. The hand-wired example uses `@app.middleware("http")`, which runs each request through extra tasks and memory streams and writes log lines synchronously. The library uses a plain ASGI middleware and writes logs from a background thread.
- **A stopped collector does not stall requests** (S5). Throughput goes up slightly because spans that cannot be delivered are dropped instead of being sent.
