import gzip
import json
import logging
import os
import threading
import time
import urllib.error

import pytest

from fastapiobserver.sinks import LogtailDLQ, _LogtailHandler


def test_logtail_dlq_ndjson_writes(tmp_path: pytest.TempPathFactory) -> None:
    dlq_dir = str(tmp_path)
    dlq = LogtailDLQ(
        directory=dlq_dir,
        filename="test.ndjson",
        max_bytes=10000,
        backup_count=1,
        compress=False,
    )
    
    dlq.submit('{"msg":"lost_1"}', reason="queue_overflow")
    dlq.submit('{"msg":"lost_2"}', reason="send_failed")
    dlq.close()
    
    with open(os.path.join(dlq_dir, "test.ndjson")) as f:
        lines = f.readlines()
        
    assert len(lines) == 2
    parsed_1 = json.loads(lines[0])
    parsed_2 = json.loads(lines[1])
    
    assert parsed_1["reason"] == "queue_overflow"
    assert parsed_1["payload"]["msg"] == "lost_1"
    assert "ts" in parsed_1
    
    assert parsed_2["reason"] == "send_failed"
    assert parsed_2["payload"]["msg"] == "lost_2"
    

def test_logtail_dlq_compresses_on_rotation(tmp_path: pytest.TempPathFactory) -> None:
    dlq_dir = str(tmp_path)
    # Extremely small max_bytes to force rotation quickly
    dlq = LogtailDLQ(
        directory=dlq_dir,
        filename="rot.ndjson",
        max_bytes=50,
        backup_count=2,
        compress=True,
    )
    
    # 3 submits, which will blow past 50 bytes and trigger rotation
    dlq.submit('{"msg":"large_payload_1234567890"}', reason="send_failed")
    dlq.submit('{"msg":"large_payload_0987654321"}', reason="send_failed")
    dlq.submit('{"msg":"large_payload_abcdefghij"}', reason="send_failed")
    dlq.close()
    
    files = set(os.listdir(dlq_dir))
    # We expect multiple rotations (rot.ndjson, rot.ndjson.1.gz, rot.ndjson.2.gz)
    assert "rot.ndjson" in files
    assert "rot.ndjson.1.gz" in files
    assert "rot.ndjson.2.gz" in files
    
    # Read both rotated files to find the first payload
    contents = ""
    for suffix in ["rot.ndjson.1.gz", "rot.ndjson.2.gz"]:
        with gzip.open(os.path.join(dlq_dir, suffix), "rt") as f:
            contents += f.read()
            
    assert "large_payload_1234567890" in contents


def test_logtail_dlq_handles_permission_errors_gracefully(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    dlq_dir = str(tmp_path)
    dlq = LogtailDLQ(
        directory=dlq_dir,
        filename="err.ndjson",
        max_bytes=1000,
        backup_count=1,
        compress=False,
    )
    
    # Mock the internal handler's stream to blow up
    def raise_err(*args, **kwargs):
        raise PermissionError("Disk is read only")
        
    monkeypatch.setattr(dlq._handler.stream, "write", raise_err)
    
    dlq.submit('{"msg":"test"}', reason="send_failed")
    # It should suppress the error and bump the counter
    assert dlq.write_failures_total == 1


def test_logtail_dlq_concurrent_thread_safety(tmp_path: pytest.TempPathFactory) -> None:
    dlq_dir = str(tmp_path)
    dlq = LogtailDLQ(
        directory=dlq_dir,
        filename="threads.ndjson",
        max_bytes=1024 * 1024,
        backup_count=1,
        compress=False,
    )
    
    def worker(i: int):
        for _ in range(100):
            dlq.submit(f'{{"worker":{i}}}', reason="queue_overflow")
            
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
        
    dlq.close()
    
    with open(os.path.join(dlq_dir, "threads.ndjson")) as f:
        lines = f.readlines()
        
    assert len(lines) == 1000
    assert dlq.written_overflow == 1000


def test_logtail_handler_emits_to_dlq_on_queue_overflow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    # Setup handler with tiny bounded memory queue
    handler = _LogtailHandler(
        source_token="test",
        endpoint="http://fake",
        batch_size=5,
        flush_interval=100.0,
        max_queue_size=1,  # Can only hold 1
    )
    handler.enable_dlq(
        directory=str(tmp_path),
        filename="overflow.ndjson",
        max_bytes=1000,
        backup_count=1,
        compress=False,
    )
    
    formatter = logging.Formatter('{"msg":"%(message)s"}')
    handler.setFormatter(formatter)
    
    rec1 = logging.LogRecord("test", logging.INFO, "path", 1, "test1", None, None)
    rec2 = logging.LogRecord("test", logging.INFO, "path", 1, "test2", None, None)
    rec3 = logging.LogRecord("test", logging.INFO, "path", 1, "test3", None, None)
    
    # Fill the queue
    handler.emit(rec1)
    
    # This force an eviction of rec1 and adds rec2
    handler.emit(rec2)
    # This force an eviction of rec2 and adds rec3
    handler.emit(rec3)
    
    # Wait lightly for background processes
    time.sleep(0.1)
    
    assert handler.drop_count == 2
    
    dlq_stats = handler.dlq_stats()
    assert dlq_stats["written_overflow"] == 2
    assert dlq_stats["written_failed"] == 0
    
    handler.close()


def test_logtail_handler_emits_to_dlq_on_network_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    handler = _LogtailHandler(
        source_token="test",
        endpoint="http://fake",
        batch_size=2,
        flush_interval=0.1,  # Fast flush trigger
        max_retries=1, # Don't backoff forever in tests
    )
    handler.enable_dlq(
        directory=str(tmp_path),
        filename="netfail.ndjson",
        max_bytes=1000,
        backup_count=1,
        compress=False,
    )
    
    formatter = logging.Formatter('{"msg":"%(message)s"}')
    handler.setFormatter(formatter)
    
    # Mock urllib to always throw 500
    def mock_urlopen(*args, **kwargs):
        raise urllib.error.HTTPError("http://fake", 500, "Internal Server Error", {}, None)
        
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    
    rec1 = logging.LogRecord("test", logging.INFO, "path", 1, "failing1", None, None)
    handler.emit(rec1)
    
    # Allow the flush worker to pick it up and die
    time.sleep(0.2)
    
    assert handler.error_count == 1
    dlq_stats = handler.dlq_stats()
    assert dlq_stats["written_failed"] == 1
    assert dlq_stats["written_overflow"] == 0
    
    handler.close()
