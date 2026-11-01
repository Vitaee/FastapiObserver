# 0012: No sinks from installed packages

**Status:** accepted · **Reviewed:** 2026-11-01 · Supersedes the row of [0007](0007-new-destinations-are-opt-in.md) on sinks from installed packages. Its startup warning is now `observability.settings_retired` ([0013](0013-every-setting-has-one-name.md))

## Context

Since 1.8.0, a package could provide a log sink through the `fastapiobserver.log_sinks` entry-point group, loaded at startup when its name was in `OBS_LOG_SINKS` ([0007](0007-new-destinations-are-opt-in.md)). No package is known to provide one. The loader was code to maintain and test for a use nobody had, and `register_sink()` already covers custom sinks.

## Decision

- The entry-point loader and the `OBS_LOG_SINKS` setting are removed.
- A custom sink is registered in code with `register_sink()`, before `install_observability()`. It goes through the same queue, redaction and circuit breaker as the built-in sinks.
- If `OBS_LOG_SINKS` is still set, startup logs `observability.log_sinks_removed`, so a sink that stopped receiving logs doesn't go unnoticed.

## Evidence

- **Usage:** no code in this repository, its examples or its docs used an entry-point sink outside the loader's own tests.
- **Tests:** `test_obs_log_sinks_is_reported_as_no_longer_read` in [`test_sinks.py`](https://github.com/Vitaee/FastapiObserver/blob/main/tests/test_sinks.py), and the existing registry tests for `register_sink()`.

## What it cost

- **A package that provided a sink needs one line in the app**, `register_sink(TheirSink())`, instead of an environment variable.
- **It removes public names:** `fastapiobserver.sinks.discover_entry_point_sinks` and `ObservabilitySettings.log_sinks`.
