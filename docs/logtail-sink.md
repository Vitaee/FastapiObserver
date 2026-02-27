# Optional Logtail sink

| Variable | Default | Description |
|---|---|---|
| `LOGTAIL_ENABLED` | `false` | Enable Better Stack Logtail sink |
| `LOGTAIL_SOURCE_TOKEN` | - | Logtail source token |
| `LOGTAIL_BATCH_SIZE` | `50` | Batch size for shipping |
| `LOGTAIL_FLUSH_INTERVAL` | `2.0` | Flush interval (seconds) |
| `LOGTAIL_DLQ_ENABLED` | `false` | Enable resilient local disk fallback for dropped logs |
| `LOGTAIL_DLQ_DIR` | `.dlq/logtail` | Directory to archive dropped NDJSON messages |
| `LOGTAIL_DLQ_MAX_BYTES` | `52428800` | Max bytes per DLQ file before rotation (50MB) |
| `LOGTAIL_DLQ_COMPRESS` | `true` | GZIP compress rotated DLQ files |

> [!TIP]
> The Logtail Dead Letter Queue (DLQ) provides best-effort local durability. If the internal memory queue overflows under immense API pressure (`queue.Full`), or if an external network partition completely exhausts the outbound HTTP retry backoff, the dropped log payloads are immediately salvaged into local NDJSON envelopes. You can replay these files to BetterStack later using the provided `scripts/replay_dlq.py` utility.

