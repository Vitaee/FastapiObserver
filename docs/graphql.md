# GraphQL Observability (Strawberry)

A GraphQL API sends every operation to one route, usually `POST /graphql`.
`StrawberryObservabilityExtension` adds the operation name to the request's log
line and, with tracing on, to its server span. Importing it without
`strawberry-graphql` installed raises `ImportError`. An error while renaming
the span is logged at `DEBUG` as `strawberry_extension.otel_failed` and does not
fail the request.

```bash
pip install fastapi-observer strawberry-graphql
pip install "fastapi-observer[otel]"  # only to rename server spans
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

- The request's server span is renamed from `POST /graphql` to `graphql.operation.GetUsersQuery`, whether FastAPI traces the request itself (0.142+) or OpenTelemetry's instrumentation does.
- It gets the attribute `graphql.operation.name=GetUsersQuery`.

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
- The request's server span is named `graphql.operation.GetUsersQuery`
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
| Operation named in the query but not in `operationName` | The name is read after parsing, so it's still found |
| Missing/unknown operation name | Falls back to `AnonymousOperation` |
| Query fails to parse or validate | The operation doesn't execute, so no name is added |
| OpenTelemetry package not present | Log enrichment still works; trace rename is skipped |
| Existing `user_context` already set | Existing keys are preserved and `graphql.operation_name` is merged |
| `user_context["graphql"]` is non-dict | Extension does not overwrite it; use a dict shape for GraphQL context |
