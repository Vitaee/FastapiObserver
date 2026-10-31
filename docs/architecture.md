# Architecture

Why it's built this way, with the evidence and what each choice cost, is in the [design decisions](decisions/index.md).

## The system at a glance

Everything runs inside your FastAPI process. There is no agent and no vendor backend: data leaves as JSON lines on stdout, as a Prometheus `/metrics` page, and over OTLP.

```mermaid
flowchart TB
  subgraph inbound["Inbound"]
    direction LR
    client["Clients"] --> proxy["Load balancer or proxy<br/>X-Request-ID, traceparent"]
  end

  subgraph process["Your FastAPI process"]
    direction LR
    mw["RequestLoggingMiddleware<br/>pure ASGI"] --> app["Your routes<br/>async on the event loop,<br/>sync in AnyIO's thread pool"]
    watch["Watchers<br/>event loop, thread pool,<br/>DB pools"]
    logs["Log pipeline<br/>bounded queue,<br/>listener thread"]
    mw -- "one line per request" --> logs
    app -. "your logging calls" .-> logs
    watch -- "warnings" --> logs
  end

  subgraph out["Leaves the process"]
    direction LR
    stdout["stdout<br/>JSON lines"]
    metrics["/metrics<br/>route-template labels"]
    otlp["OTLP exporter<br/>spans, optional logs"]
  end

  subgraph backends["What you already run"]
    direction LR
    shipper["Vector, Promtail,<br/>Fluent Bit"] --> logstore["Loki,<br/>Elasticsearch"]
    prom["Prometheus"]
    collector["OTel Collector"] --> traces["Tempo, Jaeger,<br/>any OTLP backend"]
  end

  proxy --> mw
  app -- "X-Request-ID, traceparent" --> downstream["Other services"]
  app -- "SQLAlchemy" --> db[("Database")]
  logs --> stdout
  mw --> metrics
  app -- "OTEL_ENABLED" --> otlp
  logs -. "OTEL_LOGS_ENABLED" .-> otlp
  stdout --> shipper
  metrics -- "scrape" --> prom
  otlp --> collector
  logstore --> grafana["Grafana"]
  prom --> grafana
  traces --> grafana

  classDef lib fill:#6366f11f,stroke:#6366f1,stroke-width:1.5px
  classDef yours fill:#94a3b81f,stroke:#94a3b8
  classDef out fill:#10b9811f,stroke:#10b981,stroke-width:1.5px
  classDef ext fill:#f59e0b14,stroke:#f59e0b
  class mw,watch,logs lib
  class app,client,proxy,downstream,db yours
  class stdout,metrics,otlp out
  class shipper,logstore,prom,collector,traces,grafana ext
```

Purple is fastapi-observer, green is where data leaves the process, and orange is what you run already. Optional parts are left out: the runtime control endpoint, the Logtail sink and the tamper-evident audit log.

## What `install_observability()` Wires Up

1. Profile-aware config bootstrap (`OBS_PROFILE`) and env-driven defaults for settings/policies.
2. Structured logging pipeline (JSON formatter + bounded async queue handler).
3. Metrics backend and `/metrics` endpoint when metrics are enabled.
4. OTel tracing setup when OTel is enabled.
5. Optional OTel logs/metrics setup when OTLP settings are enabled.
6. Request logging middleware with sanitization and context cleanup.
7. Runtime control endpoint when runtime control is enabled.
8. Lifespan-based teardown hooks for logging and SQLAlchemy instrumentation cleanup.

## One request

```mermaid
sequenceDiagram
  autonumber
  participant C as Client
  participant M as Middleware
  participant A as Your route
  participant L as Log pipeline
  participant P as /metrics

  C->>M: HTTP request
  M->>M: Pool check, warns once a minute if a pool is full
  M->>M: Client IP and request ID, trusted only from known proxies
  M->>M: Request state in a ContextVar, seen by sync code in the thread pool too
  M->>A: Call the app
  A-)L: Your log records, with request_id and trace_id added
  Note over A,L: Records below LOG_LEVEL, or from a request that isn't sampled, are held on the request
  A-->>M: Response, exception or cancellation
  M-->>C: Response with an X-Request-ID header
  alt status below 400
    M->>L: request.completed at INFO
  else 4xx
    M->>L: request.client_error at WARNING
  else 5xx
    M->>L: Held records, then request.server_error at ERROR
  else exception
    M->>L: Held records, then request.failed at ERROR with error.fingerprint
  else cancelled
    M->>L: request.cancelled at WARNING, status 499 if no response started
  end
  M->>P: Count and duration, labelled by route template
  M->>M: N+1 check, then clear the context
```

- The request ID comes from `X-Request-ID` only when the peer is a trusted proxy. Otherwise it's a new UUID. Outbound calls from clients OpenTelemetry instruments, such as `httpx` and `requests`, forward it along with `traceparent`.
- A request that takes `LOG_SLOW_REQUEST_MS` or longer has its sampled-out records written too, and so does every request that fails.
- The request line is redacted before it's written. With tracing on, it also counts the request's database queries and outbound HTTP calls (`db` and `http`, each with `count` and `ms`).
- 404s for paths no route matches share one `<unmatched>` metrics label, so scanners can't add a time series per URL.

## The log pipeline

```mermaid
flowchart TB
  rec["logger.info(...)<br/>from any thread"]

  subgraph handler["Queue handler, on the caller's thread, no I/O"]
    direction TB
    ctx["Add request_id,<br/>trace_id, span_id"]
    held{"Below LOG_LEVEL<br/>or not sampled?"}
    hold["Hold on the request<br/>newest 200"]
    full{"Queue full?"}
    ctx --> held
    held -- "no" --> full
    held -- "yes" --> hold
    hold -- "request fails or is slow" --> full
  end

  rec --> ctx
  hold -- "request ends fine" --> dropped(["Dropped"])
  full -- "no" --> queue[["Bounded queue, LOG_QUEUE_MAX_SIZE"]]
  full -- "yes: LOG_QUEUE_OVERFLOW_POLICY<br/>drop_oldest, drop_newest or block" --> queue

  subgraph listener["Listener thread, one per process"]
    direction TB
    breaker["Circuit breaker per sink"] --> fmt["Format: enrichers, redaction, JSON"]
  end

  queue --> breaker
  fmt --> stdout["stdout"]
  fmt --> file["Rotating file"]
  fmt --> logtail["Logtail, with a<br/>dead-letter file"]
  fmt --> otlp["OTLP log exporter"]

  classDef lib fill:#6366f11f,stroke:#6366f1,stroke-width:1.5px
  classDef out fill:#10b9811f,stroke:#10b981,stroke-width:1.5px
  classDef muted fill:#94a3b81f,stroke:#94a3b8
  class ctx,held,hold,full,queue,breaker,fmt lib
  class stdout,file,logtail,otlp out
  class rec,dropped muted
```

- **Holding is off by default.** It's on only with `LOG_LEVEL_ON_ERROR` set, or `LOG_SAMPLE_RATE` below 1. `WARNING` and higher are never held.
- **Your code never waits on I/O.** The calling thread only adds context and puts the record on the queue. Formatting, redaction and writing happen on the listener thread, so a slow stdout or a down log service can't stall the event loop.
- **Overload is counted, not hidden.** `/metrics` shows the queue's size, enqueued, dropped and blocked records, each sink's circuit-breaker state, and log bytes per route.
- **After a fork**, as in gunicorn with `--preload`, the child process gets a new queue, new locks and a new listener thread. It doesn't touch anything the parent's threads could have been holding.
- **On shutdown**, the lifespan, or `atexit` without one, stops the listener after it writes what's left in the queue.

## Diagnosing "slow, but CPU is low"

This is the question asked most in FastAPI's GitHub Discussions about production. Requests are waiting, not working, and each place they can wait has its own warning.

```mermaid
flowchart TD
  start(["p99 latency climbs,<br/>CPU stays low"]) --> which{"Which warning<br/>is in the logs?"}

  which -- "event_loop.blocked" --> loop["A sync call inside async def<br/>stopped the event loop.<br/>event.stack shows the line."]
  loop --> loopfix["Make the endpoint def,<br/>use run_in_threadpool,<br/>or an async client"]

  which -- "threadpool.saturated" --> threads["Every AnyIO thread is busy<br/>and calls are waiting"]
  threads --> threadsfix["Find slow sync endpoints and<br/>dependencies, make them async,<br/>or raise the thread limit"]

  which -- "db.pool_exhausted" --> pool["Every connection<br/>is checked out"]
  pool --> poolfix["Shorten how long sessions are held,<br/>or size pool_size and max_overflow"]

  which -- "threadpool.saturated and<br/>db.pool_exhausted together" --> deadlock["Deadlock: sync code holds<br/>connections while it waits for a thread"]
  deadlock --> deadlockfix["Keep pool_size + max_overflow at or<br/>above the thread limit, 40 by default"]

  which -- "none" --> line{"What does the<br/>request line show?"}
  line -- "db.count is high" --> nplus["db.n_plus_one:<br/>one query per item"]
  nplus --> nplusfix["One query with<br/>a join or IN"]
  line -- "http.ms is high" --> upstream["A downstream service is slow"]
  upstream --> upstreamfix["Follow the trace_id<br/>into its spans"]

  classDef symptom fill:#ef44441f,stroke:#ef4444,stroke-width:1.5px
  classDef signal fill:#f59e0b1f,stroke:#f59e0b
  classDef fix fill:#10b9811f,stroke:#10b981
  class start symptom
  class loop,threads,pool,deadlock,nplus,upstream signal
  class loopfix,threadsfix,poolfix,deadlockfix,nplusfix,upstreamfix fix
```

- `event_loop.blocked` needs `OBS_LOOP_BLOCK_THRESHOLD_MS`. The pool warnings are always on. The `db` and `http` counts need tracing.
- When clients or the load balancer give up before you answer, the same slowdown also shows as a rise in `request.cancelled` lines.

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
- `fastapiobserver/metrics/`: backend contract and registry; `prometheus.py` holds the Prometheus backend, the `/metrics` endpoint (rendered by `prometheus_client`) and multiprocess helpers.
- `fastapiobserver/security/`: policy/settings models, normalization helpers, redaction engine, trusted-proxy utilities.
- `fastapiobserver/otel/`: OTel settings/resource/tracing/logs/metrics/lifecycle helpers.

Public imports remain backward-compatible via package facades (`__init__.py` re-exports).

---
