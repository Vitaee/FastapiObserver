# Migration example: hand-written observability to fastapi-observer

This folder holds the same small FastAPI service twice.

## Before: `main.py` and four modules (239 lines)

| File | Lines | What it does |
|---|---:|---|
| `logging_setup.py` | 103 | JSON log formatter, and a filter that adds the request ID and user context |
| `middleware.py` | 76 | Pure ASGI middleware: request IDs, request duration, access log, Prometheus metrics |
| `metrics.py` | 37 | Request counter, duration histogram and in-progress gauge, plus path normalisation |
| `request_context.py` | 23 | Context variables for the request ID and user context |

`main.py` also defines its own `/metrics` route.

```bash
cd examples/migration_example
uvicorn main:app --reload
```

## After: `migrated_main.py` (26 lines)

The four modules and the `/metrics` route are gone. `main.py` calls one function instead:

```python
from fastapiobserver import ObservabilitySettings, install_observability

install_observability(app, ObservabilitySettings(
    app_name="simple-api-migrated",
    environment="production",
    version="1.0.0",
    metrics_enabled=True,
))
```

```bash
uvicorn migrated_main:app --reload
```

## What changes when you migrate

- **Metric labels.** The hand-written metrics used `handler`, `method` and `status`. fastapi-observer uses `method`, `path` (the route template), `status_code`, `service` and `environment`. Dashboards and alerts need the new names.
- **No in-progress gauge.** The hand-written version had one, and fastapi-observer doesn't.
- **Added without extra code:**
    - redaction of passwords, tokens and auth headers, on by default ([security presets](https://vitaee.github.io/FastapiObserver/security/));
    - request IDs accepted only from trusted proxies;
    - optional OpenTelemetry traces with `trace_id` in every log line;
    - an opt-in runtime control endpoint for changing the log level without a restart ([runtime control](https://vitaee.github.io/FastapiObserver/runtime-control/)).
