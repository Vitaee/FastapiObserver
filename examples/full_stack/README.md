# Full-Stack Observability Demo

**See fastapi-observer in action with Grafana, Prometheus, Loki, and Tempo — all running locally in Docker.**

This example spins up a complete observability stack with **3 independent FastAPI services** to demonstrate multi-instance monitoring, distributed tracing, and centralized log aggregation.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        Your Browser                               │
│                                                                    │
│   Grafana :3000    app-a :8000    app-b :8001    app-c :8002      │
└────────┬───────────────┬──────────────┬──────────────┬────────────┘
         │               │              │              │
         │               └──────┬───────┴──────────────┘
         │                      │
         │            ┌─────────▼──────────┐
         │            │   OTel Collector    │
         │            │    :4317 (gRPC)     │
         │            └────┬─────────┬─────┘
         │                 │         │
         │        traces   │         │  logs
         │                 │         │
    ┌────▼────┐    ┌───────▼──┐  ┌───▼────┐
    │Prometheus│    │  Tempo   │  │  Loki  │
    │  :9090   │    │  :3200   │  │ :3100  │
    └────┬─────┘    └────┬─────┘  └───┬────┘
         │               │            │
         └───────────────┴────────────┘
                         │
                  ┌──────▼──────┐
                  │   Grafana    │
                  │   :3000     │
                  └─────────────┘
```

### Services

| Service | Port | Purpose |
|---|---|---|
| **app-a** | `8000` | Primary API — calls app-b and app-c on `/chain` |
| **app-b** | `8001` | Secondary service |
| **app-c** | `8002` | Third service |
| **OTel Collector** | `4317` | Routes OTLP traces → Tempo, logs → Loki |
| **Prometheus** | `9090` | Scrapes `/metrics` from all 3 apps |
| **Loki** | `3100` | Log aggregation |
| **Tempo** | `3200` | Distributed trace storage |
| **Grafana** | `3000` | Pre-configured dashboards (no login needed) |

---

## Quick Start

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) ≥ 20.10
- [Docker Compose](https://docs.docker.com/compose/install/) ≥ 2.0

### 1. Start the stack

```bash
cd examples/full_stack
docker compose up --build
```

Wait ~30 seconds for all services to start. You'll see log output from all containers.

### 2. Generate traffic

In a separate terminal:

```bash
cd examples/full_stack

# Run for 60 seconds (default)
./generate_traffic.sh

# Or specify duration
./generate_traffic.sh 120
```

The script automatically:
- Waits for all services to be ready
- Hits all endpoints across all 3 services
- Shows real-time status (✔/✗) with color coding
- Prints a summary at the end

### 3. Open Grafana

Open [http://localhost:3000](http://localhost:3000) in your browser.

> **No login required** — anonymous admin access is enabled for the demo.

The **"FastAPI Observer — API Overview"** dashboard is pre-loaded with 7 panels:

| Panel | What It Shows |
|---|---|
| **Request Duration Heatmap** | Latency distribution across histogram buckets |
| **API Request Count by Route** | Request rate per endpoint (stacked bars) |
| **Error Rate per Route** | 4xx/5xx errors with color-coded severity |
| **CPU & Memory Usage** | Process resource consumption (dual axis) |
| **Request Duration P50/P95/P99** | Percentile latencies over time |
| **Active Requests (In-Flight)** | Current request count gauge |
| **API Logs** | Live structured JSON logs from Loki |

### 4. Switch between services

Use the **Service** dropdown at the top of the dashboard to switch between `app-a`, `app-b`, and `app-c`. Each service reports its own metrics and logs independently — this demonstrates that `fastapi-observer` works dynamically across multiple instances.

---

## Exploring the Three Pillars

### Metrics (Prometheus)

Go to **Explore → Prometheus** and try these queries:

```promql
# Request rate by service and route
sum(rate(http_requests_total[5m])) by (service, path)

# P95 latency by service
histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (le, service))

# Error rate (5xx)
sum(rate(http_requests_total{status_code=~"5.."}[5m])) by (service)
```

### Logs (Loki)

Go to **Explore → Loki** and try:

```logql
# All logs from app-a
{service_name="app-a"} | json

# Only error logs
{service_name="app-a"} | json | level="ERROR"

# Logs with a specific trace ID (copy from a trace)
{service_name="app-a"} | json | trace_id="<your-trace-id>"
```

### Traces (Tempo)

Go to **Explore → Tempo** and:

1. Click **Search** tab
2. Select **Service Name** = `app-a`
3. Click **Run Query**
4. Click any trace to see the span waterfall

For cross-service traces, look for traces from the `/chain` endpoint — they will show spans across `app-a`, `app-b`, and `app-c`.

### Cross-Linking

The datasources are pre-configured with cross-links:

- **Logs → Traces**: Click the `trace_id` field in any Loki log line to jump to the full trace in Tempo
- **Traces → Logs**: From a trace in Tempo, click "Logs for this span" to see the corresponding log entries in Loki
- **Metrics → Traces**: When exemplars are enabled, hover a metric point to jump to its trace

---

## Available Endpoints

All 3 services expose the same endpoints:

| Endpoint | Description | Purpose |
|---|---|---|
| `GET /health` | Health check | Excluded from metrics |
| `GET /items/{id}` | Item lookup | Normal request flow |
| `GET /users/{id}` | User lookup | Per-route breakdown |
| `GET /slow` | Simulated delay (0.5–2s) | Latency histogram |
| `GET /error` | Random 4xx/5xx errors | Error rate panel |
| `GET /chain` | Cross-service calls | Distributed tracing |
| `GET /metrics` | Prometheus metrics | Scraped by Prometheus |

```bash
# Try individual endpoints
curl http://localhost:8000/items/42
curl http://localhost:8001/users/1
curl http://localhost:8002/slow
curl http://localhost:8000/chain    # Cross-service trace!
```

---

## How It Works

Each FastAPI service is configured with **6 lines of code**:

```python
from fastapiobserver import ObservabilitySettings, install_observability
from fastapiobserver.otel import OTelSettings, OTelLogsSettings

settings = ObservabilitySettings.from_env()
otel_settings = OTelSettings.from_env(settings)
otel_logs_settings = OTelLogsSettings.from_env()

install_observability(app, settings, otel_settings=otel_settings, otel_logs_settings=otel_logs_settings)
```

All configuration flows through environment variables defined in `docker-compose.yml`. Each service gets its own `SERVICE_NAME` and `OTEL_SERVICE_NAME`, making it trivially easy to scale to N instances.

---

## Adding Your Own App

To add a 4th service to the stack:

1. In `docker-compose.yml`, add a new service:

```yaml
  app-d:
    <<: *app-base
    container_name: app-d
    ports:
      - "8003:8000"
    environment:
      <<: *app-env
      SERVICE_NAME: "app-d"
      OTEL_SERVICE_NAME: "app-d"
```

2. In `config/prometheus.yml`, add a scrape job:

```yaml
  - job_name: "app-d"
    static_configs:
      - targets: ["app-d:8000"]
        labels:
          service: "app-d"
```

3. `docker compose up --build` — the new service automatically appears in Grafana dropdowns.

---

## Cleanup

```bash
docker compose down -v
```

This removes all containers and volumes (metric/log/trace data).
