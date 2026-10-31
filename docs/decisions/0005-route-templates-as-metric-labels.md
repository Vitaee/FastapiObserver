# 0005: Route templates, not paths, as metric labels

**Status:** accepted · **Reviewed:** 2026-10-31

## Context

Prometheus keeps one time series per combination of label values, and the request duration histogram multiplies that by its buckets. If `/orders/42` and `/orders/43` were separate labels, there would be a series per order. Memory and query cost would then grow with traffic until Prometheus falls over.

## Decision

The `path` label comes from `_extract_route_template()` ([`events.py`](https://github.com/Vitaee/FastapiObserver/blob/main/src/fastapiobserver/middleware/events.py)):

- A matched route is labelled by its template, such as `/orders/{order_id}`, read from `scope["route"]`.
- A 404 with no matched route, mostly scanners probing paths like `/.env`, gets one shared label: `<unmatched>`.
- Any other request without a template keeps its path, with numeric, UUID and long hexadecimal segments collapsed to `:id`. FastAPI doesn't record plain Starlette routes in the scope, for example ones added with `app.add_route()`.

## Evidence

- **Scanner 404s, measured.** 1,000 requests with random 404 paths used to add 1,000 `path` labels, each with its own latency histogram. They now add one, `<unmatched>`.
- **Matched routes, measured:** 1,000 requests to `/orders/<id>` produce one label.
- **Tests:**
    - [`test_route_template_cardinality.py`](https://github.com/Vitaee/FastapiObserver/blob/main/tests/test_route_template_cardinality.py), including `test_scanner_404s_share_one_metrics_label`, which runs real 404s through `/metrics`;
    - `test_plain_route_without_template_keeps_raw_path`.

## What it cost

- **No per-path breakdown of 404s in metrics.** To see what scanners probe, query the request log, which keeps the path.
- **Dynamic plain routes still add series.** On a plain Starlette route, a segment that isn't a number, UUID or long hex value adds a series per value.
- **Mounted sub-apps drop the mount prefix.** They're labelled by their inner template, so a mount at `/sub` with a route `/{z}` is labelled `/{z}`, and two mounts with the same inner template share a label.
