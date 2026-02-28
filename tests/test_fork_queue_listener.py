import logging
import queue
from unittest.mock import Mock
from fastapiobserver.logging.setup import _restart_queue_listener_in_child
import fastapiobserver.logging.state as state
from fastapiobserver.logging.queueing import OverflowPolicyQueueHandler


def test_restart_queue_listener_in_child_drains_and_recreates_queue():
    # Setup mock state
    old_queue = queue.Queue(maxsize=10)
    old_queue.put_nowait(
        logging.LogRecord("test", logging.INFO, "path", 1, "test msg 1", None, None)
    )
    old_queue.put_nowait(
        logging.LogRecord("test", logging.INFO, "path", 1, "test msg 2", None, None)
    )

    mock_queue_handler = Mock(spec=OverflowPolicyQueueHandler)
    mock_queue_handler.queue = old_queue

    mock_listener = Mock()
    mock_listener.queue = old_queue
    mock_listener.handlers = []
    mock_listener.respect_handler_level = True

    # Temporarily override state module
    original_listener = state._QUEUE_LISTENER
    original_handlers = state._MANAGED_HANDLERS
    
    state._QUEUE_LISTENER = mock_listener
    state._MANAGED_HANDLERS = {mock_queue_handler}

    try:
        # Action
        _restart_queue_listener_in_child()

        # Assertions
        assert old_queue.empty()
        
        # Verify the new listener was created and started
        new_listener = state._QUEUE_LISTENER
        assert new_listener is not mock_listener
        assert new_listener.queue is not old_queue
        assert isinstance(new_listener, logging.handlers.QueueListener)
        assert new_listener.queue.qsize() == 0  # Pre-fork records MUST be dropped, not replayed

        # Verify the managed handler got the new queue assigned to its .queue attribute
        assert mock_queue_handler.queue is new_listener.queue

        # The actual QueueListener thread isn't started in mock but the object is created
        # new_listener.start() was called during _restart_queue_listener_in_child
        
    finally:
        # Cleanup threading if start started a thread, and restore state
        if isinstance(state._QUEUE_LISTENER, logging.handlers.QueueListener):
            state._QUEUE_LISTENER.stop()
            
        state._QUEUE_LISTENER = original_listener
        state._MANAGED_HANDLERS = original_handlers
