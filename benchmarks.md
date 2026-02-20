# Benchmarking Guide

This project does not publish synthetic performance claims without reproducible instructions.
Use this guide to benchmark your own environment and workload profile.

## Goals

Measure overhead of `fastapi-observer` against a plain FastAPI baseline for:
- request throughput
- p95/p99 latency
- stability under sustained concurrency

## Benchmark Harness

Included benchmark apps:
**Tier 1 (Basic Middleware Overhead)**
- `examples/benchmarks/plain_fastapi.py` (baseline)
- `examples/benchmarks/observer_fastapi.py` (with `install_observability()` disabled metrics/tracing)

**Tier 2 (Advanced Full-Stack)**
- `examples/benchmarks/advanced_plain_fastapi.py` (baseline with Pydantic + synthetic I/O latency)
- `examples/benchmarks/advanced_observer_fastapi.py` (with OTel, Prometheus, and security redaction enabled)
*(Note: To run the advanced observer benchmark, you must have OTLP endpoints available, such as the `examples/full_stack` Docker Compose environment).*

Included runner script:
- `examples/benchmarks/run_local_benchmark.sh`

## Prerequisites

- `uv`
- `hey` load generator
- Docker (for Advanced tier external services)

## Run

### Tier 1 (Basic)
Measures the pure CPU overhead of the middleware and structured JSON formatting without any remote tracing or metrics.

```bash
chmod +x examples/benchmarks/run_local_benchmark.sh
REQUESTS=50000 CONCURRENCY=200 examples/benchmarks/run_local_benchmark.sh
```

### Tier 2 (Advanced)
Measures the realistic overhead of application I/O combined with OpenTelemetry network-bound background exporters, Prometheus metrics, and body capture/redaction.

> [!IMPORTANT]
> Because `advanced_observer_fastapi` exports OTLP telemetry to `localhost:4317`, you must start a collector first to avoid constant connection retries skewing the benchmark:
> ```bash
> cd examples/full_stack && docker compose up -d
> ```

```bash
chmod +x examples/benchmarks/run_local_benchmark.sh
TEST_SUITE=advanced REQUESTS=10000 CONCURRENCY=100 examples/benchmarks/run_local_benchmark.sh
```

## Recommended Production-Like Settings

- CPU pinning or isolated benchmark host
- `uvicorn --workers 1` for apples-to-apples middleware overhead checks
- warmup run before collecting final numbers
- disable noisy background workloads on benchmark machine

## Reporting Template

When sharing results, include:
- CPU model and core count
- Python version
- FastAPI and `fastapi-observer` versions
- command used (`REQUESTS`, `CONCURRENCY`)
- baseline vs observer throughput and p95/p99

## Official Reference Benchmark (v0.3.1)

**Environment:** Apple Silicon (M-series, arm64), Python 3.10+, `fastapi-observer` v0.3.1
**Load Profile:** 10,000 requests, 100 concurrent workers (`hey -n 10000 -c 100`)

### Tier 1 (Basic Middleware Overhead)
Pure CPU overhead of the middleware and structured JSON formatting (stdout).

| Case | RPS | p95 Latency | p99 Latency |
|---|---:|---:|---:|
| **Baseline** | 5514.28 | 19.3ms | 26.5ms |
| **Observer** | 3262.38 | 33.7ms | 47.6ms |

*Analysis*: Enabling structured JSON logging adds roughly 10-15ms overhead per request at the p95 level in an asynchronous context without background OTLP threads impacting CPU scheduling.

### Tier 2 (Advanced Full-Stack)
Realistic application overhead including simulated 15ms database latency, Pydantic body validation, OpenTelemetry network exporters (sending to a local collector), Prometheus metrics gathering, and full request/response body capture and redaction.

| Case | RPS | p95 Latency | p99 Latency |
|---|---:|---:|---:|
| **Baseline** | 2918.01 | 18.0ms | 20.7ms |
| **Observer** | 2087.09 | 27.7ms | 29.5ms |

*Analysis*: In a realistic I/O bound scenario the overhead ratio shrinks significantly. The difference in RPS drops to roughly 28%, and latency impact narrows to <10ms relative to the baseline as `asyncio` schedules other requests during the simulated database wait.

- Default observer benchmark keeps metrics endpoint mounted but request metrics disabled (`metrics_enabled=False`).
- For full-stack overhead tests (OTLP logs/traces/metrics, remote sinks), benchmark with your real collector and sink topology.
