# Time saved: the same app by hand and with fastapi-observer

Both apps write a JSON line per request with request and trace IDs, export OTLP
traces and serve Prometheus metrics labelled by route template.

1. `the_hard_way.py` (161 lines) wires the OpenTelemetry SDK, `prometheus_client`
   and a JSON formatter by hand.
2. `the_easy_way.py` (29 lines) calls `install_observability()`.

## Running the examples locally

Jaeger and Prometheus run in Docker Compose; the apps run on your machine.

### 1. Start the Backends (Jaeger + Prometheus)
```bash
cd examples/time_saved
docker compose up -d
```

### 2. Start Both Applications
Open two terminal windows from the root of the project:

**Terminal 1 (The Hard Way - Port 8001)**
```bash
# Ensure you are at the project root
uv run uvicorn examples.time_saved.the_hard_way:app --port 8001
```

**Terminal 2 (The Easy Way - Port 8002)**
```bash
# Ensure you are at the project root
uv run uvicorn examples.time_saved.the_easy_way:app --port 8002
```

### 3. Generate Traffic
Fire some requests to both servers:
```bash
curl http://localhost:8001/hello/world
curl http://localhost:8002/hello/world
```

### 4. Observe the Results

* **Console output**: both apps write one JSON line per request with `request_id`, `trace_id` and `span_id`.
* **Jaeger (Tracing)**: Navigate to `http://localhost:16686` and search for traces under the `hard-way-api` and `easy-way-api` services. Both produce a server span per request; on FastAPI 0.142+ the easy way also has FastAPI's own spans for dependencies, the endpoint and serialization.
* **Prometheus (Metrics)**: Navigate to `http://localhost:9090` and query `http_requests_total`. Both label requests `path="/hello/{name}"`, not `/hello/world`.

### 5. Sample output

#### Structured Logs (stdout)
**The Hard Way:**
```json
{"timestamp": "2026-02-21 03:27:21,258", "level": "INFO", "name": "api", "message": "HTTP Request", "request_id": "8d...c7", "trace_id": "58...35", "span_id": "3e...25", "request": {"method": "GET", "url": "http://localhost:8001/hello/world", "client_ip": "127.0.0.1", "status_code": 200, "duration_ms": 0.64}}
```
**The Easy Way:**
```json
{"timestamp":"2026-11-05T00:27:41.354913+00:00","level":"INFO","logger":"fastapiobserver.middleware","message":"request.completed","app_name":"easy-way-api","service":"easy-way-api","environment":"development","version":"0.0.0","log_schema_version":"1.0.0","library":"fastapiobserver","library_version":"2.3.0","request_id":"75...14","trace_id":"2d...72","span_id":"be...dd","event":{"method":"GET","path":"/hello/world","status_code":200,"duration_ms":1.193,"client_ip":"127.0.0.1","error_type":"ok","http.request.method":"GET","url.path":"/hello/world","http.response.status_code":200,"app_duration_ms":1.201,"response_start_ms":0.912,"first_body_ms":1.193,"stream_duration_ms":0.0,"http.route":"/hello/{name}","response_completed":true,"cancelled":false,"client_disconnected":false,"observation":{"coverage":"unknown"},"log_sampling":{"owner":"observer","inclusion_probability":1.0,"reason":"all"}}}
```

#### Prometheus Metrics (Scraped on /metrics)
```promql
# The Hard Way Output
http_requests_total{instance="host.docker.internal:8001",job="hard_way_api",method="GET",path="/hello/{name}",status="200"} 7.0

# The Easy Way Output
http_requests_total{environment="development",instance="host.docker.internal:8002",job="easy_way_api",method="GET",path="/hello/{name}",service="easy-way-api",status_code="200"} 7.0
```

### 6. Cleanup

Stop both `uvicorn` processes, then:

```bash
docker compose down
```
