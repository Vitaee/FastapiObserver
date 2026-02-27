# Architecture


## What `install_observability()` Wires Up

1. Profile-aware config bootstrap (`OBS_PROFILE`) and env-driven defaults for settings/policies.
2. Structured logging pipeline (JSON formatter + bounded async queue handler).
3. Metrics backend and `/metrics` endpoint when metrics are enabled.
4. OTel tracing setup when OTel is enabled.
5. Optional OTel logs/metrics setup when OTLP settings are enabled.
6. Request logging middleware with sanitization and context cleanup.
7. Runtime control endpoint when runtime control is enabled.
8. Lifespan-based teardown hooks for logging and SQLAlchemy instrumentation cleanup.

Request path lifecycle (high-level):

```text
Request arrives
  -> request ID / trace context resolved
  -> app handler executes
  -> response classified (ok/client_error/server_error/exception)
  -> payload sanitized by policy
  -> log emitted + metrics recorded
  -> context cleared
```

### Route Exclusion Auto-Discovery

At application lifespan start, observability scans registered routes and auto-discovers paths that should be excluded from telemetry noise:

- FastAPI utility routes such as `/docs`, `/redoc`, `/openapi.json`
- Routes marked `include_in_schema=False`

Discovered paths are normalized (including dynamic path variants like `/:id`) and used for metrics exclusion, with active OTel middleware exclusions updated when present.

### Internal Package Layout (Contributor Map)

The project is now organized as focused subpackages instead of large monolithic modules:

- `fastapiobserver/logging/`: formatter, queueing, filters, setup lifecycle, sink circuit-breakers.
- `fastapiobserver/middleware/`: request logging orchestration, context, IP resolution, headers, body capture, metrics hooks.
- `fastapiobserver/sinks/`: sink protocol, registry/discovery, built-ins, factory wiring, Logtail + DLQ implementation.
- `fastapiobserver/metrics/`: backend contracts/registry/builder/endpoint, Prometheus integration subpackage.
- `fastapiobserver/security/`: policy/settings models, normalization helpers, redaction engine, trusted-proxy utilities.
- `fastapiobserver/otel/`: OTel settings/resource/tracing/logs/metrics/lifecycle helpers.

Public imports remain backward-compatible via package facades (`__init__.py` re-exports).

---
