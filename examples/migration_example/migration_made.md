# How We Deleted 500+ Lines of Observability Boilerplate in FastAPI

As our FastAPI application grew, so did our custom observability code. Like many teams, we found ourselves copying and pasting the same utility files from project to project: a custom JSON logging formatter, a request ID correlation middleware, and a Prometheus metrics collector. 

Recently, we discovered **[`fastapi-observer`](https://github.com/Vitaee/FastapiObserver)**—a library that promised to handle all of this out-of-the-box. We decided to migrate our entire backend to it, and the results were incredible. Here is how we did it, what we deleted, and why your team should probably do the same.

---

## What We Deleted

Before the migration, our observability stack consisted of four separate files, totaling around 560 lines of custom code:

1. **`logging.py`**: A custom `StructuredJsonFormatter` built to output logs in a format optimized for Grafana Loki. It handled timestamp formatting, extracting request IDs from `ContextVar`, and merging extra fields.
2. **`middleware.py`**: A pure ASGI middleware that generated/extracted `X-Request-ID` headers, tracked request durations, tracked in-flight requests, and logged HTTP access data.
3. **`metrics.py`**: A custom Prometheus metrics wrapper that defined our request duration histograms and request counter gauges, alongside custom logic to collapse dynamic path segments (like `/users/123` to `/users/{id}`).
4. **`request_context.py`**: Context variables to propagate request IDs and user data throughout our async operations.

*(You can see all these files in this example directory, and run the "before" state via `uvicorn main:app --reload`)*

Deleting code is every developer's favorite task. We wiped these four files completely from our repository.

---

## How We Migrated

The migration process was incredibly straightforward.

**Step 1: Install the package**
```bash
pip install "fastapi-observer[all]"
```
*(We used the `[all]` extra to pull in OpenTelemetry and Prometheus support).*

**Step 2: Update `main.py`**
We removed our custom `app.add_middleware(RequestLoggingMiddleware)` and our custom `/metrics` route definition. In their place, we simply added:

*(You can see the fully migrated result in `migrated_main.py` and run it via `uvicorn migrated_main:app --reload`)*

```python
from fastapiobserver import ObservabilitySettings, install_observability

obs_settings = ObservabilitySettings(
    app_name="simple-api",
    environment="production",
    version="1.0.0",
    metrics_enabled=True,
)

install_observability(app, obs_settings)
```

That was it. One function call completely replaced our entire custom stack.

---

## What We Gained (The Benefits)

While deleting ~560 lines of technical debt is a massive win in itself, `fastapi-observer` actually *upgraded* our system's capabilities:

### 1. OpenTelemetry Distributed Tracing (Zero Config)
Our previous custom JSON logger had empty placeholders for `"trace_id"` and `"span_id"`. With `fastapi-observer`, OpenTelemetry tracing is fully integrated. If we decide to export traces via OTLP in the future, it is simply a matter of passing an `OTelSettings` object into `install_observability()`. The trace IDs are automatically correlated injected into our log payloads.

### 2. The Runtime Control Plane
This was a feature we didn't even know we needed. `fastapi-observer` exposes a secure, authenticated `/_observability/control` endpoint. During an incident, we can hit this endpoint to dynamically change the root logger to `DEBUG` level, or increase our trace sampling ratio—**without restarting the Uvicorn server or dropping traffic**.

### 3. Built-in Security and Sanitization
Instead of writing complex regex filters to sanitize PII (Personally Identifiable Information) from our logs, `fastapi-observer` drops in with presets for `pci` or `gdpr` compliance, automatically masking sensitive fields in request bodies and headers.

### 4. Better Grafana Integration
Because `fastapi-observer` uses standard OpenTelemetry naming conventions (e.g., using `path` instead of `handler`, and `status_code` instead of `status`), our metrics perfectly aligned with community-standard PromQL queries. Updating our Grafana dashboards took seconds.

---

## Conclusion

If your Python team is maintaining their own `middleware.py` just to get request IDs, JSON logs, and basic Prometheus metrics, you are likely wasting valuable engineering hours.

Migrating to `fastapi-observer` allowed us to delete a significant chunk of complex application boilerplate while gaining enterprise-grade features like dynamic runtime controls and OpenTelemetry distributed tracing. It took less than five minutes to integrate, and it will save us countless hours of maintenance in the future.
