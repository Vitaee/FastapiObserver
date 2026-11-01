# Running with uvicorn, gunicorn and Granian

`fastapi-observer` runs inside your app, so it works the same under any ASGI server: one JSON line per request and your own `logging` calls as JSON. What differs is the server's own log lines (startup, workers, signals). This page shows how to get those as JSON too, so every line from the process has the same shape and service fields.

The server configs below build `fastapiobserver.StructuredJsonFormatter` by name. Without arguments it reads the same environment variables as `install_observability()` (`OBS_APP_NAME`, `OBS_SERVICE_NAME`, `OBS_ENVIRONMENT`, `OBS_APP_VERSION`, redaction settings), so server lines carry the same fields as request lines.

## uvicorn

Nothing to configure. `install_observability()` routes uvicorn's loggers into the JSON pipeline and drops uvicorn's access line, because `request.completed` carries the same fields.

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

## gunicorn with `UvicornWorker`

Request lines and your app's logs are JSON out of the box. Gunicorn's own lines (`Starting gunicorn`, `Booting worker`, signals) come from the master process, which never imports your app, so they stay plain text unless gunicorn is told to use the JSON formatter.

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

For Prometheus metrics with several workers, see [multi-worker gunicorn](advanced.md#multi-worker-gunicorn).

## Granian

Request lines and your app's logs are JSON out of the box. Granian's own lines (`Starting granian`, `Spawning worker`) stay plain text unless you pass a log config.

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

## Checking the result

Every line of output should now parse as JSON. A quick check, assuming the server writes to `server.log`:

```bash
grep -vc '^{' server.log
```

`0` means every line is JSON.
