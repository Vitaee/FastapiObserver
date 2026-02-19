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
- `examples/benchmarks/plain_fastapi.py` (baseline)
- `examples/benchmarks/observer_fastapi.py` (with `install_observability()`)

Included runner script:
- `examples/benchmarks/run_local_benchmark.sh`

## Prerequisites

- `uv`
- `hey` load generator

## Run

```bash
chmod +x examples/benchmarks/run_local_benchmark.sh
REQUESTS=50000 CONCURRENCY=200 examples/benchmarks/run_local_benchmark.sh
```

This runs both cases with identical endpoint/worker settings:
- Baseline: `examples.benchmarks.plain_fastapi:app`
- Observer: `examples.benchmarks.observer_fastapi:app`

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

Example table:

| Case | RPS | p95 (ms) | p99 (ms) |
|---|---:|---:|---:|
| Baseline | ... | ... | ... |
| Observer | ... | ... | ... |

## Notes

- Default observer benchmark keeps metrics endpoint mounted but request metrics disabled (`metrics_enabled=False`).
- For full-stack overhead tests (OTLP logs/traces/metrics, remote sinks), benchmark with your real collector and sink topology.
