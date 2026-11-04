# 0015: Request diagnostics can use application-owned logging

**Status:** accepted · **Reviewed:** 2026-11-04 · Narrows the ownership rules in [0004](0004-redact-before-anything-leaves.md) and [0010](0010-trace-with-fastapis-own-opentelemetry.md)

## Context

An established application can already have JSON logging, approved redaction and
OpenTelemetry export. Adding our root handler delivers each record to another
pipeline; replacing the application's handler can remove its sanitizer. Request
diagnostics should be usable without taking ownership of logging.

## Decision

- Add one installer option, `configure_logging=False`. Leave existing handlers,
  filters, formatters, levels, Uvicorn logging and log export in place. Keep the
  diagnostics' lifespan, metrics and optional tracing available.
- Capture correlation on the library's diagnostic logger, before existing
  filters and application queues. Reuse the existing context filters so custom
  record factories can define the same fields without `extra` collisions.
- Do not install an OTel logging handler or exporting log processor in this
  mode. The application owns redaction and logging lifecycle. Runtime log-level
  changes are refused, as sampling changes already are with an external sampler.
- Keep field-name masking and exception URL-credential masking precise in the
  documentation. Reuse approved organisation sanitizers; add no new detector.

## Evidence

- [Configuration, queue and provider tests](https://github.com/Vitaee/FastapiObserver/blob/main/tests/test_application_logging.py) check preserved configuration, one event per destination, queued correlation, record-factory compatibility and existing OTel providers across repeated lifespans.
- [Real-server example test](https://github.com/Vitaee/FastapiObserver/blob/main/tests/test_existing_logging_example.py) checks concurrent requests, application events, failure redaction and retained Uvicorn access logs.

## What it cost

- The application's formatter must include `event` and correlation attributes.
  Its levels, sampling and routing decide what gets written.
- Every application output/export path must retain its sanitizer. Direct native
  FastAPI OTel error logs bypass stdlib filters; the example application disables
  that path explicitly. The installer leaves native logging configuration alone.
- The default remains library-owned logging; the option does not reconfigure a
  pipeline another app already installed in the same process.
