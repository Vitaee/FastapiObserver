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
- Trace sampling ratio, when fastapi-observer set up tracing. With tracing off,
  or with a tracer provider your app configured itself, its sampler decides:
  a `trace_sampling_ratio` change is refused with `409`, and `GET` returns
  `null` for it.

**Changes apply to one process.** The response includes `process_id`, the
process that served the request. With several workers (`gunicorn -w 4`,
`uvicorn --workers 4`), each worker has its own settings, and a request reaches
one of them. To change every worker, set the environment variable and restart.

**Every change is logged.** A `control_plane.settings_changed` warning records
what was requested, the settings it replaced, the caller's IP address and the
process ID. It's written before the change, so raising the log level can't drop it.

Who can call it: the caller's address must be in `OBS_TRUSTED_CIDRS` (private
networks and loopback by default), checked before the token, so callers from
outside can't guess it. `kubectl port-forward` and in-cluster requests work as
they are. A request that passed through a proxy, such as your ingress, carries
`X-Forwarded-For` and is refused, because the real caller is unknown. To call it
through an internal proxy, set `OBS_HONOR_FORWARDED_HEADERS=true` and limit
`OBS_TRUSTED_CIDRS` to your proxies and operators.

In Kubernetes, the default private networks include every pod in the cluster,
so the token is what keeps other workloads out. Keep it secret, and narrow
`OBS_TRUSTED_CIDRS` to your operators' addresses if you can.

---

