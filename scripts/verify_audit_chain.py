"""
scripts/verify_audit_chain.py — Verify tamper-evident audit log chains.

Usage:
    export OBS_AUDIT_SECRET_KEY=your_secret
    python scripts/verify_audit_chain.py logs.ndjson
    python scripts/verify_audit_chain.py logs.ndjson.gz
"""

import argparse
import gzip
import os
import sys

# Allow running from project root without installing.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapiobserver.audit import verify_audit_chain  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify tamper-evident audit log chains.",
    )
    parser.add_argument("file", help="Path to NDJSON log file (.ndjson or .ndjson.gz)")
    parser.add_argument(
        "--key-env-var",
        default="OBS_AUDIT_SECRET_KEY",
        help="Environment variable containing the HMAC key (default: OBS_AUDIT_SECRET_KEY)",
    )
    args = parser.parse_args()

    key_raw = os.environ.get(args.key_env_var)
    if not key_raw:
        print(f"ERROR: {args.key_env_var} environment variable is required.", file=sys.stderr)
        sys.exit(1)
    key = key_raw.encode("utf-8")

    filepath = args.file
    if not os.path.isfile(filepath):
        print(f"ERROR: File not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    open_func = gzip.open if filepath.endswith(".gz") else open
    with open_func(filepath, "rt", encoding="utf-8") as f:
        result = verify_audit_chain(f, key)

    if result.valid:
        print(f"PASS — {result.total_records} records verified, chain intact.")
        sys.exit(0)
    else:
        print(f"FAIL — Chain broken at seq={result.failed_at_seq}: {result.error}")
        print(f"  Records checked: {result.total_records}")
        sys.exit(1)


if __name__ == "__main__":
    main()
