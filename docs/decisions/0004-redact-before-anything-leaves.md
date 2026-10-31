# 0004: Redact before anything leaves the process

**Status:** accepted · **Reviewed:** 2026-10-31

## Context

Log records carry request data. Passwords, tokens, cookies and API keys end up in `extra` fields and headers. Once a value has been written to a file or exported to a backend, it can't be taken back.

## Decision

- **Redaction runs in fastapi-observer's own output path,** before anything is written or exported:
    - the JSON formatter covers stdout, files and sinks;
    - a sanitizing wrapper covers the OTLP log handler ([`otel/logs.py:26`](https://github.com/Vitaee/FastapiObserver/blob/v1.8.0/src/fastapiobserver/otel/logs.py#L26)).
- **Presets choose what is masked:** `strict`, `pci` and `gdpr`.
- **Never attach a handler to the root logger other than fastapi-observer's queue handler.** Anything else on the root logger receives records before redaction.

## Evidence

- **The leak this rule prevents.** [Postmortem: OTLP logs exported without redaction](../postmortems/2026-10-otlp-logs-exported-unredacted.md). From March to October 2026, an OpenTelemetry handler on the root logger exported every record a second time without redaction.
- **The guard test.** [`test_otlp_logs_are_redacted_without_deprecated_or_root_handler`](https://github.com/Vitaee/FastapiObserver/blob/v1.8.0/tests/test_otel_logs_pipeline.py#L104) fails if another handler sits on the root logger, and checks that an exported `password` is `***`.
- **Deep nesting is masked too.** Since 1.4.0, containers nested deeper than the depth limit are masked instead of passed through ([`redaction.py:41`](https://github.com/Vitaee/FastapiObserver/blob/v1.8.0/src/fastapiobserver/security/redaction.py#L41)).

## What it cost

- **Other root-logger handlers aren't covered.** Redaction belongs to fastapi-observer's handlers, not to the records themselves. So a handler someone else puts on the root logger still sees unredacted data, whether it's Sentry's logging integration or one your app adds. The [Sentry notes](../sentry.md) document this. It isn't solved.
- **Matching is by field name.** A secret stored under a key that isn't on the list isn't masked.
- **CPU on every record.** Nested data is walked. Since 1.5.0, the key sets are built once per policy instead of once per record.
