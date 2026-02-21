import logging

import pytest
from fastapi.testclient import TestClient

from examples.time_saved.the_hard_way import app as hard_app
from examples.time_saved.the_easy_way import app as easy_app


@pytest.fixture
def hard_client() -> TestClient:
    return TestClient(hard_app)


@pytest.fixture
def easy_client() -> TestClient:
    return TestClient(easy_app)


def test_dx_parity_metrics_and_logs(
    hard_client: TestClient, easy_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    
    # 1. Fire identical requests
    hard_resp = hard_client.get("/hello/world")
    easy_resp = easy_client.get("/hello/world")

    assert hard_resp.status_code == 200
    assert easy_resp.status_code == 200

    # 2. Check JSON Logs
    # Note: Because the 'hard' way uses a custom JSONFormatter that dumps
    # strings, we need to inspect the formatted messages or the test log
    # output. Fortunately, caplog captures records directly!
    # Let's inspect the Raw capout
    records = caplog.records
    assert len(records) >= 4, "Both apps should have emitted log records"
    
    # Check "hard way" request log
    hard_request_logs = [r for r in records if getattr(r, "request_info", None)]
    assert len(hard_request_logs) == 1
    
    # Check "easy way" request log
    easy_request_logs = [
        r for r in records
        if r.name == "fastapiobserver.middleware" and r.message == "request.completed"
    ]
    assert len(easy_request_logs) == 1

    # In JSON rendering, these produce identical keys
    # (trace_id, span_id, request_id, event/request...)
    # The true test of parity is the presence of the contextual fields
    assert hasattr(hard_request_logs[0], "request_info")
    assert hasattr(easy_request_logs[0], "event")

    # 3. Check Prometheus Metrics Parity
    hard_metrics = hard_client.get("/metrics")
    easy_metrics = easy_client.get("/metrics")

    assert hard_metrics.status_code == 200
    assert easy_metrics.status_code == 200

    hard_text = hard_metrics.text
    easy_text = easy_metrics.text

    # Both should have successfully routed the GET /hello/{name} 
    # and prevented cardinality explosion via templating.
    # The 'hard_way' hand-rolled labels looks like:
    # `http_requests_total{method="GET",path="/hello/{name}",status="200"}`
    # The 'easy_way' uses standard fastapiobserver prometheus conventions:
    # `http_server_requests_total{...,path="/hello/{name}",status_code="200"}`
    
    assert 'path="/hello/{name}"' in hard_text
    assert 'path="/hello/{name}"' in easy_text 
    
    assert 'status="200"' in hard_text
    assert 'status_code="200"' in easy_text

