# Using with Sentry

`fastapi-observer` and Sentry's FastAPI integration work together without extra setup. Initialise Sentry as usual, then call `install_observability()`:

```python
import sentry_sdk
from fastapi import FastAPI
from fastapiobserver import install_observability

sentry_sdk.init(dsn="https://<key>@<org>.ingest.sentry.io/<project>")

app = FastAPI()
install_observability(app)
```

What you get:

- **One Sentry event per failure.** An unhandled exception reaches Sentry once, with its full traceback, through the `request.failed` log line. Sentry discards its own duplicate of the same exception.
- **Your JSON logs are unchanged**, including the structured `error` field on `request.failed`.
- **Breadcrumbs carry the `request_id`**, so you can find the matching JSON lines for any Sentry issue.

## Redaction does not reach Sentry

`fastapi-observer` redacts what it writes. Sentry's logging integration reads the original log records, so it sees `extra` data before redaction. Sentry's default scrubber only checks top-level keys, which means nested values such as `extra={"event": {"token": "..."}}` reach Sentry's breadcrumbs unchanged.

Turn on recursive scrubbing, and add any field names your app treats as sensitive:

```python
import sentry_sdk
from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber

sentry_sdk.init(
    dsn="https://<key>@<org>.ingest.sentry.io/<project>",
    event_scrubber=EventScrubber(
        denylist=DEFAULT_DENYLIST + ["card_number", "cvv"],
        recursive=True,
    ),
)
```

With this, the nested `token` above arrives in Sentry as `[Filtered]`.

Tested with `sentry-sdk` 2.73.
