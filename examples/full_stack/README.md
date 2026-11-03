# Diagnose a slow API

One app runs as `app-a`, `app-b` and `app-c`, with Prometheus, Loki, Tempo and
Grafana. The failure lab creates three bounded failures and shows how to find
and fix each one. It uses a local SQLite database, so no database service is needed.

## Run

From the repository root, with Docker Compose and Python 3.11+:

```bash
docker compose -f examples/full_stack/docker-compose.yml up --build -d --wait
python3 examples/full_stack/failure_lab.py
```

Compose waits for the app health checks. The driver uses only Python's standard
library and exits with an error if the demo is unreachable or a scenario cannot
be triggered. It prints the warning to look for; the server emits the actual
warning. Logs can be inspected without Grafana:

```bash
docker compose -f examples/full_stack/docker-compose.yml logs --no-log-prefix app-a
```

Open [Grafana](http://localhost:3000) and select **Explore → Loki**. This local demo
has anonymous access enabled. Paste:

```logql
{service_name="app-a"} | json | level="WARNING"
```

Warnings are rate-limited: pool warnings appear at most once a minute, and an N+1
statement is reported once per route and process. Restart `app-a` for fresh warnings
when repeating the lab:

```bash
docker compose -f examples/full_stack/docker-compose.yml restart app-a
```

## Three failures and their fixes

| Scenario | Trigger | Signal | Fix to try |
|---|---|---|---|
| Blocked event loop | `GET /lab/block` calls synchronous `time.sleep` inside `async def` | `event_loop.blocked`, with the blocking stack | `GET /slow` awaits `asyncio.sleep`, leaving the worker responsive |
| Exhausted database pool | `GET /lab/db-pool` holds the only connection while another request arrives | `db.pool_exhausted`, with checked-out connections and pool limit | Release connections before unrelated I/O; size pools for actual concurrency |
| N+1 queries | `GET /lab/n-plus-one` fetches IDs, then reads each item separately | `db.n_plus_one`; the request line has `event.db.count=11` | `GET /lab/batched` returns the same ten items in one query |

Run one scenario:

```bash
python3 examples/full_stack/failure_lab.py --scenario event-loop
python3 examples/full_stack/failure_lab.py --scenario db-pool
python3 examples/full_stack/failure_lab.py --scenario n-plus-one
```

The pool driver checks `/metrics` to confirm the connection is occupied before
sending another request. Sleep durations are bounded to three seconds. The lab
routes deliberately contain bad application code and belong only in this demo.
The watchdog is enabled at 100 ms in Compose; SQL query counts do not require traces.

In **Explore → Prometheus**, inspect:

```promql
fastapiobserver_db_pool_checked_out
fastapiobserver_db_pool_limit

histogram_quantile(
  0.95,
  sum(rate(http_request_duration_seconds_bucket[5m])) by (le, service)
)
```

The **API Overview** dashboard shows request rates, latency, errors, CPU, memory
and logs. Use its service dropdown to compare the three app instances.

## Cross-service correlation

```bash
curl -H 'X-Request-ID: demo-chain' http://localhost:8000/chain
```

`/chain` forwards the request ID and current W3C trace context to `app-b` and
`app-c` using `inject_trace_headers()`. Their server spans share the same trace;
this example does not add HTTP client spans. Find the request's `trace_id` on its
JSON log line, then follow the link from Loki to Tempo. Search logs by request ID:

```logql
{service_name=~"app-[abc]"} | json | request_id="demo-chain"
```

The containers call each other over their Compose network. When running outside
Docker, `TARGET_B` and `TARGET_C` default to `http://localhost:8001` and
`http://localhost:8002`.

## Other routes and traffic

| Route | Purpose |
|---|---|
| `GET /health` | Readiness; excluded from request metrics |
| `GET /items/42`, `GET /users/1` | Normal requests with a request ID |
| `GET /slow?seconds=0.5` | A predictable nonblocking delay |
| `GET /error?status=400` or `status=500` | A deliberate HTTP error; 400, 404, 500 and 503 are supported |
| `GET /metrics` | Prometheus request, queue and pool metrics |

Generate ordinary traffic for the dashboard:

```bash
./examples/full_stack/generate_traffic.sh 60
# One service, with a bounded readiness wait:
APPS=http://localhost:8000 READY_TIMEOUT=10 ./examples/full_stack/generate_traffic.sh 10
```

`app-a`, `app-b` and `app-c` listen on ports 8000, 8001 and 8002. Grafana uses 3000,
Prometheus 9090, Loki 3100 and Tempo 3200. The collector accepts OTLP on 4317/4318.
Configuration lives in `docker-compose.yml` and `config/`. The image uses SQLAlchemy
2.0 for SQL tracing compatibility and copies only package inputs into the build.
SQL comments are disabled so repeated statements remain comparable.

## Without Docker

After the [example dependency setup](../README.md), start one app:

```bash
OBS_METRICS_ENABLED=true OBS_LOOP_BLOCK_THRESHOLD_MS=100 \
  uv run uvicorn examples.full_stack.app:app --host 127.0.0.1 --port 8000
```

Run `python3 examples/full_stack/failure_lab.py` from another terminal. The three
warnings are written to stdout; no collector or Grafana is required. Set
`--base-url http://localhost:8001` to use another running instance.

## Cleanup

```bash
docker compose -f examples/full_stack/docker-compose.yml down
# Also discard this demo's stored logs and traces when they are no longer needed:
docker compose -f examples/full_stack/docker-compose.yml down --volumes
```
