# FastAPI Observer — Examples

This directory contains standalone, runnable examples demonstrating the features of `fastapi-observer`.

## How to Run

Most of the examples are single-file FastAPI applications. You can run them using `uvicorn`:

```bash
# Make sure you are in the root directory of the repository
# and your virtual environment is activated
uvicorn examples.basic_app:app --reload
```

## Available Examples

### Core Observability
| File | Description |
|---|---|
| [`basic_app.py`](basic_app.py) | The absolute minimum setup. Shows structured JSON logs and request ID injection. |
| [`security_presets_app.py`](security_presets_app.py) | Demonstrates how to use built-in security presets (`gdpr`, `pci`, `strict`) to automatically redact logs and headers. |
| [`allowlist_app.py`](allowlist_app.py) | Demonstrates how to implement a strict allowlist-only logging policy. |
| [`graphql_app.py`](graphql_app.py) | Shows how `fastapi-observer` natively integrates with GraphQL (Strawberry) to capture introspection queries and mutation names. |

### Distributed Tracing & Metrics (OpenTelemetry)
| File | Description |
|---|---|
| [`otel_app.py`](otel_app.py) | **(Requires `[otel]`)** Shows OTel instrumentation. Demonstrates how to inject standard traces and custom resource attributes (`k8s.namespace`, etc.) into spans and correlating logs. |
| [`db_tracing_app.py`](db_tracing_app.py) | **(Requires `[otel,otel-sqlalchemy]`)** Shows SQLCommenter integration. It patches an async SQLAlchemy engine to inject trace IDs directly into raw SQL database queries as comments. |

## Full-Stack Deployments

If you have Docker installed, you can spin up a fully pre-configured observability stack (Prometheus, Loki, Tempo, Grafana) to see how these libraries behave in a production-like environment:

### [`full_stack/`](full_stack/)
A robust Docker Compose environment. It spins up 3 microservices interconnected via OTel propagation, exporting telemetry to a complete Grafana backend stack.
```bash
cd examples/full_stack
docker compose up --build
```
Open `http://localhost:3000` (admin/admin) to view the dashboards.

### [`k8s/`](k8s/)
A complete Kubernetes setup leveraging Kustomize to deploy the `fastapi-observer` example applications alongside an OpenTelemetry Collector and the LGTM stack (Loki, Grafana, Tempo, Prometheus).
```bash
kubectl kustomize --load-restrictor=LoadRestrictionsNone examples/k8s | kubectl apply -f -
```

### [`time_saved/`](time_saved/)
A lightweight Docker setup focusing purely on the minimal backend infrastructure (Jaeger and Prometheus). Ideal for verifying local application traces without the overhead of Loki and Tempo.
```bash
cd examples/time_saved
docker compose up
```

---

*Note: `db_tracing_app.py` uses an in-memory SQLite database unless `DATABASE_URL` points at your own Postgres or MySQL; the URL formats are in the file's comments.*
