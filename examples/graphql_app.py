"""
graphql_app.py — Demonstrates Native Strawberry GraphQL Observability.

Prerequisites:
    pip install strawberry-graphql

Run this:
    uvicorn examples.graphql_app:app --reload

Then try running a query in the GraphiQL interface at:
    http://localhost:8000/graphql

Or perform a curl request:
    curl -X POST http://localhost:8000/graphql \\
         -H 'Content-Type: application/json' \\
         -d '{"query": "query GetHello { hello }", "operationName": "GetHello"}'

What happens under the hood:
  1. The `StrawberryObservabilityExtension` is injected via Duck Typing.
  2. For every `POST /graphql` request, it intercepts Strawberry's execution context.
  3. It extracts the `operationName` (e.g., GetHello).
  4. It injects the `operation_name` into the `user_context["graphql"]` structured log dictionary.
  5. If OpenTelemetry is enabled, it dynamically renames the opaque HTTP span
     from `POST /graphql` to `graphql.operation.GetHello` for highly-granular trace views.
"""

from fastapi import FastAPI
import strawberry
from strawberry.fastapi import GraphQLRouter

from fastapiobserver import (
    ObservabilitySettings,
    install_observability,
)
from fastapiobserver.integrations.strawberry import StrawberryObservabilityExtension

app = FastAPI(title="Strawberry GraphQL Example")

settings = ObservabilitySettings(
    app_name="graphql-api",
    service="graphql-svc",
    environment="development",
)

# --- 1. Install observability ---
install_observability(app, settings)


# --- 2. Define your Strawberry Schema ---
@strawberry.type
class Query:
    @strawberry.field
    def hello(self) -> str:
        return "Hello World! Check your logs to see the operation name."


schema = strawberry.Schema(
    query=Query,
    extensions=[StrawberryObservabilityExtension],  # Inject the Zero-Dependency Extension!
)

graphql_app = GraphQLRouter(schema)
app.include_router(graphql_app, prefix="/graphql")
