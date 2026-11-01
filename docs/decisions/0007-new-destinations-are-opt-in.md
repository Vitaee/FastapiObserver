# 0007: New destinations and controls are opt-in

**Status:** accepted · **Reviewed:** 2026-10-31 · Its row on sinks from installed packages is superseded by [0012](0012-no-sinks-from-installed-packages.md), which removed them

## Context

Before 1.8.0, two parts of the library widened where data went, or who could change behaviour, without anyone configuring them:

- any installed package that declared a `fastapiobserver.log_sinks` entry point was imported at startup and received every log line;
- the runtime control plane accepted any caller holding the token, including callers coming through a public ingress.

## Decision

Each new destination or control needs explicit configuration:

| Destination or control | Off until |
|---|---|
| Log sinks from installed packages | Named in `OBS_LOG_SINKS` ([`discovery.py:21`](https://github.com/Vitaee/FastapiObserver/blob/v1.8.0/src/fastapiobserver/sinks/discovery.py#L21)). Unlisted packages are never imported. |
| Runtime control plane | `OBS_RUNTIME_CONTROL_ENABLED=true`, and even then only for callers in `OBS_TRUSTED_CIDRS` holding the token |
| Prometheus `/metrics` | `METRICS_ENABLED=true` |
| OTLP traces, logs and metrics | `OTEL_ENABLED`, `OTEL_LOGS_ENABLED`, `OTEL_METRICS_ENABLED` |
| Request and response bodies in logs | `OBS_LOG_REQUEST_BODY`, `OBS_LOG_RESPONSE_BODY` |

At startup, one `observability.configured` line lists what is on ([`fastapi.py:146`](https://github.com/Vitaee/FastapiObserver/blob/v1.8.0/src/fastapiobserver/fastapi.py#L146)).

## Evidence

- **The two pre-1.8.0 holes were closed in [PR #15](https://github.com/Vitaee/FastapiObserver/pull/15).**
- **Tests:** [`test_entry_point_sinks_load_only_when_enabled`](https://github.com/Vitaee/FastapiObserver/blob/v1.8.0/tests/test_sinks.py#L220) checks that an unlisted entry point is never loaded.

## What it cost

- **1.8.0 broke two things on purpose:**
    - automatic sink discovery, which was never documented;
    - control-plane calls through a public ingress, which now get 403.
