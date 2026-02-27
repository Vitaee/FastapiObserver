# Plugin Hooks

Extend behavior without editing package internals:

```python
from fastapiobserver import (
    register_log_enricher,
    register_log_filter,
    register_metric_hook,
)


def add_git_sha(payload: dict) -> dict:
    payload["git_sha"] = "abc123"
    return payload


def drop_health_probe(record) -> bool:
    return "health" not in record.getMessage().lower()


def track_slow_requests(request, response, duration):
    if duration > 1.0:
        print(f"slow request: {request.url.path} {duration:.2f}s")


register_log_enricher("git_sha", add_git_sha)
register_log_filter("drop_health_probe", drop_health_probe)
register_metric_hook("slow_requests", track_slow_requests)
```

Plugin failures are isolated and do not crash request handling.

### Custom Metrics Backend Registry

Use `register_metrics_backend()` to plug in non-Prometheus backends without
modifying core code:

```python
from fastapiobserver import register_metrics_backend


class MyBackend:
    def observe(self, method, path, status_code, duration_seconds):
        ...

    def mount_endpoint(self, app, *, path="/metrics", metrics_format="negotiate"):
        # Optional: mount a backend-specific endpoint
        ...


def build_my_backend(*, service: str, environment: str, exemplars_enabled: bool):
    return MyBackend()


register_metrics_backend("my_backend", build_my_backend)
```

### Formatter Dependency Injection

`StructuredJsonFormatter` accepts injectable callables for enrichment and
sanitization, keeping defaults unchanged while improving testability:

```python
formatter = StructuredJsonFormatter(
    settings,
    enrich_event=my_enricher,
    sanitize_payload=my_sanitizer,
)
```

---

