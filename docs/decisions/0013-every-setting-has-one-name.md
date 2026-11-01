# 0013: Every setting has one name

**Status:** accepted · **Reviewed:** 2026-11-01 · Supersedes the warning named in [0012](0012-no-sinks-from-installed-packages.md). Records before this one use the 1.x variable names.

## Context

In 1.x the library read about forty environment variables under four conventions:

- bare names an app often sets for itself, such as `LOG_LEVEL`, `ENVIRONMENT` and `METRICS_ENABLED`;
- `OBS_*`;
- names it made up inside OpenTelemetry's namespace, such as `OTEL_ENABLED`, `OTEL_LOGS_ENDPOINT` and `OTEL_TRACE_SAMPLING_RATIO`;
- the specification's own, such as `OTEL_EXPORTER_OTLP_ENDPOINT`, though only for traces. Log and metric export ignored `OTEL_EXPORTER_OTLP_PROTOCOL`.

The settings models also matched variables named like their fields. `ENABLED=true` turned on the runtime control plane and OTLP export, and the control plane's path was read from `PATH`.

## Decision

- Every variable the library owns starts with `OBS_`. A 1.x name becomes `OBS_` plus that name.
- For OpenTelemetry it reads the specification's variables: `OTEL_SDK_DISABLED`, `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES`, `OTEL_TRACES_SAMPLER_ARG`, `OTEL_METRIC_EXPORT_INTERVAL`, and `OTEL_EXPORTER_OTLP_PROTOCOL` with its per-signal forms. It doesn't read endpoints: OpenTelemetry's exporters read `OTEL_EXPORTER_OTLP_*ENDPOINT` themselves ([0006](0006-delegate-to-opentelemetry.md)).
- A setting is read only from its documented name, never from its field name. `ObservabilitySettings`, the one model built from field names in code, adds `OBS_` to them for the environment.
- At startup, `observability.settings_retired` lists the 1.x variables still set, each with its replacement. Setting the replacement stops it, so an app that sets `LOG_LEVEL` for itself isn't warned forever.

## Evidence

- **Tests:** in [`test_config_settings.py`](https://github.com/Vitaee/FastapiObserver/blob/main/tests/test_config_settings.py), `test_settings_ignore_variables_named_like_their_fields` and `test_otel_service_name_names_the_service_everywhere`. The per-signal protocol fallback and `OTEL_SDK_DISABLED` are in [`test_otel_logs_from_env.py`](https://github.com/Vitaee/FastapiObserver/blob/main/tests/test_otel_logs_from_env.py) and [`test_otel_metrics_from_env.py`](https://github.com/Vitaee/FastapiObserver/blob/main/tests/test_otel_metrics_from_env.py), and the warning in `test_retired_settings_are_reported_with_their_replacements` in [`test_sinks.py`](https://github.com/Vitaee/FastapiObserver/blob/main/tests/test_sinks.py).
- **The leak:** before this change, `_RuntimeControlEnvSettings().path` returned the process's `PATH`.

## What it cost

- **Every deployment renames its variables.** The [upgrade table](../configuration.md#upgrading-from-1x) lists each one, and the startup warning names any it missed.
- **`OTEL_TRACES_SAMPLER` isn't read.** Sampling stays parent-based by trace ID, so the runtime control plane can change the ratio. Only `OTEL_TRACES_SAMPLER_ARG` is honoured.
- **`ObservabilitySettings` gains a few undocumented names** from the prefix, such as `OBS_SERVICE` for `service`, where a field's name differs from its variable's.
