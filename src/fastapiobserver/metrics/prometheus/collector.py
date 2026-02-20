from __future__ import annotations

import threading
from typing import Any

from .client import _import_prometheus_client
from .multiprocess import _is_prometheus_multiprocess_enabled

_LOG_QUEUE_COLLECTOR_LOCK = threading.Lock()
_LOG_QUEUE_COLLECTOR_REGISTERED = False


class _LogQueueMetricsCollector:
    """Custom collector for logging queue pressure counters."""

    def collect(self) -> Any:
        from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

        from ...logging import get_log_queue_stats, get_sink_circuit_breaker_stats
        from ...sinks import get_logtail_dlq_stats

        stats = get_log_queue_stats()

        queue_size = GaugeMetricFamily(
            "fastapiobserver_log_queue_size",
            "Current number of log records waiting in the core queue.",
        )
        queue_size.add_metric([], float(stats["queue_size"]))
        yield queue_size

        queue_capacity = GaugeMetricFamily(
            "fastapiobserver_log_queue_capacity",
            "Configured capacity of the core logging queue.",
        )
        queue_capacity.add_metric([], float(stats["queue_capacity"]))
        yield queue_capacity

        queue_policy = GaugeMetricFamily(
            "fastapiobserver_log_queue_overflow_policy_info",
            "Current overflow policy for the core logging queue.",
            labels=["policy"],
        )
        queue_policy.add_metric([str(stats["overflow_policy"])], 1.0)
        yield queue_policy

        enqueued_total = CounterMetricFamily(
            "fastapiobserver_log_queue_enqueued_total",
            "Total log records accepted into the core queue.",
        )
        enqueued_total.add_metric([], float(stats["enqueued_total"]))
        yield enqueued_total

        dropped_total = CounterMetricFamily(
            "fastapiobserver_log_queue_dropped_total",
            "Total log records dropped due to queue pressure.",
            labels=["reason"],
        )
        dropped_total.add_metric(["drop_oldest"], float(stats["dropped_oldest_total"]))
        dropped_total.add_metric(["drop_newest"], float(stats["dropped_newest_total"]))
        yield dropped_total

        blocked_total = CounterMetricFamily(
            "fastapiobserver_log_queue_blocked_total",
            "Total times producers entered blocking mode while queue was full.",
        )
        blocked_total.add_metric([], float(stats["blocked_total"]))
        yield blocked_total

        block_timeouts_total = CounterMetricFamily(
            "fastapiobserver_log_queue_block_timeouts_total",
            "Total blocking enqueue attempts that timed out and dropped the newest record.",
        )
        block_timeouts_total.add_metric([], float(stats["block_timeout_total"]))
        yield block_timeouts_total

        dlq_stats = get_logtail_dlq_stats()
        dlq_written = CounterMetricFamily(
            "fastapiobserver_dlq_written_total",
            "Total payloads written to local DLQ.",
            labels=["reason"],
        )
        dlq_written.add_metric(["queue_overflow"], float(dlq_stats["written_queue_overflow"]))
        dlq_written.add_metric(["send_failed"], float(dlq_stats["written_send_failed"]))
        yield dlq_written

        dlq_failures = CounterMetricFamily(
            "fastapiobserver_dlq_write_failures_total",
            "Total failures when attempting to write payloads to the DLQ disk.",
        )
        dlq_failures.add_metric([], float(dlq_stats["failures"]))
        yield dlq_failures

        dlq_bytes = CounterMetricFamily(
            "fastapiobserver_dlq_bytes_total",
            "Total bytes written to the DLQ disk.",
        )
        dlq_bytes.add_metric([], float(dlq_stats["bytes"]))
        yield dlq_bytes

        sink_stats = get_sink_circuit_breaker_stats()
        if not sink_stats:
            return

        sink_state = GaugeMetricFamily(
            "fastapiobserver_sink_circuit_breaker_state_info",
            "Current sink circuit-breaker state.",
            labels=["sink", "state"],
        )
        sink_failures = CounterMetricFamily(
            "fastapiobserver_sink_circuit_breaker_failures_total",
            "Total sink handler failures observed by circuit breakers.",
            labels=["sink"],
        )
        sink_skipped = CounterMetricFamily(
            "fastapiobserver_sink_circuit_breaker_skipped_total",
            "Total records skipped while sink circuit breaker was open.",
            labels=["sink"],
        )
        sink_opens = CounterMetricFamily(
            "fastapiobserver_sink_circuit_breaker_opens_total",
            "Total transitions into open state for sink circuit breakers.",
            labels=["sink"],
        )
        sink_half_open = CounterMetricFamily(
            "fastapiobserver_sink_circuit_breaker_half_open_total",
            "Total transitions into half-open state for sink circuit breakers.",
            labels=["sink"],
        )
        sink_closes = CounterMetricFamily(
            "fastapiobserver_sink_circuit_breaker_closes_total",
            "Total transitions back to closed state for sink circuit breakers.",
            labels=["sink"],
        )

        for sink_name, sink in sorted(sink_stats.items()):
            sink_state.add_metric([sink_name, str(sink["state"])], 1.0)
            sink_failures.add_metric([sink_name], float(sink["failures_total"]))
            sink_skipped.add_metric([sink_name], float(sink["skipped_total"]))
            sink_opens.add_metric([sink_name], float(sink["opens_total"]))
            sink_half_open.add_metric([sink_name], float(sink["half_open_total"]))
            sink_closes.add_metric([sink_name], float(sink["closes_total"]))

        yield sink_state
        yield sink_failures
        yield sink_skipped
        yield sink_opens
        yield sink_half_open
        yield sink_closes


def _register_log_queue_metrics_collector() -> None:
    global _LOG_QUEUE_COLLECTOR_REGISTERED

    if _is_prometheus_multiprocess_enabled():
        return

    with _LOG_QUEUE_COLLECTOR_LOCK:
        if _LOG_QUEUE_COLLECTOR_REGISTERED:
            return
        prometheus_client = _import_prometheus_client()
        collector = _LogQueueMetricsCollector()
        try:
            prometheus_client.REGISTRY.register(collector)
        except ValueError:
            # Collector already registered by another backend init in-process.
            pass
        _LOG_QUEUE_COLLECTOR_REGISTERED = True


__all__ = ["_LogQueueMetricsCollector", "_register_log_queue_metrics_collector"]
