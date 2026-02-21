# Time-Saved: DX Parity Examples

This directory contains two FastAPI applications that achieve **functionally equivalent** complex observability outcomes (OTLP traces, Prometheus metrics, structured JSON logging).

1. `the_hard_way.py`: A raw implementation needing `~160` lines of boilerplate using the underlying SDKs directly.
2. `the_easy_way.py`: A declarative implementation needing `~30` lines using `fastapiobserver`.

## Running the Examples Locally

You can spin up local Jaeger and Prometheus instances to physically see both applications transmitting functionally equivalent telemetry to the backends.

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

* **Console Output**: Look at both terminal windows. Both emit functionally identical JSON logs with trace and request injection.
* **Jaeger (Tracing)**: Navigate to `http://localhost:16686` and search for traces under the `hard-way-api` and `easy-way-api` services. Notice they both feature identical spans spanning the FastAPI lifecycle.
* **Prometheus (Metrics)**: Navigate to `http://localhost:9090` and query `http_requests_total`. Notice how both correctly avoid cardinality explosions by parsing the literal `/hello/{name}` route instead of `/hello/world`.

### 5. Expected Output Parity

Both the hand-rolled ~160 line script and the ~30 line `fastapiobserver` script generate equivalent contextual telemetry.

#### Structured Logs (stdout)
**The Hard Way:**
```json
{"timestamp": "2026-02-21 03:27:21,258", "level": "INFO", "name": "api", "message": "HTTP Request", "request_id": "8d...c7", "trace_id": "58...35", "span_id": "3e...25", "request": {"method": "GET", "url": "http://localhost:8001/hello/world", "client_ip": "127.0.0.1", "status_code": 200, "duration_ms": 0.64}}
```
**The Easy Way:**
```json
{"timestamp":"2026-02-21T00:27:41.354913+00:00","level":"INFO","logger":"fastapiobserver.middleware","message":"request.completed","app_name":"easy-way-api","service":"easy-way-api","request_id":"75...14","trace_id":"2d...72","span_id":"be...dd","event":{"method":"GET","path":"/hello/world","status_code":200,"duration_ms":1.193,"client_ip":"127.0.0.1","error_type":"ok","http.request.method":"GET","url.path":"/hello/world","http.response.status_code":200}}
```

#### Prometheus Metrics (Scraped on /metrics)
Notice how both applications protect metric cardinality mapping the traffic down to the ASGI `path="/hello/{name}"` routing template:
```promql
# The Hard Way Output
http_requests_total{instance="host.docker.internal:8001",job="hard_way_api",method="GET",path="/hello/{name}",status="200"} 7.0

# The Easy Way Output
http_requests_total{environment="development",instance="host.docker.internal:8002",job="easy_way_api",method="GET",path="/hello/{name}",service="easy-way-api",status_code="200"} 7.0
```

### 5. Cleanup
```bash
# Don't forget to kill your uvicorn processes!
docker compose down
```
