"""Check the running Compose example through its public HTTP and telemetry endpoints.

    python3 examples/full_stack/verify.py
    python3 examples/full_stack/verify.py --no-tracing

Uses only the standard library. Start the stack first; this script neither starts
nor stops containers. Each run uses fresh request IDs and bounded waits.
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from collections.abc import Callable, Iterator
from http.client import IncompleteRead
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def fetch(url: str, *, request_id: str | None = None) -> tuple[int, bytes]:
    request = Request(url, headers={"X-Request-ID": request_id} if request_id else {})
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except HTTPError as exc:
        return exc.code, exc.read()


def wait_for(check: Callable[[], Any], description: str) -> Any:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except (HTTPError, URLError, TimeoutError, KeyError, json.JSONDecodeError):
            pass
        time.sleep(0.25)
    raise RuntimeError(f"Timed out waiting for {description}")


def objects(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)


def verify(args: argparse.Namespace) -> None:
    prefix = f"verify-{uuid.uuid4().hex}"
    for port in (8000, 8001, 8002):
        assert fetch(f"http://localhost:{port}/health")[0] == 200
    expected = {}

    def call(name: str, path: str, status: int, **event: Any) -> bytes:
        request_id = f"{prefix}-{name}"
        actual, body = fetch(f"http://localhost:8000{path}", request_id=request_id)
        assert actual == status, (path, actual, body)
        expected[request_id] = {"status_code": status, **event}
        return body

    chain = json.loads(call("chain", "/chain", 200, count=2))
    for result in chain["results"].values():
        assert result["request_id"] == f"{prefix}-chain"
    call("slow", "/lab/downstream?seconds=0.2&calls=2", 200, count=2)
    call(
        "timeout",
        "/lab/downstream?seconds=0.2&timeout=0.02",
        504,
        count=1,
        errors={"read_timeout": 1},
    )
    call("status", "/lab/downstream?failure=true", 502, count=1, errors={"http_5xx": 1})
    call("background", "/lab/background?seconds=0.2&fail=true", 202)
    assert call("stream", "/lab/downstream-stream?seconds=0.2", 200, count=1) == b"first\nlast\n"
    broken_id = f"{prefix}-broken-stream"
    try:
        fetch(
            "http://localhost:8000/lab/downstream-stream?seconds=0.2&timeout=0.02",
            request_id=broken_id,
        )
    except IncompleteRead:
        pass
    else:
        raise AssertionError("The downstream stream should have been interrupted")
    expected[broken_id] = {"status_code": 200, "count": 1, "errors": {"read_timeout": 1}}

    def request_logs() -> dict[str, Any]:
        query = urlencode({"query": f'{{service_name="app-a"}} |= "{prefix}"', "limit": 1000})
        payload = json.loads(fetch(f"http://localhost:3100/loki/api/v1/query_range?{query}")[1])
        records = {}
        for stream in payload["data"]["result"]:
            for entry in stream["values"]:
                record = json.loads(entry[1])
                if record["message"].startswith("request."):
                    records[record["request_id"]] = record
        return records if expected.keys() <= records.keys() else {}

    records = wait_for(request_logs, "the correlated request records in Loki")
    for request_id, wanted in expected.items():
        record = records[request_id]
        event = record["event"]
        assert event["status_code"] == wanted["status_code"], record
        assert event["app_duration_ms"] >= event["duration_ms"], record
        assert event["response_completed"] is (request_id != broken_id), record
        if "count" in wanted:
            assert event["http"]["count"] == wanted["count"], record
            assert event["http"].get("errors", {}) == wanted.get("errors", {}), record
        if args.no_tracing:
            assert not record.get("trace_id"), record
    background = records[f"{prefix}-background"]
    assert background["message"] == "request.failed", background
    assert background["event"]["app_duration_ms"] - background["event"]["duration_ms"] >= 150
    assert records[broken_id]["message"] == "request.failed"
    assert records[f"{prefix}-slow"]["event"]["http"]["ms"] >= 300
    assert records[f"{prefix}-stream"]["event"]["duration_ms"] >= 150

    def prometheus_ready() -> bool:
        query = urlencode(
            {
                "query": 'http_requests_total{service="app-a",path="/lab/background",'
                'status_code="202"}'
            }
        )
        payload = json.loads(fetch(f"http://localhost:9090/api/v1/query?{query}")[1])
        return any(float(point["value"][1]) >= 1 for point in payload["data"]["result"])

    wait_for(prometheus_ready, "the completed background response in Prometheus")
    if not args.no_tracing:
        trace_id = records[f"{prefix}-chain"]["trace_id"]

        def trace_ready() -> bool:
            trace = json.loads(fetch(f"http://localhost:3200/api/traces/{trace_id}")[1])
            entries = list(objects(trace))
            services = {
                entry["value"].get("stringValue")
                for entry in entries
                if entry.get("key") == "service.name"
            }
            clients = sum(entry.get("kind") in (3, "SPAN_KIND_CLIENT") for entry in entries)
            return {"app-a", "app-b", "app-c"} <= services and clients >= 2

        wait_for(trace_ready, "the three-service trace and both client spans in Tempo")
    print("PASS: response/background timing, HTTP counts, timeout/status errors, streaming,")
    print("      cross-service request IDs, Loki export and Prometheus scraping")
    print(
        "      tracing disabled"
        if args.no_tracing
        else "      Tempo trace and client spans without duplicate counts"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-tracing", action="store_true")
    args = parser.parse_args()
    try:
        verify(args)
    except (AssertionError, OSError, RuntimeError, ValueError, KeyError) as exc:
        print(f"FAIL: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
