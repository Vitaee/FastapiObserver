"""
Prometheus metrics for the API.
"""
import re
from prometheus_client import Counter, Gauge, Histogram

DURATION_BUCKETS = (
    0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5,
    0.75, 1.0, 2.5, 5.0, 7.5, 10.0, float("inf"),
)

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    labelnames=["handler", "method"],
    buckets=DURATION_BUCKETS,
)

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total number of HTTP requests",
    labelnames=["handler", "method", "status"],
)

HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "http_requests_in_progress",
    "Number of HTTP requests currently being processed",
)

_UUID_RE = re.compile(r"""[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-
[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}""")
_INT_RE = re.compile(r"/\d+(?=/|$)")

def normalize_path(path: str) -> str:
    path = _UUID_RE.sub("{id}", path)
    path = _INT_RE.sub("/{id}", path)
    return path
