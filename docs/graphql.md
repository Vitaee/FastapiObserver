# GraphQL Observability (Strawberry)

GraphQL APIs often collapse all traffic into one HTTP route (usually `POST /graphql`).
That makes route-level telemetry too coarse for incident response and capacity planning.

`fastapi-observer` provides a native Strawberry extension that extracts operation names
and enriches both logs and traces at operation level.

## Why Use This Integration

Without operation-aware telemetry, these different requests all look identical at HTTP level:
- `GetUsersQuery`
- `CheckoutMutation`
- `AdminAuditReportQuery`

This integration restores per-operation visibility while keeping your existing FastAPI + Strawberry architecture.

## Benefits

| Benefit | What you get |
|---|---|
| Faster incident triage | See which GraphQL operation is causing errors/latency, not just `/graphql` |
| Better trace readability | Span names become `graphql.operation.<operation_name>` |
| Cleaner log correlation | Structured logs include `user_context.graphql.operation_name` |
| Low coupling | Extension is duck-typed; no hard `strawberry-graphql` dependency in `fastapi-observer` core |
| Safe failure behavior | Integration failures degrade gracefully and do not crash request handling |

## Prerequisites

- FastAPI service using `strawberry-graphql`
- `fastapi-observer` installed and configured
- Optional: OpenTelemetry enabled if you also want span renaming/attributes

Install:

```bash
# Core + Strawberry usage in your app
pip install fastapi-observer strawberry-graphql

# Optional: enable trace export setup
pip install "fastapi-observer[otel]"
```

## Minimal Setup

```python
from fastapi import FastAPI
import strawberry
from strawberry.fastapi import GraphQLRouter

from fastapiobserver import ObservabilitySettings, install_observability
from fastapiobserver.integrations.strawberry import StrawberryObservabilityExtension

app = FastAPI()

settings = ObservabilitySettings(
    app_name="graphql-api",
    service="graphql-svc",
    environment="production",
)
install_observability(app, settings)


@strawberry.type
class Query:
    @strawberry.field
    def hello(self) -> str:
        return "world"


schema = strawberry.Schema(
    query=Query,
    extensions=[StrawberryObservabilityExtension],
)

app.include_router(GraphQLRouter(schema), prefix="/graphql")
```

Runnable example: `examples/graphql_app.py`

## What Changes After Enabling

### Logs

Request logs get GraphQL operation context:

```json
{
  "event": {
    "method": "POST",
    "path": "/graphql"
  },
  "user_context": {
    "graphql": {
      "operation_name": "GetUsersQuery"
    }
  }
}
```

### Traces (when OTel is enabled)

- Span name changes from `POST /graphql` to `graphql.operation.GetUsersQuery`
- Span attribute is added: `graphql.operation.name=GetUsersQuery`

This makes dashboards and trace search much more actionable for GraphQL traffic.

## Verification Checklist

1. Start your app and run a named operation:

```bash
curl -X POST http://localhost:8000/graphql \
  -H "Content-Type: application/json" \
  -d '{"query":"query GetUsersQuery { hello }","operationName":"GetUsersQuery"}'
```

2. Confirm logs include:
- `user_context.graphql.operation_name = "GetUsersQuery"`

3. If OTel is enabled, confirm in your tracing backend:
- Span name is `graphql.operation.GetUsersQuery`
- Span attribute `graphql.operation.name` exists

4. Send a request without operation name and confirm fallback:

```bash
curl -X POST http://localhost:8000/graphql \
  -H "Content-Type: application/json" \
  -d '{"query":"{ hello }"}'
```

Expected fallback in context:
- `user_context.graphql.operation_name = "AnonymousOperation"`

## Edge Cases and Runtime Behavior

| Scenario | Behavior |
|---|---|
| Missing/unknown operation name | Falls back to `AnonymousOperation` |
| Strawberry execution context parsing error | Error is swallowed; request continues |
| OpenTelemetry package not present | Log enrichment still works; trace rename is skipped |
| Existing `user_context` already set | Existing keys are preserved and `graphql.operation_name` is merged |
| `user_context["graphql"]` is non-dict | Extension does not overwrite it; use a dict shape for GraphQL context |

## Production Best Practices

1. Always send explicit GraphQL operation names from clients.
2. Keep GraphQL body capture off unless absolutely required for debugging/compliance.
3. If body capture is required, keep strict redaction and media-type allowlists enabled.
4. Use operation-level traces/logs for debugging, but keep metrics labels route-templated to avoid cardinality spikes.
5. Alert on operation-specific error and latency patterns (for example `CheckoutMutation` p95 and failure rate).
6. Register `StrawberryObservabilityExtension` once per schema; avoid duplicate extension wiring.

## When This Is Most Valuable

Use this integration when:
- your API traffic is primarily GraphQL,
- multiple critical business flows share one `/graphql` endpoint,
- on-call responders need operation-level visibility to isolate incidents quickly.

---
