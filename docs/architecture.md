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
  app -- "OBS_OTEL_ENABLED" --> otlp
  logs -. "OBS_OTEL_LOGS_ENABLED" .-> otlp
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

Purple is fastapi-observer, green is where data leaves the process, and orange is what you run already. The optional runtime control endpoint is left out.

These diagrams show the default library-owned logging pipeline. With
`configure_logging=False`, diagnostics use the application's existing handlers,
sanitizer, formatting, export and shutdown. The library adds no logging queue or
log exporter in that mode. See [application-owned logging](logging.md#application-owned-logging).

## What `install_observability()` Wires Up

1. Profile-aware config bootstrap (`OBS_PROFILE`) and env-driven defaults for settings/policies.
2. Structured logging pipeline (JSON formatter + bounded async queue handler), unless `configure_logging=False`.
3. Metrics backend and `/metrics` endpoint when metrics are enabled.
4. OTel tracing setup when OTel is enabled.
5. Optional OTel metrics setup, and OTel logs when enabled with library-owned logging.
6. Request logging middleware with correlation, body-capture controls and context cleanup.
7. Runtime control endpoint when runtime control is enabled.
8. A lifespan hook that runs diagnostic watchers and flushes library-managed logging and the OTel providers it uses when the app stops.

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
  Note over A,L: Records below OBS_LOG_LEVEL, or from a request that isn't sampled, are held on the request
  A-->>M: Final response body or trailers
  M-->>C: Response with an X-Request-ID header
  M->>P: Count and response duration, labelled by route template
  A-->>M: Application returns, raises or is cancelled (including background work)
  alt unhandled exception
    M->>L: Held records, then request.failed at ERROR with error.fingerprint
  else cancelled
    M->>L: request.cancelled at WARNING, status 499 if no response started
  else 5xx
    M->>L: Held records, then request.server_error at ERROR
  else 4xx
    M->>L: request.client_error at WARNING
  else status below 400
    M->>L: request.completed at INFO
  end
  M->>M: N+1 check, then clear the context
```

- The request ID comes from `X-Request-ID` only when the peer is a trusted proxy. Otherwise it's a new UUID. The HTTPX/HTTPX2 and Requests client helpers forward it without tracing; with OpenTelemetry, instrumented clients can send it alongside `traceparent`.
- A request that takes `OBS_LOG_SLOW_REQUEST_MS` or longer has its sampled-out records written too, and so does every request that fails.
- The request line is redacted before it's written. It also counts completed database queries and outbound HTTP calls (`db` and `http`, each with `count` and `ms`): SQLAlchemy queries from its events, HTTPX/HTTPX2 and Requests from explicitly installed client helpers, and other clients from sampled spans. HTTP failures add bounded categories in `http.errors`; a call and its span count once.
- HTTP metrics and `duration_ms` stop at the final successful response send (or interruption). `app_duration_ms` includes background work and cleanup. The request log is emitted at application completion, retaining later failures without changing an already-sent status. See [timing semantics](logging.md#what-the-durations-mean).
- Requests no route matches, such as scanners, CORS preflights and requests an auth middleware rejects, share one `<unmatched>` metrics label, and made-up methods share `_OTHER`. No client can add a time series per URL or per method.

## The log pipeline

```mermaid
flowchart TB
  rec["logger.info(...)<br/>from any thread"]

  subgraph handler["Queue handler, on the caller's thread, no I/O"]
    direction TB
    ctx["Add request_id,<br/>trace_id, span_id"]
    held{"Below OBS_LOG_LEVEL<br/>or not sampled?"}
    hold["Hold on the request<br/>newest 200"]
    full{"Queue full?"}
    ctx --> held
    held -- "no" --> full
    held -- "yes" --> hold
    hold -- "request fails or is slow" --> full
  end

  rec --> ctx
  hold -- "request ends fine" --> dropped(["Dropped"])
  full -- "no" --> queue[["Bounded queue, OBS_LOG_QUEUE_MAX_SIZE"]]
  full -- "yes: OBS_LOG_QUEUE_OVERFLOW_POLICY<br/>drop_oldest, drop_newest or block" --> queue

  subgraph listener["Listener thread, one per process"]
    direction TB
    breaker["Circuit breaker per sink"] --> fmt["Format: enrichers, redaction, JSON"]
  end

  queue --> breaker
  fmt --> stdout["stdout"]
  fmt --> file["Rotating file"]
  fmt --> custom["Sinks added with register_sink()"]
  fmt --> otlp["OTLP log exporter"]

  classDef lib fill:#6366f11f,stroke:#6366f1,stroke-width:1.5px
  classDef out fill:#10b9811f,stroke:#10b981,stroke-width:1.5px
  classDef muted fill:#94a3b81f,stroke:#94a3b8
  class ctx,held,hold,full,queue,breaker,fmt lib
  class stdout,file,custom,otlp out
  class rec,dropped muted
```

- **Holding is off by default.** It's on only with `OBS_LOG_LEVEL_ON_ERROR` set, or `OBS_LOG_SAMPLE_RATE` below 1. Sampling does not hold `WARNING` and higher records; error-verbosity retention can hold them if they are below `OBS_LOG_LEVEL`.
- **Default writes do not wait for sink I/O.** Formatting, redaction and writing happen on the listener thread. The default overflow policy drops records when the queue is full; choosing `block` can stall the calling thread up to its configured timeout. Application-owned logging follows its own handler behavior.
- **Overload is counted, not hidden.** `/metrics` shows the queue's size, enqueued, dropped and blocked records, each sink's circuit-breaker state, and log bytes per route (in Prometheus multiprocess mode these per-worker metrics aren't exported).
- **After a fork**, as in gunicorn with `--preload`, the child process gets a new queue, new locks and a new listener thread. It doesn't touch anything the parent's threads could have been holding.
- **When the app stops**, its lifespan waits for the queue to be written and flushes the outputs, but leaves logging running: other apps in the process share it, and a reused TestClient starts the app again. `atexit` stops the listener when the process exits.

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

- `event_loop.blocked` needs `OBS_LOOP_BLOCK_THRESHOLD_MS`. The pool warnings are always on. SQLAlchemy query totals and explicitly installed HTTP client diagnostics do not need tracing.
- `request.cancelled` means the application received cancellation. It does not identify whether the cause was a client disconnect, a timeout or shutdown. A disconnected client can leave a handler running without cancellation; explicit disconnect and shutdown diagnostics remain on the [roadmap](https://github.com/Vitaee/FastapiObserver/blob/main/ROADMAP.md#diagnosis).

### Route Exclusion Auto-Discovery

At application lifespan start, observability scans registered routes and auto-discovers paths that should be excluded from telemetry noise:

- FastAPI utility routes such as `/docs`, `/redoc`, `/openapi.json`
- Routes marked `include_in_schema=False`

Discovered paths are normalized (including dynamic path variants like `/:id`) and used for metrics exclusion. They're added to the trace exclusions too, unless those were set explicitly, with `OBS_OTEL_EXCLUDED_URLS` or OpenTelemetry's `OTEL_PYTHON_*_EXCLUDED_URLS`.

### Internal Package Layout (Contributor Map)

The project is now organized as focused subpackages instead of large monolithic modules:

- `fastapiobserver/logging/`: formatter, queueing, filters, setup lifecycle, sink circuit-breakers.
- `fastapiobserver/middleware/`: request logging orchestration, context, IP resolution, headers, body capture, metrics hooks.
- `fastapiobserver/sinks/`: sink protocol, registry, built-in stdout and file sinks, and the factory that assembles them.
- `fastapiobserver/metrics/`: backend contract and registry; `prometheus.py` holds the Prometheus backend, the `/metrics` endpoint (rendered by `prometheus_client`) and multiprocess helpers.
- `fastapiobserver/security/`: policy/settings models, normalization helpers, redaction engine, trusted-proxy utilities.
- `fastapiobserver/otel/`: OTel settings/resource/tracing/logs/metrics/lifecycle helpers.

The top-level `fastapiobserver` package exports only the names in its `__init__.py`. Everything else is imported from its submodule, without the same promise of stability.

---
