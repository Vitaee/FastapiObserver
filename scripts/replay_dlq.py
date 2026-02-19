"""
scripts/replay_dlq.py — Basic utility to replay a Logtail DLQ directory.

Usage:
    export LOGTAIL_SOURCE_TOKEN=your_token
    python scripts/replay_dlq.py .dlq/logtail
"""

import argparse
import gzip
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("replay_dlq")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    formatter = logging.Formatter("[%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def replay_file(
    filepath: str,
    endpoint: str,
    token: str,
    batch_size: int,
    logger: logging.Logger,
) -> int:
    success_count = 0
    is_gzip = filepath.endswith(".gz")
    
    open_func = gzip.open if is_gzip else open
    
    batch: list[str] = []
    
    def _flush() -> None:
        nonlocal success_count, batch
        if not batch:
            return
            
        payload = "[" + ",".join(batch) + "]"
        data = payload.encode("utf-8")
        
        req = urllib.request.Request(
            endpoint,
            data=data,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status < 300:
                        success_count += len(batch)
                        batch.clear()
                        return
            except Exception as e:
                logger.warning(f"Batch flush failed: {e}. Retrying in {2**attempt}s...")
                time.sleep(2**attempt)
                
        logger.error("Exhausted retries for batch! Stopping file replay safely.")
        sys.exit(1)

    try:
        with open_func(filepath, "rt", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    envelope = json.loads(line)
                    raw_payload = json.dumps(envelope["payload"])
                    batch.append(raw_payload)
                    
                    if len(batch) >= batch_size:
                        _flush()
                except json.JSONDecodeError as err:
                    logger.error(f"Malformed NDJSON line in {filepath}: {err}")
                    
        # Flush tail
        _flush()
    except Exception as e:
        logger.error(f"Failed processing {filepath}: {e}")
        
    return success_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay Logtail DLQ NDJSON files.")
    parser.add_argument("directory", help="Path to the DLQ directory")
    parser.add_argument(
        "--endpoint",
        default="https://in.logs.betterstack.com",
        help="Logtail ingest endpoint",
    )
    parser.add_argument(
        "--batch-size", type=int, default=50, help="Batch size for replaying payloads"
    )
    args = parser.parse_args()

    logger = setup_logger()
    
    token = os.environ.get("LOGTAIL_SOURCE_TOKEN")
    if not token:
        logger.error("LOGTAIL_SOURCE_TOKEN environment variable is required.")
        sys.exit(1)

    if not os.path.isdir(args.directory):
        logger.error(f"Directory not found: {args.directory}")
        sys.exit(1)

    # Sort files chronologically (oldest first if standard rotation numeric suffix is used)
    files = sorted(os.listdir(args.directory))
    
    total_replayed = 0
    for filename in files:
        if not (filename.endswith(".ndjson") or filename.endswith(".ndjson.gz")):
            continue
            
        filepath = os.path.join(args.directory, filename)
        logger.info(f"Replaying file: {filepath}...")
        
        replayed = replay_file(
            filepath=filepath,
            endpoint=args.endpoint,
            token=token,
            batch_size=args.batch_size,
            logger=logger,
        )
        total_replayed += replayed
        logger.info(f"Successfully replayed {replayed} payloads from {filename}.")
        
    logger.info(f"Replay complete. Total records replayed: {total_replayed}")


if __name__ == "__main__":
    main()
