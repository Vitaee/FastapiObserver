# Next Steps (v0.2.0)

This file tracks implementation priorities after `v0.1.0`.

## 1) OTel Enhancements

- Add log correlation tests that assert trace/span IDs in emitted logs under instrumented requests.
- Add integration test fixture for OTLP export to a local collector mock.
- Support configurable resource attributes beyond service metadata.

## 2) Security Hardening

- Add optional allowlist-only logging mode for headers and event keys.
- Add redaction policy presets (`strict`, `pci`, `gdpr`) with contract tests.
- Add request body media-type allowlist before body capture.

## 3) Dynamic Runtime Controls

- Add optional audit trail sink for control-plane changes.
- Add runtime toggle for per-path sampling profiles.
- Add auth extensibility interface for non-token control-plane auth.

## 4) Performance and Reliability

- Add middleware overhead benchmark with regression threshold in CI.
- Add high-concurrency tests for context isolation.
- Add graceful fallback metrics path if Prometheus endpoint cannot mount.

## 5) Release Pipeline

- Configure Trusted Publishing on PyPI and TestPyPI environments.
- Add release checklist gating:
  - changelog entry
  - schema compatibility check
  - test matrix green
  - SBOM generated
- Publish `0.2.0.dev*` pre-releases to TestPyPI before stable `0.2.0`.
