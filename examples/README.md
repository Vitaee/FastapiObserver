# Examples

Run these from the repository root. For the Python examples and tests:

```bash
uv sync --extra dev --extra prometheus --extra otel --extra otel-sqlalchemy
uv run uvicorn examples.basic_app:app --reload
curl http://localhost:8000/items/42
```

`install_observability()` manages logging and its lifespan by default. Pass
`configure_logging=False` to retain application-owned logging. Settings come from
`OBS_*` and the standard `OTEL_*` variables; see [configuration](../docs/configuration.md).

| Example | Try | Demonstrates |
|---|---|---|
| [basic_app.py](basic_app.py) | `GET /items/42`, `GET /crash` | One-call setup, request IDs, structured errors |
| [existing_logging_app.py](existing_logging_app.py) | `GET /orders/42`, `GET /failure` | Existing JSON formatter and application redaction, with correlated diagnostics and no additional log handler |
| [security_presets_app.py](security_presets_app.py) | `POST /payments` with JSON | PCI field redaction and opt-in JSON body capture |
| [allowlist_app.py](allowlist_app.py) | `GET /users/42` | Keeping selected event fields while retaining the log envelope |
| [otel_app.py](otel_app.py) | `GET /orders/42` | Correlated logs and traces; enable `OBS_OTEL_ENABLED=true` with a collector |
| [db_tracing_app.py](db_tracing_app.py) | `POST /users/ada`, then `GET /users` | Async SQLAlchemy query counts, with optional SQL tracing |
| [graphql_app.py](graphql_app.py) | `POST /graphql` with `{"query":"query GetHello { hello }"}` | GraphQL operation names in logs and server spans |

Substitute the module name in the `uvicorn` command. Enable `/metrics` with
`OBS_METRICS_ENABLED=true`.
The database example uses an in-memory SQLite database and closes it at shutdown.
`DATABASE_URL` can select another async SQLAlchemy driver. Its query counts work
without tracing; SQL spans require a supported instrumentation version.

For a Python operation without a web application, run
`python examples/python_operation.py` with SQLAlchemy and HTTPX installed. It
performs real SQLite queries and loopback HTTP, forwards correlation, and emits
an experimental operation summary through the existing JSON logging pipeline.
See [experimental operations](../docs/experimental-core.md).
`python examples/callback_operation.py` demonstrates an explicit custom-client
completion handle across an executor callback, using only standard-library
SQLite and the existing logging pipeline.

## Reproduce and diagnose failures

The [full-stack demo](full_stack/README.md) runs three copies of one app alongside
Grafana, Prometheus, Loki and Tempo. Its failure lab deliberately blocks the event
loop, occupies a database pool and runs N+1 queries, then shows the corresponding
warnings and a batched-query alternative.

```bash
docker compose -f examples/full_stack/docker-compose.yml up --build -d --wait
python3 examples/full_stack/failure_lab.py
```

Open [Grafana](http://localhost:3000); no login is required for this local demo.
The driver uses Python's standard library. Each scenario can also be run on its
own with `--scenario event-loop`, `db-pool` or `n-plus-one`.

To run the app without Docker or a telemetry backend:

```bash
OBS_METRICS_ENABLED=true OBS_LOOP_BLOCK_THRESHOLD_MS=100 \
  uv run uvicorn examples.full_stack.app:app --host 127.0.0.1 --port 8000
# In another terminal:
python3 examples/full_stack/failure_lab.py
```

Other deployment examples:

- [Kubernetes](k8s/README.md): Kustomize manifests for the app and telemetry backends.
- [Time saved](time_saved/README.md): the same app configured by hand and with this library.
- [Migration](migration_example/migration_made.md): replacing an existing logging setup.
