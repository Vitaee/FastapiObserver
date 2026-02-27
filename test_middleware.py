from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware

app = FastAPI()
FastAPIInstrumentor.instrument_app(app)

# We must build the middleware stack first!
app.build_middleware_stack()

current = app.middleware_stack
found = False
while hasattr(current, "app"):
    if isinstance(current, OpenTelemetryMiddleware):
        print("Found OTel Middleware!")
        found = True
        break
    current = current.app

if not found:
    print("Not found.")
