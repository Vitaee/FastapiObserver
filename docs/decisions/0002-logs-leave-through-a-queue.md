# 0002: Logs leave through a bounded queue, and overflow drops the oldest lines

**Status:** accepted · **Reviewed:** 2026-10-31

## Context

Python's `logging` is synchronous: a handler's `emit()` runs on the caller's thread, which in an async app is the event loop. A slow destination, such as a full pipe, a busy disk or a network sink, therefore stalls every request the process is serving.

## Decision

- Records go through a bounded queue (`LOG_QUEUE_MAX_SIZE`, default 10,000) to a listener thread. That thread formats the JSON, redacts, and writes to every sink.
- Only cheap work stays on the caller's thread: record creation, filters, and capturing request context ([`queueing.py:139`](https://github.com/Vitaee/FastapiObserver/blob/v1.8.0/src/fastapiobserver/logging/queueing.py#L139)).
- When the queue is full, the default `LOG_QUEUE_OVERFLOW_POLICY` is `drop_oldest`. `drop_newest`, and `block` with a timeout, are the alternatives.
- Drops are counted and exported as `fastapiobserver_log_queue_dropped_total{reason}` ([`prometheus.py:117`](https://github.com/Vitaee/FastapiObserver/blob/v1.8.0/src/fastapiobserver/metrics/prometheus.py#L117)), so you can alert on them. The [log queue reference](../advanced.md#bounded-queue-and-overflow-policy) lists every setting and metric.

`drop_oldest` is the default because blocking turns a logging problem into an outage, and during an overload the newest lines describe what is happening now.

## Evidence

A handler that takes 1 ms per line, measured with [`examples/benchmarks/slow_sink.py`](https://github.com/Vitaee/FastapiObserver/blob/main/examples/benchmarks/slow_sink.py) over 500 log calls:

| Where the slow handler runs | Cost to the caller |
|---|---:|
| On the caller's thread | 1,292 to 1,536 µs per call |
| Behind fastapi-observer's queue | 18 to 20 µs per call |

Nothing was dropped.

## What it cost

- **Logs can be lost under overload, by design.** The counter shows when this happens.
- **The listener thread can't see request context.** 1.5.0 fixed three bugs caused by this:
    - `user_context` was read on the listener thread, where it is empty;
    - the standard library's `QueueHandler.prepare()` dropped `exc_info`;
    - timestamps were stamped at formatting time.

    Context is now captured on the caller's thread before the record is queued.
- **A killed process loses its queued lines.** On a normal shutdown the queue drains first. Since 1.7.0, that happens before the OTLP providers shut down.
