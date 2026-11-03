# Configuration

## Environment Variables

The library supports configuration from code and env vars. Every variable it owns starts with `OBS_`. For OpenTelemetry it reads the [specification's variables](https://opentelemetry.io/docs/specs/otel/configuration/sdk-environment-variables/), such as `OTEL_SERVICE_NAME` and `OTEL_EXPORTER_OTLP_ENDPOINT`. Below are the most relevant env vars by area.

To see what took effect, look for the `observability.configured` line that `install_observability()` writes at startup. It lists the active profile, log level and sampling, metrics path, OpenTelemetry endpoint, protocol and sampling, OTLP log and metric export, redaction mode, trusted networks and control plane path. Endpoint credentials, query strings and tokens are left out.

```json
{"message": "observability.configured", "event": {"profile": "production", "log_level": "INFO", "metrics_path": null, "tracing": true, "otlp_endpoint": "http://collector:4317", "trace_sampling_ratio": 0.1, "redaction_mode": "drop", "trusted_cidrs": ["10.0.0.0/8", "..."], "control_plane": null}}
```

The line is written at `INFO`, so it's hidden when `OBS_LOG_LEVEL` is `WARNING` or higher.

### Environment Profiles (`OBS_PROFILE`)

`OBS_PROFILE` is an optional profile switch for zero-glue configuration (`install_observability(app)` with env-based settings).

| Variable | Allowed values | Description |
|---|---|---|
| `OBS_PROFILE` | `development`, `production` | Applies environment-specific defaults before env-based settings are loaded. |

Current profile defaults:

- `development`: sets `OBS_LOG_LEVEL=DEBUG`.
- `production`: sets `OBS_LOG_LEVEL=INFO`, `OBS_LOG_QUEUE_MAX_SIZE=20000`, `OBS_REDACTION_PRESET=strict` and `OBS_LOOP_BLOCK_THRESHOLD_MS=250`. `OBS_LOG_LEVEL_ON_ERROR` stays off, since each held `DEBUG` record costs CPU; see [decision record 0014](decisions/0014-diagnosis-without-tracing.md).

Notes:
- Variables you set yourself win over the profile's.
- If you pass settings/policy objects directly in Python, those object values take precedence over profile-derived env defaults.


### Identity and logging

| Variable | Default | Description |
|---|---|---|
| `OBS_APP_NAME` | `app` | Namespace for app-level identity |
| `OBS_SERVICE_NAME` | `OTEL_SERVICE_NAME`, else `api` | Service name on logs, metrics and traces |
| `OBS_ENVIRONMENT` | `development` | Environment label |
| `OBS_APP_VERSION` | `0.0.0` | Service version |
| `OBS_LOG_LEVEL` | `INFO` | Root log level |
| `OBS_LOG_LEVEL_ON_ERROR` | - | A lower level, such as `DEBUG`, for requests that fail: their lines at this level are held and written only if the request ends in a 5xx or an unhandled exception. See [debug logs for failed requests](logging.md#debug-logs-for-failed-requests) |
| `OBS_LOG_SAMPLE_RATE` | `1.0` | Share of requests whose `INFO` and lower lines are written, chosen by trace ID. Failed and slow requests, and `WARNING` and higher lines, are always written. See [sampling request logs](logging.md#sampling-request-logs) |
| `OBS_LOG_SLOW_REQUEST_MS` | `1000` | With sampling, requests that take at least this long are written in full |
| `OBS_LOOP_BLOCK_THRESHOLD_MS` | `250` with the `production` profile, off otherwise | Write `event_loop.blocked`, with the stack of the blocking call, when the event loop doesn't run for this many milliseconds. See [calls that block the event loop](logging.md#calls-that-block-the-event-loop) |
| `OBS_LOG_DIR` | - | Optional file log directory |
| `OBS_LOG_QUEUE_MAX_SIZE` | `10000` | Max in-memory records in core log queue |
| `OBS_LOG_QUEUE_OVERFLOW_POLICY` | `drop_oldest` | Queue overflow behavior: `drop_oldest`, `drop_newest`, `block` |
| `OBS_LOG_QUEUE_BLOCK_TIMEOUT_SECONDS` | `1.0` | Timeout used by `block` policy before dropping newest |
| `OBS_LOG_SINK_CIRCUIT_BREAKER_ENABLED` | `true` | Enable sink circuit-breaker protection |
| `OBS_LOG_SINK_CIRCUIT_BREAKER_FAILURE_THRESHOLD` | `5` | Consecutive sink failures before opening circuit |
| `OBS_LOG_SINK_CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS` | `30.0` | Open-state cooldown before half-open probe |
| `OBS_REQUEST_ID_HEADER` | `x-request-id` | Request ID header read from incoming requests and sent on outbound calls |
| `OBS_RESPONSE_REQUEST_ID_HEADER` | `x-request-id` | Response request ID header |

### Metrics

| Variable | Default | Description |
|---|---|---|
| `OBS_METRICS_ENABLED` | `false` | Enable metrics backend |
| `OBS_METRICS_BACKEND` | `prometheus` | Registered backend name used by `install_observability()` |
| `OBS_METRICS_PATH` | `/metrics` | Metrics endpoint path |
| `OBS_METRICS_EXCLUDE_PATHS` | `/metrics,/health,/healthz,/docs,/openapi.json` | Skip metrics for noisy endpoints. Comma-separated or a JSON array. Empty drops these defaults; the metrics path, FastAPI's docs routes and routes with `include_in_schema=False` are still skipped once the app starts |
| `OBS_METRICS_EXEMPLARS_ENABLED` | `false` | Enable exemplars where supported |
| `OBS_METRICS_FORMAT` | `negotiate` | `negotiate` lets `prometheus_client` pick from the `Accept` header; `openmetrics` or `prometheus` always serve that format. Responses are gzipped when the scraper accepts it |

> [!CAUTION]
> The `/metrics` endpoint is **unauthenticated by default**. In production it should be restricted to internal networks (e.g. behind a Kubernetes `NetworkPolicy`, VPC security group, or ingress rule that only allows your Prometheus scraper). Exposing it publicly leaks service topology, error rates, and request patterns.

### Security and trust boundary

| Variable | Default | Description |
|---|---|---|
| `OBS_REDACTION_PRESET` | - | `strict`, `pci`, `gdpr` |
| `OBS_REDACTED_FIELDS` | built-in list | CSV keys to redact |
| `OBS_REDACTED_HEADERS` | built-in list | CSV headers to redact |
| `OBS_REDACTION_MODE` | `mask` | `mask`, `hash`, `drop` |
| `OBS_MASK_TEXT` | `***` | Mask replacement text |
| `OBS_REDACTION_HASH_KEY` | random per process | HMAC key for `hash` mode. Set it so the same value gets the same hash in every worker and after restarts |
| `OBS_LOG_REQUEST_BODY` | `false` | Enable request body logging |
| `OBS_LOG_RESPONSE_BODY` | `false` | Enable response body logging |
| `OBS_MAX_BODY_LENGTH` | `256` | Max captured body bytes |
| `OBS_HEADER_ALLOWLIST` | - | CSV headers allowed in logs |
| `OBS_EVENT_KEY_ALLOWLIST` | - | CSV event keys allowed in logs |
| `OBS_BODY_CAPTURE_MEDIA_TYPES` | - | CSV allowed media types for body capture |
| `OBS_TRUSTED_PROXY_ENABLED` | `true` | Enable trusted-proxy policy |
| `OBS_TRUSTED_CIDRS` | RFC1918 + loopback | CSV trusted CIDRs |
| `OBS_HONOR_FORWARDED_HEADERS` | `false` | Take the client IP from `X-Forwarded-For` when the direct peer is in `OBS_TRUSTED_CIDRS`: the rightmost address that is not a trusted proxy |

Notes:
- `OBS_HEADER_ALLOWLIST`, `OBS_EVENT_KEY_ALLOWLIST`, and `OBS_BODY_CAPTURE_MEDIA_TYPES` accept `none`, `null`, or `unset` to clear values.

### OpenTelemetry tracing/log export

The library's own switches:

| Variable | Default | Description |
|---|---|---|
| `OBS_OTEL_ENABLED` | `false` | Enable tracing |
| `OBS_OTEL_EXCLUDED_URLS` | auto-derived | Comma-separated patterns, in OpenTelemetry's format, for requests not to trace. Empty traces everything |
| `OBS_OTEL_LOGS_ENABLED` | `false` | Enable OTLP log export |
| `OBS_OTEL_LOGS_MODE` | `local_json` | `local_json`, `otlp`, `both` |
| `OBS_OTEL_METRICS_ENABLED` | `false` | Enable OTLP metrics export |

The specification's variables it reads:

| Variable | Default | Description |
|---|---|---|
| `OTEL_SDK_DISABLED` | `false` | `true` turns tracing and OTLP export off, whatever the switches above say |
| `OTEL_SERVICE_NAME` | - | Service name, used when `OBS_SERVICE_NAME` isn't set |
| `OTEL_RESOURCE_ATTRIBUTES` | - | `key=value` pairs added to the resource, such as `deployment.environment.name=prod`. They override the service name, version and environment |
| `OTEL_TRACES_SAMPLER_ARG` | `1.0` | Share of traces sampled. Sampling is always parent-based by trace ID, so `OTEL_TRACES_SAMPLER` isn't read |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` | `grpc` or `http/protobuf`, for every signal |
| `OTEL_EXPORTER_OTLP_TRACES_PROTOCOL`, `OTEL_EXPORTER_OTLP_LOGS_PROTOCOL`, `OTEL_EXPORTER_OTLP_METRICS_PROTOCOL` | `OTEL_EXPORTER_OTLP_PROTOCOL` | The protocol for one signal |
| `OTEL_EXPORTER_OTLP_ENDPOINT` and the per-signal `OTEL_EXPORTER_OTLP_*_ENDPOINT` | the exporter's | Read by OpenTelemetry's exporters themselves, as the specification describes |
| `OTEL_METRIC_EXPORT_INTERVAL` | `60000` | OTLP metrics export interval in milliseconds |

### Runtime control plane

| Variable | Default | Description |
|---|---|---|
| `OBS_RUNTIME_CONTROL_ENABLED` | `false` | Enable runtime control endpoint |
| `OBS_RUNTIME_CONTROL_PATH` | `/_observability/control` | Control endpoint path |
| `OBS_RUNTIME_CONTROL_TOKEN_ENV_VAR` | `OBSERVABILITY_CONTROL_TOKEN` | Name of env var containing bearer token |
| `OBSERVABILITY_CONTROL_TOKEN` | - | Bearer token value used for auth |

### Upgrading from 1.x

2.0 renamed every variable the library owns to start with `OBS_`, and reads OpenTelemetry's standard variables instead of its own. At startup, `observability.settings_retired` lists any 1.x variable still set, with its replacement. It stops once the replacement is set, so an app that sets a name like `LOG_LEVEL` for itself can keep it.

| 1.x | 2.0 |
|---|---|
| `APP_NAME`, `SERVICE_NAME`, `ENVIRONMENT`, `APP_VERSION` | the same with `OBS_` in front |
| `LOG_*`, `METRICS_*`, `REQUEST_ID_HEADER`, `RESPONSE_REQUEST_ID_HEADER` | the same with `OBS_` in front |
| `OTEL_ENABLED`, `OTEL_EXCLUDED_URLS`, `OTEL_LOGS_ENABLED`, `OTEL_LOGS_MODE`, `OTEL_METRICS_ENABLED` | the same with `OBS_` in front |
| `OTEL_SERVICE_VERSION`, `OTEL_ENVIRONMENT` | `OBS_APP_VERSION`, `OBS_ENVIRONMENT`, or `OTEL_RESOURCE_ATTRIBUTES` |
| `OTEL_TRACE_SAMPLING_RATIO` | `OTEL_TRACES_SAMPLER_ARG` |
| `OTEL_EXTRA_RESOURCE_ATTRIBUTES` | `OTEL_RESOURCE_ATTRIBUTES` |
| `OTEL_LOGS_ENDPOINT`, `OTEL_LOGS_PROTOCOL` | `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT`, `OTEL_EXPORTER_OTLP_LOGS_PROTOCOL` |
| `OTEL_METRICS_ENDPOINT`, `OTEL_METRICS_PROTOCOL` | `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`, `OTEL_EXPORTER_OTLP_METRICS_PROTOCOL` |
| `OTEL_METRICS_EXPORT_INTERVAL_MILLIS` | `OTEL_METRIC_EXPORT_INTERVAL` |
| `LOGTAIL_*`, `OBS_AUDIT_*`, `OBS_LOG_SINKS` | removed; for Logtail, add your own handler with `register_sink()` |
