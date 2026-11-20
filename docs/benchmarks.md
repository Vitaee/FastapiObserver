# Benchmarks

## Code you don't write

`examples/time_saved/` builds the same service twice: by hand (`the_hard_way.py`) and with `install_observability()` (`the_easy_way.py`). Both apps run in the test suite.

| | Hand-wired | `fastapi-observer` |
| :--- | :--- | :--- |
| Lines of code | 161 | 29 |
| Direct dependencies to pin | 6 (`prometheus-client`, `opentelemetry-sdk`, ...) | 1 |

The library also handles route-template metric labels (`/users/{user_id}` instead of `/users/123`), contextvar cleanup in `finally` blocks, and non-blocking log writes on a background `QueueListener` thread.

## CPU per request, in process

[`examples/benchmarks/request_cpu.py`](https://github.com/Vitaee/FastapiObserver/blob/main/examples/benchmarks/request_cpu.py) calls the ASGI app in-process without network I/O and measures process CPU per request, including background JSON formatting and redaction. Measured for 2.4.0 on an Apple ARM64 Mac with Python 3.14; 2.3.0 measures the same in `default` mode on that machine.

| Mode | Added process CPU per request |
|---|---:|
| `default` (library-owned JSON queue + redaction) | ~42 µs |
| `application` (`configure_logging=False`) | ~26 µs |
| `sampled` (`OBS_LOG_SAMPLE_RATE=0.1`) | ~25 µs |
| Each outbound call through `instrument_httpx_client` | +16 µs |
| ...plus OpenTelemetry's HTTPX instrumentation, when `[otel-httpx]` is installed, even with tracing off | +37 µs |

In `sampled` mode, a fast successful request whose line is dropped skips building and redacting it. Metrics, warnings and errors are still recorded for every request.

```bash
python examples/benchmarks/request_cpu.py default > /dev/null
# The HTTP rows are this run minus the same run with COUNT_HTTP_CALLS=0,
# without and with opentelemetry-instrumentation-httpx installed:
python examples/benchmarks/request_cpu.py http > /dev/null
```

### Manual version comparison

```bash
python scripts/compare_overhead.py --baseline origin/main --output overhead.json
```

`scripts/compare_overhead.py` runs nine alternating baseline/candidate pairs across `default` and `application` modes on the same interpreter. The regression budget is 10% of baseline CPU overhead plus three robust standard errors (MAD) of paired differences; runs with relative spread above 10% fail as unreliable.
