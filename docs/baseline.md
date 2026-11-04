# Compatibility baseline: 2.2.1

Reviewed 4 November 2026. The package remains **Beta**. This inventory covers the
existing FastAPI product and the first experimental Python operation slice;
future datastore/framework/language support remains outside this baseline.

## Reproducible compatibility evidence

`tests/fixtures/compatibility-2.2.1.json` records all 27 stable root exports,
function parameters/defaults, configuration-model defaults and validation aliases,
the log schema version, and representative success, 404 and exception records.
`scripts/compatibility_probe.py` generated it against source extracted from the
immutable `v2.2.1` tag. The candidate runs the same probe against that fixture.
Timestamps, generated request IDs, elapsed durations, library version and
exception stack locations are normalized; field names and outcomes are not.

To reproduce the baseline without importing the candidate:

```bash
baseline_dir=$(mktemp -d)
git archive v2.2.1 src | tar -x -C "$baseline_dir"
PYTHONPATH="$baseline_dir/src" python scripts/compatibility_probe.py > /tmp/baseline.json
diff -u tests/fixtures/compatibility-2.2.1.json /tmp/baseline.json
```

The installed dependencies must support 2.2.1. The probe does not initialize
exporters or contact an external backend. `tests/test_release_compatibility.py`
checks the fixture on each test run. Existing tests provide the wider timing,
metrics, privacy and lifecycle contracts that three example records cannot prove.

## Claims and their limits

| Claim | Evidence | Limit |
|---|---|---|
| One-call FastAPI setup and repeated lifespans | `test_fastapi_install.py`, `test_lifespan_teardown.py` | FastAPI/Starlette remain required installation dependencies |
| Concurrent request correlation | `test_context_isolation.py`, `test_trust_boundary.py` | Copied thread contexts share request state; arbitrary new threads need explicit handoff |
| Response timing excludes background work | `test_response_timing.py` | Final request record follows application completion; sent status is preserved |
| Structured logs and application-owned logging | `test_logging_schema_contract.py`, `test_application_logging.py`, `test_existing_logging_example.py` | Existing handlers own free-text sanitization, delivery and shutdown in application mode |
| SQLAlchemy counts without tracing | `test_request_insights.py`, `test_sqlalchemy_integration.py` | Explicit engines; successful cursor executions only; SQLite sync/async is tested |
| HTTP diagnostics without tracing | `test_http_calls.py`, `test_propagation.py` | Explicit HTTPX/HTTPX2 or Requests helpers; streamed calls complete at consumption/closure |
| N+1 warnings avoid raw SQL | `test_security_audit_fixes.py`, `test_request_insights.py` | Full-statement SHA-256; 128 per-request fingerprints, 1,024 retained warning keys; a hash is not anonymization |
| Safe field/header redaction | `test_security_redaction.py`, `test_body_media_allowlist.py` | No universal free-text detector; SQL tracing payloads require separate review |
| Bounded queue and blocked-sink behavior | `test_log_queue_controls.py`, `test_logging_shutdown.py`, `test_production_runtime.py` | Record counts do not bound arbitrary record bytes; application handlers can block |
| Fork safety and recycled workers | `test_fork_queue_listener.py`, `test_processes.py` | POSIX scenarios; not a Windows fork claim |
| Prometheus labels stay bounded | `test_route_template_cardinality.py`, `test_metrics_contracts.py` | Queue/pool/process pressure collectors are unavailable in multiprocess mode |
| OTLP delivery and borrowed providers | `test_otlp_export_integration.py`, `test_otel_lifecycle.py`, `test_application_logging.py` | SDK/global-provider ownership persists; no exactly-once backend guarantee |
| Production and release gates | `test_production_runtime.py`, `test_overhead_gate.py`, `test_release_ref.py`, shared validation workflow | Controlled budgets, not universal application SLAs; local tests do not replace the dependency/backend matrix |

Configuration compatibility includes the `OBS_*` names documented in
[configuration](configuration.md), `OBS_PROFILE` defaults, standard
`OTEL_EXPORTER_OTLP_*`, `OTEL_TRACES_SAMPLER_ARG`, `OTEL_RESOURCE_ATTRIBUTES`,
`OTEL_METRIC_EXPORT_INTERVAL`, `OTEL_SDK_DISABLED` and service-name precedence.
`test_config_settings.py`, `test_profiles.py`, `test_otel_logs_from_env.py`,
`test_otel_metrics_from_env.py` and `test_otel_integration.py` exercise that
precedence. Legacy `OTEL_*` convenience settings remain represented through the
existing compatibility models; their environment-loading methods must not change.

Stable root imports are the fixture's 27 names. Documented submodule interfaces
include logging flush/level/stats helpers; SQLAlchemy tracing/uninstrumentation;
HTTP uninstrumentation; metrics registration/backends; sink registration; and
Strawberry integration. They retain their existing narrower stability promise.
HTTP request histograms retain their current names, buckets and
`service/environment/method/path/status_code` labels; this slice adds no metrics.

## State and resource ownership

| Resource | Existing owner / lifetime | Extraction constraint |
|---|---|---|
| Root logging, queue, listener, sink handlers | Process; explicit `setup_logging` and final shutdown | An operation creates/stops none; conflicting process configurations are not independent runtimes |
| Application logging | Application | Preserve handlers, formatter, sanitizer, filters, queues and exporters |
| OTel providers and processors | Process/provider owner | Borrowed providers are not replaced or shut down by an operation |
| Installed middleware, runtime endpoints, lifespan/watchdog | Application | FastAPI facade remains responsible for framework behavior |
| SQLAlchemy hooks and pool watchers | Explicit engine registration | Existing shared hooks remain attached across app lifespan reuse |
| SQL tracing engine registry | Process-owned `_ENGINES` | Explicit uninstrumentation owns reset; no per-operation mutation |
| Plugin, sink, metrics and HTTP wrapper registries | Process | No automatic registration when entering an operation |
| Shared context, call totals, fingerprints, held logs | Request; experimental operation when explicitly entered | Restore outer context; freeze and release experimental state at closure |

Root imports previously eagerly loaded installer, metrics, OTel lifecycle and
Starlette plugin types. Framework exports now load when accessed; ordinary
logging/operation imports do not load frameworks. Importing starts no queue,
watchdog, client instrumentation or exporter. The existing fork-reset module
paths stay unchanged because this slice does not move lock-owning modules.

## Application validation and scope

The maintainer reports prior use in real FastAPI applications and selected the
bundled application for this change's runnable validation. The
`examples/existing_logging_app.py` regression runs a real Uvicorn server,
concurrent successful requests and failures. It checks existing JSON routing,
approved sanitizer behavior, correlation and retained Uvicorn access logging.
Run it against released and candidate source with the same dependencies.

This is reproducible application-integration evidence. No independent deployed
application, canary period or production traffic has been inspected in this
change. Redis/MongoDB/Tortoise adopter examples and new-language maintainers
remain prerequisites for their later milestones.

## Licensing, naming and release boundary

The repository's MIT license covers checked-in code, documentation and fixtures;
retain its notice in redistributions. Contribution and release instructions are
in [maintenance](contributing.md). No new external specification or package
ownership is claimed. The first experiment stays in `fastapi-observer`, under
`fastapiobserver.experimental`, with the existing dependency floor and root API.
Registry-name availability checks apply before a separate distribution is named.

A prerelease is not stable promotion. Additional adapters, a second language,
regional deployment validation and independent adopter canaries do not block
this opt-in experiment; they continue to gate their respective support claims.
