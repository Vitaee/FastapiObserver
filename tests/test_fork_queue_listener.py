import logging
import queue
import threading
from unittest.mock import Mock
from fastapiobserver.logging.setup import _restart_queue_listener_in_child
import fastapiobserver.logging.state as state
from fastapiobserver.logging.circuit_breaker import SinkCircuitBreakerHandler
from fastapiobserver.logging.queueing import _LOG_QUEUE_TELEMETRY, OverflowPolicyQueueHandler


def test_restart_in_child_gives_managed_handlers_a_new_empty_queue(monkeypatch):
    old_queue = queue.Queue(maxsize=10)
    old_queue.put_nowait(logging.makeLogRecord({"msg": "logged before the fork"}))
    handler = Mock(spec=OverflowPolicyQueueHandler, queue=old_queue)
    monkeypatch.setattr(state, "_MANAGED_HANDLERS", {handler})
    monkeypatch.setattr(state, "_QUEUE_LISTENER", Mock(
        queue=old_queue, handlers=[], respect_handler_level=True,
    ))

    _restart_queue_listener_in_child()
    listener = state._QUEUE_LISTENER
    try:
        assert isinstance(listener, logging.handlers.QueueListener)
        assert handler.queue is listener.queue is not old_queue
        assert listener.queue.empty()  # pre-fork records are dropped, not replayed
    finally:
        listener.stop()


def test_restart_in_child_ignores_locks_held_at_fork():
    # In the parent, the listener or a request thread can hold these locks at the
    # moment of the fork. Nothing in the child releases them.
    old_queue = queue.Queue(maxsize=10)
    breaker = SinkCircuitBreakerHandler(
        sink_name="stdout",
        delegate=logging.NullHandler(),
        failure_threshold=1,
        recovery_timeout_seconds=1,
    )
    held = [old_queue.mutex, _LOG_QUEUE_TELEMETRY._lock, breaker._lock]
    listener = Mock(queue=old_queue, handlers=[], respect_handler_level=True)
    saved = (state._QUEUE_LISTENER, state._SINK_CIRCUIT_BREAKERS, _LOG_QUEUE_TELEMETRY._lock)
    state._QUEUE_LISTENER = listener
    state._SINK_CIRCUIT_BREAKERS = [breaker]
    for lock in held:
        lock.acquire()
    try:
        child = threading.Thread(target=_restart_queue_listener_in_child, daemon=True)
        child.start()
        child.join(timeout=2)

        assert not child.is_alive()
        assert not _LOG_QUEUE_TELEMETRY._lock.locked()
        assert not breaker._lock.locked()
    finally:
        for lock in held:
            lock.release()
        if isinstance(state._QUEUE_LISTENER, logging.handlers.QueueListener):
            state._QUEUE_LISTENER.stop()
        state._QUEUE_LISTENER, state._SINK_CIRCUIT_BREAKERS, _LOG_QUEUE_TELEMETRY._lock = saved
