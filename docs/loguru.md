# Loguru Coexistence Guide

This guide shows how to keep existing `loguru` call sites while routing logs through `fastapi-observer` for:
- request/trace correlation
- redaction/sanitization
- bounded queue handling
- sink circuit-breaker protection

## Install

```bash
pip install "fastapi-observer[loguru]"
```

## Recommended Architecture

Use one logging pipeline:

`loguru` calls -> bridge -> stdlib root logger -> `fastapi-observer` queue/sinks

This avoids duplicated sink config and keeps security policy enforcement in one place.

## Minimal Setup

```python
from fastapi import FastAPI
from loguru import logger as loguru_logger

from fastapiobserver import (
    ObservabilitySettings,
    install_loguru_bridge,
    install_observability,
)

app = FastAPI()

settings = ObservabilitySettings(
    app_name="orders-api",
    service="orders",
    environment="production",
)
install_observability(app, settings)

# Forward all Loguru records into fastapi-observer's stdlib pipeline.
bridge_id = install_loguru_bridge()

loguru_logger.bind(event={"component": "checkout"}).info("payment.started")
```

## Structured Context

`loguru` bound fields are forwarded as stdlib `extra` fields.  
If you bind an `event` dict, it is preserved by `StructuredJsonFormatter`.

```python
loguru_logger.bind(
    request_id="req-123",
    event={"order_id": "ord-42", "phase": "validate"},
).info("order.validation")
```

## Removing the Bridge

```python
from fastapiobserver import remove_loguru_bridge

remove_loguru_bridge(bridge_id)
```

## Loop-Safety Rule

Do not install both directions at the same time:
- Loguru -> stdlib bridge (this guide)
- stdlib -> Loguru intercept

Running both in one process can create recursive log loops.

## Migration Playbook (Low Risk)

1. Keep existing `loguru` calls and add `install_loguru_bridge()`.
2. Verify logs in staging (fields, redaction, request IDs, trace IDs).
3. Migrate module-by-module to stdlib logging only if you want to simplify dependencies.
4. Remove bridge once all call sites are stdlib.
