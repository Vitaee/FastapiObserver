# Runtime Control Plane (No Restart)

Use runtime controls when you need higher log verbosity or different trace sampling during an incident.

```bash
export OBSERVABILITY_CONTROL_TOKEN="replace-me"
```

```python
from fastapiobserver import RuntimeControlSettings, install_observability

runtime_control = RuntimeControlSettings(enabled=True)
install_observability(app, settings, runtime_control_settings=runtime_control)
```

Inspect current runtime values:

```bash
curl -X GET http://localhost:8000/_observability/control \
  -H "Authorization: Bearer replace-me"
```

Update runtime values:

```bash
curl -X POST http://localhost:8000/_observability/control \
  -H "Authorization: Bearer replace-me" \
  -H "Content-Type: application/json" \
  -d '{"log_level":"DEBUG","trace_sampling_ratio":0.25}'
```

What changes immediately:
- Root logger level (and uvicorn loggers)
- Dynamic OTel trace sampling ratio

---

