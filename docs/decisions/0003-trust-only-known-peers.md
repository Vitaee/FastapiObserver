# 0003: Identity headers count only from known peers

**Status:** accepted · **Reviewed:** 2026-10-31

## Context

`X-Request-ID`, `traceparent` and `X-Forwarded-For` are set by whoever sends the request. If they were accepted from anyone, a client could:

- choose the request ID that appears in your logs, or reuse another request's ID;
- attach its request to someone else's trace;
- make the logs show any client IP it likes.

## Decision

- **Request ID and `traceparent`** are taken from the request only when the direct peer is in `OBS_TRUSTED_CIDRS` ([`context.py:33`](https://github.com/Vitaee/FastapiObserver/blob/v1.8.0/src/fastapiobserver/middleware/context.py#L33)). By default that's the private ranges plus loopback. Otherwise a new request ID is generated.
- **Client IP** comes from `X-Forwarded-For` only when `OBS_HONOR_FORWARDED_HEADERS=true`. The hops are read right to left, and the first address that isn't a trusted proxy is the client ([`proxies.py:28`](https://github.com/Vitaee/FastapiObserver/blob/v1.8.0/src/fastapiobserver/security/proxies.py#L28)). Proxies append to the header, so only the right-hand entries are written by servers you control.
- **The runtime control plane** uses the same check, so it can't be reached through a public ingress ([`control_plane.py:125`](https://github.com/Vitaee/FastapiObserver/blob/v1.8.0/src/fastapiobserver/control_plane.py#L125)).

## Evidence

- **A spoofing bug, fixed in 1.5.0.** Until 1.5.0, the logged client IP was the leftmost `X-Forwarded-For` entry, which the client controls, so clients could log any IP they chose ([`b580060`](https://github.com/Vitaee/FastapiObserver/commit/b580060)).
- **Tests:**
    - [`test_spoofed_request_id_is_ignored_for_untrusted_source`](https://github.com/Vitaee/FastapiObserver/blob/v1.8.0/tests/test_trust_boundary.py#L28)
    - [`test_forwarded_for_uses_rightmost_hop_outside_trusted_proxies`](https://github.com/Vitaee/FastapiObserver/blob/v1.8.0/tests/test_trust_boundary.py#L56)
    - [`test_control_plane_only_accepts_trusted_callers`](https://github.com/Vitaee/FastapiObserver/blob/v1.8.0/tests/test_runtime_control_auth.py#L57)

## What it cost

- **Request IDs from an untrusted proxy are ignored.** If your edge proxy is outside the default ranges, its request IDs don't count until you add it to `OBS_TRUSTED_CIDRS`. It's the most likely surprise on day one.
- **The defaults trust every private address.** In a shared network that includes machines you don't control. Narrow the list in production.
- **Honouring forwarded headers needs care.** `OBS_TRUSTED_CIDRS` must list only your proxies, and the library logs a warning when the setting is on.
