
## Environment Variables

The library supports configuration from code and env vars. Below are the most relevant env vars by area.

### Environment Profiles (`OBS_PROFILE`)

`OBS_PROFILE` is an optional profile switch for zero-glue configuration (`install_observability(app)` with env-based settings).

| Variable | Allowed values | Description |
|---|---|---|
| `OBS_PROFILE` | `development`, `production` | Applies environment-specific defaults before env-based settings are loaded. |

Current profile defaults:

- `development`: sets `LOG_LEVEL=DEBUG`, `OTEL_ENABLED=false`, `OTEL_LOGS_ENABLED=false`, `OTEL_METRICS_ENABLED=false`.
- `production`: sets `LOG_LEVEL=INFO`, `LOG_QUEUE_MAX_SIZE=20000`, `LOG_QUEUE_OVERFLOW_POLICY=drop_oldest`, `OBS_REDACTION_PRESET=strict`.

Notes:
- Profile values are applied with `setdefault`, so explicitly provided env vars still win.
- If you pass settings/policy objects directly in Python, those object values take precedence over profile-derived env defaults.


### Identity and logging

| Variable | Default | Description |
|---|---|---|
| `APP_NAME` | `app` | Namespace for app-level identity |
| `SERVICE_NAME` | `api` | Service label for logs/metrics |
| `ENVIRONMENT` | `development` | Environment label |
| `APP_VERSION` | `0.0.0` | Service version |
| `LOG_LEVEL` | `INFO` | Root log level |
| `LOG_DIR` | - | Optional file log directory |
| `LOG_QUEUE_MAX_SIZE` | `10000` | Max in-memory records in core log queue |
| `LOG_QUEUE_OVERFLOW_POLICY` | `drop_oldest` | Queue overflow behavior: `drop_oldest`, `drop_newest`, `block` |
| `LOG_QUEUE_BLOCK_TIMEOUT_SECONDS` | `1.0` | Timeout used by `block` policy before dropping newest |
| `LOG_SINK_CIRCUIT_BREAKER_ENABLED` | `true` | Enable sink circuit-breaker protection |
| `LOG_SINK_CIRCUIT_BREAKER_FAILURE_THRESHOLD` | `5` | Consecutive sink failures before opening circuit |
| `LOG_SINK_CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS` | `30.0` | Open-state cooldown before half-open probe |
| `REQUEST_ID_HEADER` | `x-request-id` | Incoming request ID header |
| `RESPONSE_REQUEST_ID_HEADER` | `x-request-id` | Response request ID header |

### Metrics

| Variable | Default | Description |
|---|---|---|
| `METRICS_ENABLED` | `false` | Enable metrics backend |
| `METRICS_BACKEND` | `prometheus` | Registered backend name used by `install_observability()` |
| `METRICS_PATH` | `/metrics` | Metrics endpoint path |
| `METRICS_EXCLUDE_PATHS` | `/metrics,/health,/healthz,/docs,/openapi.json` | Skip metrics for noisy endpoints |
| `METRICS_EXEMPLARS_ENABLED` | `false` | Enable exemplars where supported |
| `METRICS_FORMAT` | `negotiate` | `prometheus`, `openmetrics`, or `negotiate` |

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
| `OBS_LOG_REQUEST_BODY` | `false` | Enable request body logging |
| `OBS_LOG_RESPONSE_BODY` | `false` | Enable response body logging |
| `OBS_MAX_BODY_LENGTH` | `256` | Max captured body bytes |
| `OBS_HEADER_ALLOWLIST` | - | CSV headers allowed in logs |
| `OBS_EVENT_KEY_ALLOWLIST` | - | CSV event keys allowed in logs |
| `OBS_BODY_CAPTURE_MEDIA_TYPES` | - | CSV allowed media types for body capture |
| `OBS_TRUSTED_PROXY_ENABLED` | `true` | Enable trusted-proxy policy |
| `OBS_TRUSTED_CIDRS` | RFC1918 + loopback | CSV trusted CIDRs |
| `OBS_HONOR_FORWARDED_HEADERS` | `false` | Trust forwarded headers |

Notes:
- `OBS_HEADER_ALLOWLIST`, `OBS_EVENT_KEY_ALLOWLIST`, and `OBS_BODY_CAPTURE_MEDIA_TYPES` accept `none`, `null`, or `unset` to clear values.

### OpenTelemetry tracing/log export

| Variable | Default | Description |
|---|---|---|
| `OTEL_ENABLED` | `false` | Enable tracing instrumentation |
| `OTEL_SERVICE_NAME` | `SERVICE_NAME` | OTel service name override |
| `OTEL_SERVICE_VERSION` | `APP_VERSION` | OTel service version override |
| `OTEL_ENVIRONMENT` | `ENVIRONMENT` | OTel environment override |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | - | OTLP endpoint |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` | `grpc` or `http/protobuf` |
| `OTEL_TRACE_SAMPLING_RATIO` | `1.0` | Initial trace sampling ratio |
| `OTEL_EXTRA_RESOURCE_ATTRIBUTES` | - | CSV `key=value` pairs |
| `OTEL_EXCLUDED_URLS` | auto-derived | CSV excluded paths for tracing |
| `OTEL_LOGS_ENABLED` | `false` | Enable OTLP log export |
| `OTEL_LOGS_MODE` | `local_json` | `local_json`, `otlp`, `both` |
| `OTEL_LOGS_ENDPOINT` | - | OTLP logs endpoint |
| `OTEL_LOGS_PROTOCOL` | `grpc` | `grpc` or `http/protobuf` |
| `OTEL_METRICS_ENABLED` | `false` | Enable OTLP metrics export |
| `OTEL_METRICS_ENDPOINT` | - | OTLP metrics endpoint |
| `OTEL_METRICS_PROTOCOL` | `grpc` | `grpc` or `http/protobuf` |
| `OTEL_METRICS_EXPORT_INTERVAL_MILLIS` | `60000` | OTLP metrics export interval in milliseconds |

### Runtime control plane

| Variable | Default | Description |
|---|---|---|
| `OBS_RUNTIME_CONTROL_ENABLED` | `false` | Enable runtime control endpoint |
| `OBS_RUNTIME_CONTROL_PATH` | `/_observability/control` | Control endpoint path |
| `OBS_RUNTIME_CONTROL_TOKEN_ENV_VAR` | `OBSERVABILITY_CONTROL_TOKEN` | Name of env var containing bearer token |
| `OBSERVABILITY_CONTROL_TOKEN` | - | Bearer token value used for auth |
