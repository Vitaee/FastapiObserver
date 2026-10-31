# Postmortem: OTLP logs exported without redaction

**Fixed in:** 1.5.0, released 2026-10-31 · **Severity:** high. Values the redaction policy should have masked, such as passwords and tokens, reached the OTLP log backend in clear text.

## Summary

From 2026-03-04 until 1.5.0, apps with both tracing and OTLP log export turned on exported every log record twice. One copy went through fastapi-observer's redaction. The other was exported as it was logged.

Nothing in fastapi-observer's code changed on 2026-03-04. An OpenTelemetry release changed what an existing call did, and installs picked that release up through a version range.

## Who was affected

All of these had to be true:

- fastapi-observer 0.2.1 to 1.4.1;
- tracing on (`OTEL_ENABLED=true`) and OTLP log export on (`OTEL_LOGS_ENABLED=true` with `OTEL_LOGS_MODE` set to `otlp` or `both`);
- `opentelemetry-instrumentation-logging` 0.61b0 or newer installed. fastapi-observer 1.3.x allowed it (`>=0.60b1`); 1.4.0 and 1.4.1 required it (`>=0.66b1`).

The unredacted copy covered every record that reached the root logger, from any logger in the process, with its `extra` attributes. JSON logs on stdout, Prometheus metrics and traces were not affected.

## What happened

When tracing was on, fastapi-observer called `LoggingInstrumentor().instrument(set_logging_format=False)` to put trace IDs on log records. The call was in the first commit ([`3e3770b`](https://github.com/Vitaee/FastapiObserver/commit/3e3770b), 2026-02-18). Up to `opentelemetry-instrumentation-logging` 0.60b1, `instrument()` only patched the log record factory, so the call was harmless.

From 0.61b0, `instrument()` also attaches an OpenTelemetry `LoggingHandler` to the root logger by default, unless `OTEL_PYTHON_LOG_AUTO_INSTRUMENTATION=false`. That handler receives each record before fastapi-observer's queue and redaction, and exports it through the global logger provider. That's the same provider fastapi-observer configures for its own, redacted OTLP export, so each record went out twice.

We checked this against the published wheels: 0.57b0 to 0.60b1 attach no handler, and every release from 0.61b0 does.

## Timeline (UTC)

| When | What |
|---|---|
| 2026-02-18 | The `LoggingInstrumentor().instrument()` call is added ([`3e3770b`](https://github.com/Vitaee/FastapiObserver/commit/3e3770b)). |
| 2026-02-19 | 0.2.1 ships OTLP log export. Upstream still attaches no handler. |
| 2026-03-01 to 2026-10-29 | No commits to the repository. |
| 2026-03-04 | `opentelemetry-instrumentation-logging` 0.61b0 is released and attaches a root handler by default. **Exposure starts.** |
| 2026-10-30 21:33 | fastapi-observer 1.4.0 raises the minimum to 0.66b1, so every new `[otel]` install now gets the root handler. |
| 2026-10-30 22:03 | **Found** during a review of which modules could be handed to OpenTelemetry. Logging `{"password": "hunter2"}` with OTLP logs on exported it twice, once unmasked. |
| 2026-10-30 22:18 | Confirmed in the upstream source: `_setup_logging_handler()` adds the handler to the root logger. |
| 2026-10-31 08:35 | Fix committed ([`ec08005`](https://github.com/Vitaee/FastapiObserver/commit/ec08005)). |
| 2026-10-31 08:57 | **1.5.0 released.** |

About 240 days of exposure, and 11 hours from discovery to release.

## Why it wasn't caught

- **The tests checked the wrong thing.** They checked that fastapi-observer's OTLP handler redacts, and it did. No test checked that our handler was the only way records reached the exporter.
- **CI only runs on pushes.** Nothing was pushed for eight months, so the upstream change never met the test suite.
- **We called a helper for one side effect**, trace IDs on records, and relied on that being all it did. Nothing checked whether a new upstream release made it do more.

## The fix

[`ec08005`](https://github.com/Vitaee/FastapiObserver/commit/ec08005) removes the call. fastapi-observer's own `TraceContextFilter` already put trace and span IDs on records, so nothing was lost.

## What stops it happening again

- **A rule and a test.** [Decision 0004](../decisions/0004-redact-before-anything-leaves.md): no handler other than fastapi-observer's queue handler on the root logger. Its guard test fails if one appears.
- **A nightly CI run against the newest upstream releases**, added in 1.8.0 ([decision 0006](../decisions/0006-delegate-to-opentelemetry.md)). An upstream change now meets the tests within a day, pushes or no pushes.
- **Visible security fixes.** This fix went out inside a commit titled `perf: cut per-request overhead and fix queued log records`, and its changelog entry named the wrong upstream version (0.66 instead of 0.61). Security fixes now ship as their own commits, under a `Security` heading in the changelog. The 1.5.0 entry has been corrected.

## If you ran an affected setup

1. Upgrade to 1.5.0 or later.
2. Treat anything your redaction policy was meant to mask as exposed to your OTLP backend and to anyone who can read it. Rotate those secrets, and check how long your log backend keeps data.
3. If you can't upgrade yet, set `OTEL_PYTHON_LOG_AUTO_INSTRUMENTATION=false`. That stops the extra handler.

## Not fixed

Other handlers on the root logger, such as Sentry's logging integration, still see records before redaction. See [decision 0004](../decisions/0004-redact-before-anything-leaves.md).
