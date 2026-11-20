# Running with uvicorn, gunicorn and Granian

`fastapi-observer` runs inside your app, so it works the same under any ASGI server: one JSON line per request and your own `logging` calls as JSON. What differs is the server's own log lines (startup, workers, signals). This page shows how to get those as JSON too, so every line from the process has the same shape and service fields.

The examples below use the default logging pipeline. For `configure_logging=False`, see [application-owned logging](logging.md#application-owned-logging).

The server configs below build `fastapiobserver.StructuredJsonFormatter` by name. Without arguments it reads the same environment variables as `install_observability()` (`OBS_APP_NAME`, `OBS_SERVICE_NAME`, `OBS_ENVIRONMENT`, `OBS_APP_VERSION`, redaction settings), so server lines carry the same fields as request lines. `OBS_PROFILE` defaults aren't applied here, so set the variables themselves (for example `OBS_REDACTION_PRESET`) if server lines must be redacted the same way.

## uvicorn

Nothing to configure. `install_observability()` routes uvicorn's loggers into the JSON pipeline and drops uvicorn's access line, because `request.completed` carries the same fields.

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Starting uvicorn from Python with an import string, `uvicorn.run("main:app")`, behaves the same.
With the app object, uvicorn applies its own logging configuration after your app was imported, so
its startup lines and its access line appear as plain text beside the JSON lines. Pass
`log_config=None` to keep them in the JSON pipeline:

```python
uvicorn.run(app, host="0.0.0.0", port=8000, log_config=None)
```

## gunicorn with `UvicornWorker`

Request lines and your app's logs are JSON by default. Gunicorn's own lines (`Starting gunicorn`, `Booting worker`, signals) come from the master process, which never imports your app, so they stay plain text unless gunicorn is told to use the JSON formatter.

Put this in `gunicorn.conf.py`. Gunicorn loads that file from the working directory automatically, or pass it with `-c`:

```python
logconfig_dict = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "fastapiobserver.StructuredJsonFormatter"}},
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "stream": "ext://sys.stdout",
        },
    },
    "root": {"level": "INFO", "handlers": []},
    "loggers": {
        "gunicorn.error": {"level": "INFO", "handlers": ["stdout"], "propagate": False},
        "gunicorn.access": {"level": "INFO", "handlers": ["stdout"], "propagate": False},
    },
}
```

```bash
gunicorn -c gunicorn.conf.py -k uvicorn.workers.UvicornWorker -w 4 main:app
```

Leave the root logger without handlers: in each worker, `install_observability()` attaches its own queue handler there, and a second one would print app logs twice. Keep gunicorn's access log off (the default); `request.completed` already covers it.

### Several workers and `--preload`

With `--preload`, Gunicorn imports your app and runs `install_observability()` in the master process before forking workers. Each worker gets its own log queue and listener thread after the fork, as described under [the log pipeline](architecture.md#the-log-pipeline). The event-loop watchdog, the thread-pool watcher and the garbage-collection timer start in each worker's ASGI lifespan. With `--preload` and OTLP export enabled, prefer `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf` over gRPC channels created pre-fork.

### Prometheus multiprocess mode

Configure a shared directory before starting Gunicorn workers:

```bash
export PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus-metrics
rm -rf "$PROMETHEUS_MULTIPROC_DIR" && mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
```

```python
# gunicorn.conf.py
from fastapiobserver import mark_prometheus_process_dead

def child_exit(server, worker):
    mark_prometheus_process_dead(worker.pid)
```

In multiprocess mode, `/metrics` exports request metrics and the diagnostic counters, but not the per-process gauges. See [metrics](advanced.md#metrics).

## Granian

Request lines and your app's logs are JSON by default. Granian's own lines (`Starting granian`, `Spawning worker`) stay plain text unless you pass a log config.

Save this as `granian-log.json`:

```json
{
  "version": 1,
  "disable_existing_loggers": false,
  "formatters": {"json": {"()": "fastapiobserver.StructuredJsonFormatter"}},
  "handlers": {
    "stdout": {"class": "logging.StreamHandler", "formatter": "json", "stream": "ext://sys.stdout"}
  },
  "loggers": {
    "_granian": {"level": "INFO", "handlers": ["stdout"], "propagate": false},
    "granian.access": {"level": "INFO", "handlers": ["stdout"], "propagate": false}
  }
}
```

```bash
granian --interface asgi --workers 4 --log-config granian-log.json main:app
```

Keep Granian's access log off (`--no-access-log`, the default); `request.completed` already covers it.

## Client disconnects and shutdown

`request.client_disconnected` means the application received `http.disconnect`
before the response finished. `event.after_disconnect_ms` measures how long it
continued afterward, and `event.after_disconnect_calls` (`{"db": N, "http": M}`)
counts the queries and outbound calls it started after the client left. The
middleware only observes the application's own reads; it adds no body reader.
If the application never reads after the client leaves, the disconnect may
remain unobserved. A disconnect received after completion is not an abandoned
request.

Exceptions and cancellation keep their existing event names, with independent
`event.cancelled` and `event.client_disconnected` fields. Cancellation alone does
not identify a disconnect, timeout or shutdown. A successful response send keeps
its status even when a disconnect was observed concurrently.

The server owns signals and graceful request draining. Configure its graceful
timeout and give the process supervisor enough additional time for logging and
configured exporter timeouts. CI exercises Uvicorn SIGTERM during ordinary,
streaming and background work; its test budgets are in the
[release budgets](contributing.md#release-validation-budgets).

## Checking the result

Every line of output should now parse as JSON. A quick check, assuming the server writes to `server.log`:

```bash
grep -vc '^{' server.log
```

`0` means every line is JSON.
