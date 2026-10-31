# 0005: Route templates, not paths, as metric labels

**Status:** accepted, with a known gap · **Reviewed:** 2026-10-31

## Context

Prometheus keeps one time series per combination of label values, and the request duration histogram multiplies that by its buckets. If `/orders/42` and `/orders/43` were separate labels, there would be a series per order. Memory and query cost would then grow with traffic until Prometheus falls over.

## Decision

The `path` label is the matched route template, such as `/orders/{order_id}`, read from `scope["route"]` ([`events.py:82`](https://github.com/Vitaee/FastapiObserver/blob/v1.8.0/src/fastapiobserver/middleware/events.py#L82)). When no route matched, the raw path is used instead. Numeric, UUID and long hexadecimal segments in it are collapsed to `:id`.

## Evidence

- **Tests:** [`test_route_template_preferred_over_raw_path`](https://github.com/Vitaee/FastapiObserver/blob/v1.8.0/tests/test_route_template_cardinality.py#L15) and the rest of that file.
- **Matched routes, measured:** 1,000 requests to `/orders/<id>` produce one `path` label.

## What it cost

- **The known gap: unmatched paths.** Requests that match no route, mostly 404s from scanners, keep their raw path as the label. Paths like `/.env`, `/wp-admin/setup.php` or `/probe-3f9a1c2b.php` don't collapse. Measured: after 1,000 requests with random 404 paths, `http_requests_total` had 1,000 more `path` labels, each with its own latency histogram. A single scan can therefore add thousands of series. The fix is to give unmatched requests one fixed label. Until then, keep `/metrics` internal and watch your series count.
- **Different apps, different labels.** The label depends on how the app declares routes, so two apps serving the same URLs can label them differently.
