# Compatibility baseline: 2.2.1

## The compatibility probe

`tests/fixtures/compatibility-2.2.1.json` records the 27 stable root exports,
function parameters and defaults, configuration-model defaults and validation
aliases, the log schema version, and representative success, 404 and exception
records. `scripts/compatibility_probe.py` generated it from the `v2.2.1` tag,
and `tests/test_release_compatibility.py` runs the same probe against the
current source on every test run.

The probe normalizes timestamps, generated request IDs, elapsed durations, the
library version and exception stack locations. Field names and outcomes must
match exactly. It leaves out fields added since 2.2.1, which have their own
contract tests: `http.route`, `observation`, `log_sampling`, `response_bytes`,
`response_start_ms`, `first_body_ms` and `stream_duration_ms`.

To reproduce the baseline without importing the current source:

```bash
baseline_dir=$(mktemp -d)
git archive v2.2.1 src | tar -x -C "$baseline_dir"
PYTHONPATH="$baseline_dir/src" python scripts/compatibility_probe.py > /tmp/baseline.json
diff -u tests/fixtures/compatibility-2.2.1.json /tmp/baseline.json
```

The installed dependencies must support 2.2.1. The probe starts no exporter and
contacts no backend.

## Claims and their tests

| Claim | Evidence | Limit |
|---|---|---|
| One-call FastAPI setup and repeated lifespans | `test_fastapi_install.py`, `test_lifespan_teardown.py` | FastAPI/Starlette remain required installation dependencies |
| Concurrent request correlation | `test_context_isolation.py`, `test_trust_boundary.py` | Copied thread contexts share request state; arbitrary new threads need explicit handoff |
| Response/stream timing excludes background work | `test_response_timing.py`, `test_production_runtime.py` | Measured at ASGI send completion, not client receipt; the final record follows application completion; the sent status is kept |
| Structured logs and application-owned logging | `test_logging_schema_contract.py`, `test_application_logging.py`, `test_existing_logging_example.py` | With `configure_logging=False`, the application's handlers own free-text sanitization, delivery and shutdown |
| SQLAlchemy counts without tracing | `test_request_insights.py`, `test_sqlalchemy_integration.py`, `test_sql_execution_details.py` | Explicit engines; `db.count` counts successful executions, `db.sqlalchemy` also counts attempts and failures; SQLite sync/async is tested |
| HTTP diagnostics without tracing | `test_http_calls.py`, `test_propagation.py` | Explicit HTTPX/HTTPX2 or Requests helpers; streamed calls complete at consumption/closure |
| N+1 warnings avoid raw SQL | `test_hardening.py`, `test_request_insights.py` | Full-statement SHA-256; 128 per-request fingerprints, 1,024 retained warning keys; a hash is not anonymization |
| Diagnostic span events | `test_diagnostic_spans.py`, `test_sqlalchemy_integration.py` | At most three per request, on existing recording server spans; pool pressure is a snapshot, not a measured wait |
| Field/header redaction | `test_security_redaction.py`, `test_body_media_allowlist.py` | Name-based; SQL tracing payloads need their own review |
| Bounded queue and blocked-sink behavior | `test_log_queue_controls.py`, `test_logging_shutdown.py`, `test_production_runtime.py` | Limits count records, not bytes; application handlers can block |
| Fork safety and recycled workers | `test_fork_queue_listener.py`, `test_processes.py` | POSIX only |
| Prometheus labels stay bounded | `test_route_template_cardinality.py`, `test_metrics_contracts.py` | Queue, pool and process collectors are left out in multiprocess mode |
| OTLP delivery and borrowed providers | `test_otlp_export_integration.py`, `test_otel_lifecycle.py`, `test_application_logging.py` | Best-effort delivery: OTLP retries can duplicate records, and a killed process loses queued ones |
| Production and release gates | `test_production_runtime.py`, `test_release_ref.py`, shared validation workflow | The [release budgets](contributing.md#release-validation-budgets) apply to the test workload. CPU comparisons are [manual](benchmarks.md#manual-version-comparison); `test_compare_overhead.py` checks only their arithmetic |

Configuration compatibility covers the `OBS_*` names in
[configuration](configuration.md), the `OBS_PROFILE` defaults, the standard
`OTEL_EXPORTER_OTLP_*`, `OTEL_TRACES_SAMPLER_ARG`, `OTEL_RESOURCE_ATTRIBUTES`,
`OTEL_METRIC_EXPORT_INTERVAL` and `OTEL_SDK_DISABLED` variables, and
service-name precedence. `test_config_settings.py`, `test_profiles.py` and
`test_otel_integration.py` test that precedence.

Submodule interfaces with a narrower stability promise than the root exports:
logging flush, level and stats helpers; SQLAlchemy tracing and
uninstrumentation; HTTP uninstrumentation; metrics registration and backends;
sink registration; and the Strawberry integration. HTTP request histograms keep
their names, buckets and `service`, `environment`, `method`, `path` and
`status_code` labels.
