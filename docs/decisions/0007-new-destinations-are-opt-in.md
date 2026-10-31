# 0007: Nothing new receives data or control without being configured

**Status:** accepted · **Reviewed:** 2026-10-31

## Context

An observability library decides where an app's data goes and who can change its behaviour. A default that widens either is easy to miss and hard to undo. A package that quietly starts receiving logs, or an endpoint that quietly becomes reachable, is how data leaks without anyone making a decision.

## Decision

Each new destination or control surface needs explicit configuration:

| Surface | Off until |
|---|---|
| Log sinks from installed packages | Named in `OBS_LOG_SINKS` ([`discovery.py:21`](https://github.com/Vitaee/FastapiObserver/blob/v1.8.0/src/fastapiobserver/sinks/discovery.py#L21)). Unlisted packages are never imported. |
| Runtime control plane | `OBS_RUNTIME_CONTROL_ENABLED=true`, and even then only for callers in `OBS_TRUSTED_CIDRS` holding the token |
| Prometheus `/metrics` | `METRICS_ENABLED=true` |
| OTLP traces, logs and metrics | `OTEL_ENABLED`, `OTEL_LOGS_ENABLED`, `OTEL_METRICS_ENABLED` |
| Request and response bodies in logs | `OBS_LOG_REQUEST_BODY`, `OBS_LOG_RESPONSE_BODY` |

At startup, one `observability.configured` line lists what is on ([`fastapi.py:146`](https://github.com/Vitaee/FastapiObserver/blob/v1.8.0/src/fastapiobserver/fastapi.py#L146)).

## Evidence

- **1.8.0** ([PR #15](https://github.com/Vitaee/FastapiObserver/pull/15)) closed two places where this didn't hold:
    - any installed package that declared a `fastapiobserver.log_sinks` entry point was imported at startup and got every log line;
    - the control plane accepted any caller that had the token.
- **Tests:** [`test_entry_point_sinks_load_only_when_enabled`](https://github.com/Vitaee/FastapiObserver/blob/v1.8.0/tests/test_sinks.py#L220) checks that an unlisted entry point is never loaded.

## What it cost

- **More setup.** Each destination is one more setting to know about.
- **1.8.0 broke two things on purpose:**
    - anyone relying on sinks being discovered automatically, a feature that was never documented;
    - control-plane calls made through a public ingress, which now get 403.
