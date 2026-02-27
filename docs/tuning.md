# Low-Overhead & Production Tuning (Advanced)

`fastapi-observer` integrates natively with the core OpenTelemetry Python SDK, meaning you can aggressively tune its resource usage purely via standard environment variables without altering your application code.

For high-throughput services (e.g. `10k+ RPS`), apply these exact variables to minimize the observer footprint:

### 1. Head-Based Sampling

Tracing 100% of requests is too expensive at scale. You should configure `fastapi-observer` to respect upstream trace flags, while only sampling a fraction of net-new requests:

```bash
# Keep the parent's sample decision if it exists, otherwise sample 5%
export OTEL_TRACES_SAMPLER="parentbased_traceidratio"
export OTEL_TRACES_SAMPLER_ARG="0.05"
```

### 2. Exclude Noisy URLs from the SDK

Do not waste cycles generating spans for health checks or static assets. `fastapi-observer` will auto-derive metrics exclusions, but you can explicitly drop them from tracing at the C-extension level:

```bash
export OTEL_PYTHON_FASTAPI_EXCLUDED_URLS="healthz,metrics,favicon.ico"
```

### 3. Cap Span Attributes

Prevent large, unmanageable spans from consuming excessive memory in the `BatchSpanProcessor`:

```bash
export OTEL_SPAN_ATTRIBUTE_COUNT_LIMIT="128"
export OTEL_SPAN_EVENT_COUNT_LIMIT="128"
export OTEL_SPAN_LINK_COUNT_LIMIT="128"
```

### 4. Optimize Output Buffers

The default OpenTelemetry batch limits are too conservative for high-throughput ASGI microservices. Increase the max queue limits so spikes aren't dropped, but decrease the timeout so the process memory is flushed faster:

```bash
export OTEL_BSP_MAX_QUEUE_SIZE="10000"
export OTEL_BSP_MAX_EXPORT_BATCH_SIZE="5000"
export OTEL_BSP_SCHEDULE_DELAY="1000"
```

---

