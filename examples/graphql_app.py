"""Put GraphQL operation names on request logs and the server span.

Requires strawberry-graphql. Run: uvicorn examples.graphql_app:app --reload
POST /graphql with {"query":"query GetHello { hello }"}.
Enable OBS_OTEL_ENABLED=true with a collector to see the renamed server span.
"""

import strawberry
from fastapi import FastAPI
from strawberry.fastapi import GraphQLRouter

from fastapiobserver import install_observability
from fastapiobserver.integrations.strawberry import StrawberryObservabilityExtension


@strawberry.type
class Query:
    @strawberry.field
    def hello(self) -> str:
        return "Hello World! Check your logs to see the operation name."


schema = strawberry.Schema(query=Query, extensions=[StrawberryObservabilityExtension])
app = FastAPI(title="Strawberry GraphQL Example")
install_observability(app)
app.include_router(GraphQLRouter(schema), prefix="/graphql")
