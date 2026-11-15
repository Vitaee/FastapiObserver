# Advanced Operations

The queue and sink settings below apply to the default logging pipeline. For
`configure_logging=False`, see [application-owned logging](logging.md#application-owned-logging).
Runnable examples are listed in
[`examples/`](https://github.com/Vitaee/FastapiObserver/tree/main/examples).

### Middleware ordering for body capture

If body capture is enabled, install observability before other middleware:

```python
from fastapi.middleware.cors import CORSMiddleware
from fastapiobserver import SecurityPolicy, install_observability

install_observability(app, settings, security_policy=SecurityPolicy(log_request_body=True))
app.add_middleware(CORSMiddleware, allow_origins=["*"])
```

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

### Metrics

With `OBS_METRICS_ENABLED=true`, `/metrics` serves `http_requests_total` and
`http_request_duration_seconds` plus these library metrics.

Single process only, left out in [Prometheus multiprocess mode](servers.md#prometheus-multiprocess-mode):

- Log queue: `fastapiobserver_log_queue_size`, `fastapiobserver_log_queue_capacity`, `fastapiobserver_log_queue_overflow_policy_info`, `fastapiobserver_log_queue_enqueued_total`, `fastapiobserver_log_queue_dropped_total{reason}`, `fastapiobserver_log_queue_blocked_total`, `fastapiobserver_log_queue_block_timeouts_total`, `fastapiobserver_log_bytes_total{route}`.
- Sink circuit breakers: `fastapiobserver_sink_circuit_breaker_state_info{sink,state}` and `fastapiobserver_sink_circuit_breaker_{failures,skipped,opens,half_open,closes}_total{sink}`.
- Pools: `fastapiobserver_threadpool_busy`, `fastapiobserver_threadpool_limit`, `fastapiobserver_threadpool_waiting`, `fastapiobserver_db_pool_checked_out{engine}`, `fastapiobserver_db_pool_limit{engine}`.

Diagnostic counters, in both modes:

- `fastapiobserver_db_n_plus_one_total{route}`
- `fastapiobserver_http_n_plus_one_total{route}`
- `fastapiobserver_db_connection_held_during_http_total{route}`
- `fastapiobserver_http_validation_errors_total{route, field}`
- `fastapiobserver_db_pool_exhausted_total{engine}`
- `fastapiobserver_threadpool_saturated_total`
- `fastapiobserver_event_loop_blocked_total`

### Lifespan and Loguru

Use `observability_lifespan(app)` directly only when calling `setup_logging()`
without `install_observability()`, which already wraps the app's lifespan in it.
What it does at shutdown is described under [the log pipeline](architecture.md#the-log-pipeline).

If your service uses `loguru`, route its records into the same pipeline via `install_loguru_bridge()`; see [`loguru.md`](loguru.md).

For the runtime stress harness, see [contributing](contributing.md#runtime-stress-validation).
