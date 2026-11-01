# 0011: Every unmatched request shares one label, and methods are bounded

**Status:** accepted · **Reviewed:** 2026-11-01 · Supersedes the unmatched-request rules of [0005](0005-route-templates-as-metric-labels.md)

## Context

[0005](0005-route-templates-as-metric-labels.md) gave only 404s without a matched route the shared `<unmatched>` label. Any other request without a template kept its raw path. That left three ways for any anonymous client to add a time series per request:

- a CORS preflight to a made-up path, which `CORSMiddleware` answers with 200 before routing;
- a request to a made-up path that an auth middleware rejects with 401 or 403 before routing;
- a made-up HTTP method, because the `method` label was copied from the request.

## Decision

- A request whose scope has a route template is labelled by it, as before.
- Otherwise the app's routes are matched against the request. This finds plain Starlette routes, such as ones added with `app.add_route()`, which FastAPI doesn't record in the scope. A route whose path matches but whose method doesn't also counts, so a 405 is labelled by its route.
- Anything else gets `<unmatched>`, whatever its status code.
- The `method` label is one of the nine standard methods, or `_OTHER`, as OpenTelemetry's semantic conventions do.

## Evidence

- **Measured:** 3 preflights to random paths and 3 made-up methods used to add 6 series. They now add 2: `OPTIONS <unmatched>` and `_OTHER /orders/{order_id}`.
- **Tests:** `test_preflights_rejections_and_made_up_methods_add_no_series` and `test_plain_starlette_route_is_labelled_by_its_path` in [`test_route_template_cardinality.py`](https://github.com/Vitaee/FastapiObserver/blob/main/tests/test_route_template_cardinality.py).

## What it cost

- **Matching costs unmatched requests about 20 µs** in an app with 54 routes. Requests with a template still take 0.17 µs to label.
- **Plain routes are labelled by their template**, such as `/plain/{name}`, not by their path with IDs collapsed. Dashboards that filter on the old labels need updating.
- **Custom methods share `_OTHER` in metrics.** The request log keeps the real method.
