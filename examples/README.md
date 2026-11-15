# Examples

Run these from the repository root. For the Python examples and tests:

```bash
uv sync --extra dev --extra prometheus --extra otel --extra otel-sqlalchemy
uv run uvicorn examples.basic_app:app --reload
curl http://localhost:8000/items/42
```

Settings come from `OBS_*` and the standard `OTEL_*` variables; see
[configuration](../docs/configuration.md).

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

Scripts without a web app:

- `python examples/python_operation.py` (needs SQLAlchemy and HTTPX) and
  `python examples/callback_operation.py`: [experimental operations](../docs/experimental-core.md).
- `python examples/query_budget.py`: an engine-scoped SQL query budget with
  `assert_query_budget()`, including failed executions and an over-budget assertion.

Larger examples:

- [Full stack](full_stack/README.md): three app instances with Grafana,
  Prometheus, Loki and Tempo, and a failure lab for blocked event loops, full
  database pools and N+1 queries.
- [Kubernetes](k8s/README.md): Kustomize manifests for the app and telemetry backends.
- [Time saved](time_saved/README.md): the same app configured by hand and with this library.
- [Migration](migration_example/migration_made.md): replacing an existing logging setup.
