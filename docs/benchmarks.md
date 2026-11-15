# Benchmarks

This page covers two measurements: the boilerplate `fastapi-observer` replaces compared with wiring the same observability by hand, and the CPU and end-to-end latency it adds per request.

## Code you don't write

`examples/time_saved/` builds the same service twice: by hand (`the_hard_way.py`) and with `install_observability()` (`the_easy_way.py`). `tests/test_dx_parity.py` verifies that both produce equivalent JSON logs, trace context and Prometheus metrics.

| | Hand-wired | `fastapi-observer` |
| :--- | :--- | :--- |
| Lines of code | 161 | 29 |
| Direct dependencies to pin | 6 (`prometheus-client`, `opentelemetry-sdk`, ...) | 1 |

The library also handles route-template metric labels (`/users/{user_id}` instead of `/users/123`), contextvar cleanup in `finally` blocks, and non-blocking log writes on a background `QueueListener` thread.

## CPU per request, in process

[`examples/benchmarks/request_cpu.py`](https://github.com/Vitaee/FastapiObserver/blob/main/examples/benchmarks/request_cpu.py) calls the ASGI app in-process without network I/O and measures process CPU per request, including background JSON formatting and redaction (Apple ARM64 Mac, Python 3.14):

| Mode | Added process CPU per request |
|---|---:|
| `default` (library-owned JSON queue + redaction) | ~55 µs |
| `application` (`configure_logging=False`) | ~38 µs |
| `sampled` (`OBS_LOG_SAMPLE_RATE=0.1`) | ~38 µs |
| Outbound HTTP helper (`instrument_httpx_client`, no OTel) | +12 µs |

```bash
python examples/benchmarks/request_cpu.py default > /dev/null
```

### Manual version comparison

```bash
python scripts/compare_overhead.py --baseline origin/main --output overhead.json
```

`scripts/compare_overhead.py` runs nine alternating baseline/candidate pairs across `default` and `application` modes on the same interpreter. The regression budget is 10% of baseline CPU overhead plus three robust standard errors (MAD) of paired differences; runs with relative spread above 10% fail as unreliable.

## End-to-end concurrent overhead

Measured across 3,000 concurrent requests (`concurrency=128`) where every request executes a SQLAlchemy query and an outbound `httpx` call, comparing bare FastAPI against full `install_observability(app, db_engine=engine)`:

| Scenario (3,000 requests, concurrency = 128) | Throughput (`req/s`) | `p50` (`ms`) | `p95` (`ms`) | `p99` (`ms`) | Enqueued Logs | Dropped |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| **Bare FastAPI** (1 DB query + 1 HTTP call, no observer) | 6,247 | 0.150 | 0.156 | 0.293 | 0 | 0 |
| **Full Observer (100% `INFO` JSON + Redact + Metrics + DB/HTTP hooks)** | 3,191 | 0.288 | 0.435 | 0.536 | 3,031 | 0 |
| **Full Observer + 10% Log Sampling (`OBS_LOG_SAMPLE_RATE=0.1`)** | 3,795 | 0.246 | 0.302 | 0.398 | 283 | 0 |
| **Full Observer + Chaos Mix** (`200` + `422` validation + `10×` N+1 SQL + `ConnHeld`) | 3,033 | 0.292 | 78.018 | 115.039 | 6,063 | 0 |

- **Fixed per-request cost (~138 µs at `p50` with 100% logs, ~96 µs with 10% sampling):** In a real service where database and upstream HTTP calls take 15–150 ms, the added CPU is under 1% of request latency.
- **Sampling skips terminal event construction:** When `OBS_LOG_SAMPLE_RATE < 1.0`, unsampled fast `2xx/3xx` requests skip building and redacting the `request.completed` dictionary while still recording 100% of Prometheus metrics, warnings/errors, and DB/HTTP diagnostics.
