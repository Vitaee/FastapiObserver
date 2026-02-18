# Changelog

All notable changes to this project will be documented in this file.

## [0.2.0.dev0] - Unreleased

### Added
- Explicit log schema version (`log_schema_version`) and package version metadata in every JSON log.
- Public package version export (`observabilityfastapi.__version__`).
- Environment-based constructors:
  - `SecurityPolicy.from_env()`
  - `TrustedProxyPolicy.from_env()`
  - `OTelSettings.from_env()`
  - `RuntimeControlSettings.from_env()`
- Validation/normalization for settings and policy inputs:
  - log level validation
  - header/path normalization
  - OTel protocol and sampling validation
  - security redaction mode and body length checks
- New tests for configuration and environment loading.

### Changed
- Project version bumped to `0.2.0.dev0` for next development cycle.

## [0.1.0] - 2026-02-18

### Added
- Secure-by-default structured logging middleware for FastAPI.
- Request ID propagation with trust-boundary controls.
- Optional Prometheus metrics backend and `/metrics` endpoint mounting.
- Optional OpenTelemetry install helpers and dynamic trace sampling controls.
- Runtime control plane with token authentication.
- Plugin hooks for custom log enrichers and metric hooks.

### Breaking Changes
- None.
