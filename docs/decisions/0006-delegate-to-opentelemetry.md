# 0006: Hand work to OpenTelemetry and `prometheus_client`

**Status:** accepted, in 1.7.0 · **Reviewed:** 2026-10-31

## Context

By 1.6.0, fastapi-observer had its own versions of the exporter builders, provider checks, `/metrics` content negotiation and shutdown hooks that the OpenTelemetry SDK and `prometheus_client` already provide. Some existed in three copies, and one copy looked for a provider class named `ProxyMeterProvider`. OpenTelemetry's class is `_ProxyMeterProvider`, so OTLP metrics were never exported.

## Decision

Use the SDK and `prometheus_client` for everything they already do ([PR #13](https://github.com/Vitaee/FastapiObserver/pull/13)). fastapi-observer keeps what is its own: the one-call setup, request ID and trace correlation in JSON logs, redaction, and the trust boundary. The public API stays the same.

## Evidence

| | 1.6.0 | 1.7.0 |
|---|---:|---:|
| Code lines (package) | 5,877 | 5,267 (−10%) |
| `otel/` | 1,076 | 767 (−29%) |
| `metrics/` | 672 lines, 13 files | 387 lines, 2 files (−42%) |
| Public API | 77 names | 77 names |

The [1.7.0 changelog](https://github.com/Vitaee/FastapiObserver/blob/main/CHANGELOG.md#170---2026-10-31) lists the bugs fixed in the same release.

## What it cost

- **Upstream changes reach users directly.** That's the risk this decision accepts. The [1.5.0 leak](../postmortems/2026-10-otlp-logs-exported-unredacted.md), which came earlier, shows what one looks like.
- **Some internals are still involved:**
    - the logs SDK, which lives in a private module, `opentelemetry.sdk._logs` ([`otel/logs.py:90`](https://github.com/Vitaee/FastapiObserver/blob/v1.8.0/src/fastapiobserver/otel/logs.py#L90));
    - a check on provider class names ([`resource.py:104`](https://github.com/Vitaee/FastapiObserver/blob/v1.8.0/src/fastapiobserver/otel/resource.py#L104)).
- **Guards:**
    - the root-handler test from [decision 0004](0004-redact-before-anything-leaves.md);
    - since 1.8.0, a nightly CI run against the newest OpenTelemetry and `prometheus_client` releases, pre-releases included.
