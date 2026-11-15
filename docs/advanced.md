# Advanced Operations

Queue, sink, and lifecycle settings below apply to the default library-owned
logging pipeline. With `configure_logging=False`, the application owns these
resources; see [application-owned logging](logging.md#application-owned-logging).

## Runnable Examples

| Example | What it shows |
|---|---|
| [`basic_app.py`](https://github.com/Vitaee/FastapiObserver/tree/main/examples/basic_app.py) | Minimal setup and request logging |
| [`existing_logging_app.py`](https://github.com/Vitaee/FastapiObserver/tree/main/examples/existing_logging_app.py) | Application-owned JSON logging and redaction |
| [`security_presets_app.py`](https://github.com/Vitaee/FastapiObserver/tree/main/examples/security_presets_app.py) | Preset-based security policy |
| [`allowlist_app.py`](https://github.com/Vitaee/FastapiObserver/tree/main/examples/allowlist_app.py) | Allowlist-only sanitization |
| [`otel_app.py`](https://github.com/Vitaee/FastapiObserver/tree/main/examples/otel_app.py) | OTel tracing and resource attributes |
| [`db_tracing_app.py`](https://github.com/Vitaee/FastapiObserver/tree/main/examples/db_tracing_app.py) | SQLCommenter and database trace injection |
| [`graphql_app.py`](https://github.com/Vitaee/FastapiObserver/tree/main/examples/graphql_app.py) | Native Strawberry GraphQL observability |
| [`benchmarks/`](https://github.com/Vitaee/FastapiObserver/tree/main/examples/benchmarks/) | Baseline vs observer benchmark harness |
| [`k8s/`](https://github.com/Vitaee/FastapiObserver/tree/main/examples/k8s/) | Kubernetes stack with Prometheus + Loki + Tempo + Grafana |
| [`full_stack/`](https://github.com/Vitaee/FastapiObserver/tree/main/examples/full_stack/) | Docker Compose stack: 3 FastAPI services + Grafana + Prometheus + Loki + Tempo |

### Middleware ordering for body capture

If body capture is enabled, install observability before other middleware:

```python
from fastapi.middleware.cors import CORSMiddleware
from fastapiobserver import SecurityPolicy, install_observability

install_observability(app, settings, security_policy=SecurityPolicy(log_request_body=True))
app.add_middleware(CORSMiddleware, allow_origins=["*"])
```

### Multi-worker Gunicorn

With `--preload`, Gunicorn imports your app and runs `install_observability()` in the master process before forking workers. Each worker automatically starts a fresh log-writer thread, lock, and empty queue after `os.fork()`. The event-loop watchdog and thread-pool watcher start in each worker's ASGI lifespan. With `--preload` and OTLP export enabled, prefer `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf` over gRPC channels created pre-fork.

#### Prometheus Multiprocess Mode

Configure a shared directory before starting Gunicorn workers:

```bash
export PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus-metrics
rm -rf "$PROMETHEUS_MULTIPROC_DIR" && mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
```

```python
# gunicorn.conf.py
from fastapiobserver import mark_prometheus_process_dead

def child_exit(server, worker):
    mark_prometheus_process_dead(worker.pid)
```

In multiprocess mode, `/metrics` exports request metrics and all diagnostic counters. Single-worker gauges (queue size, sink breaker state, pool gauges) are omitted in multiprocess mode.

### Bounded queue and overflow policy

```python
settings = ObservabilitySettings(
    app_name="orders-api",
    service="orders",
    environment="production",
    log_queue_max_size=20000,
    log_queue_overflow_policy="drop_oldest",  # "drop_oldest" | "drop_newest" | "block"
    log_queue_block_timeout_seconds=0.5,
)
```

Queue, pool, and diagnostic metrics exposed on `/metrics`:
- **Queue gauges & counters (single-process):** `fastapiobserver_log_queue_size`, `fastapiobserver_log_queue_capacity`, `fastapiobserver_log_queue_overflow_policy_info`, `fastapiobserver_log_queue_enqueued_total`, `fastapiobserver_log_queue_dropped_total{reason}`, `fastapiobserver_log_queue_blocked_total`, `fastapiobserver_log_queue_block_timeouts_total`, `fastapiobserver_log_bytes_total{route}`.
- **Pool gauges (single-process):** `fastapiobserver_threadpool_busy`, `fastapiobserver_threadpool_limit`, `fastapiobserver_threadpool_waiting`, `fastapiobserver_db_pool_checked_out{engine}`, `fastapiobserver_db_pool_limit{engine}`.
- **Diagnostic counters (exported in both single- and multiprocess modes):**
  - `fastapiobserver_db_n_plus_one_total{route}`
  - `fastapiobserver_http_n_plus_one_total{route}`
  - `fastapiobserver_db_connection_held_during_http_total{route}`
  - `fastapiobserver_http_validation_errors_total{route, field}`
  - `fastapiobserver_db_pool_exhausted_total{engine}`
  - `fastapiobserver_threadpool_saturated_total`
  - `fastapiobserver_event_loop_blocked_total`

### Sink circuit breaker

Every output sink (including custom sinks registered via `register_sink(MySink())`) is wrapped with a circuit breaker so a failing sink cannot stall the queue listener:

```python
settings = ObservabilitySettings(
    app_name="orders-api",
    service="orders",
    environment="production",
    sink_circuit_breaker_enabled=True,
    sink_circuit_breaker_failure_threshold=5,
    sink_circuit_breaker_recovery_timeout_seconds=30.0,
)
```

Breaker metrics on `/metrics` (single-process): `fastapiobserver_sink_circuit_breaker_state_info{sink,state}`, `..._failures_total{sink}`, `..._skipped_total{sink}`, `..._opens_total{sink}`, `..._half_open_total{sink}`, `..._closes_total{sink}`.

### Logging Shutdown Lifecycle and Loguru Coexistence

`install_observability()` wraps the app's lifespan in `observability_lifespan`, draining queued records and flushing OTel providers on shutdown while keeping the listener reusable across `TestClient` runs until `atexit`. Use `observability_lifespan(app)` directly only when calling `setup_logging()` without `install_observability()`.

If your service uses `loguru`, route its records into the same pipeline via `install_loguru_bridge()`; see [`loguru.md`](loguru.md).

### Runtime Stress Harness

```bash
uv run python scripts/runtime_stress.py --profile standard
```

`scripts/runtime_stress.py` exercises an adversarial 4-way workload (`200` with DB+HTTP+PII redaction, `422` validation failure, `200` with `10×` N+1 SQL + `db.connection_held_during_http`, and `500` unhandled exception with URL credential stripping + `error.fingerprint`) alongside repeated lifespan churn, queue overflow (`drop_oldest`, `drop_newest`, `block`), thread-contention setup/shutdown, and subprocess `os.fork()` replay safety.
