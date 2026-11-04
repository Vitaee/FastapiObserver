"""Compare paired CPU overhead on one runner; never accept an unstable baseline."""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


def mad(values: list[float]) -> float:
    center = statistics.median(values)
    return statistics.median(abs(value - center) for value in values)


def compare_samples(baseline: list[float], candidate: list[float]) -> dict[str, object]:
    if len(baseline) != len(candidate) or len(baseline) < 7:
        raise ValueError("Need at least seven paired measurements")
    if not all(math.isfinite(value) and value > 0 for value in baseline + candidate):
        raise ValueError("CPU overhead measurements must be finite and positive")
    center = statistics.median(baseline)
    differences = [new - old for old, new in zip(baseline, candidate)]
    regression = statistics.median(differences)
    noise = 3 * 1.4826 * mad(differences) / math.sqrt(len(differences))
    reliable = (
        1.4826 * mad(baseline) <= center * 0.10
        and 1.4826 * mad(candidate) <= statistics.median(candidate) * 0.10
    )
    budget = center * 0.10 + noise
    return {"baseline_us": center, "candidate_us": statistics.median(candidate),
            "regression_us": regression, "budget_us": budget, "noise_us": noise,
            "reliable": reliable, "passed": reliable and regression <= budget,
            "baseline_samples": baseline, "candidate_samples": candidate}


def measure(root: Path, harness: Path, mode: str) -> dict:
    result = subprocess.run(
        [sys.executable, str(harness), mode, "--json"],
        env={**os.environ, "PYTHONPATH": str(root / "src"), "BENCH_RUNS": "1",
             "BENCH_REQUESTS": "2000", "BENCH_WARMUP": "500"},
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=60,
    )
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:])
    payload = json.loads(result.stderr.strip().splitlines()[-1])
    if not Path(payload["source"]).is_relative_to(root / "src"):
        raise RuntimeError("Benchmark imported a different checkout")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, help="Immutable Git commit to compare")
    parser.add_argument("--output", type=Path, default=Path("overhead.json"))
    parser.add_argument("--pairs", type=int, default=9)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    harness = root / "examples/benchmarks/request_cpu.py"
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", f"{args.baseline}^{{commit}}"], cwd=root, text=True,
    ).strip()
    candidate = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    report: dict = {"baseline_commit": commit, "candidate_commit": candidate, "modes": {}}
    with tempfile.TemporaryDirectory(prefix="observer-baseline-") as directory:
        baseline = Path(directory)
        archive = baseline / "source.tar"
        with archive.open("wb") as output:
            subprocess.run(["git", "archive", commit, "src"], cwd=root, stdout=output, check=True)
        with tarfile.open(archive) as source:
            source.extractall(baseline, filter="data")
        for mode in ("default", "application"):
            old, new = [], []
            for pair in range(args.pairs):
                # Alternate order to reduce a consistent warm-up/thermal bias.
                for location, samples in ((baseline, old), (root, new))[::1 if pair % 2 else -1]:
                    payload = measure(location, harness, mode)
                    samples.append(payload["process_us"])
                    report["python"] = payload["python"]
                    report["platform"] = payload["platform"]
                    report["dependencies"] = payload["dependencies"]
            report["modes"][mode] = compare_samples(old, new)
    report["passed"] = all(mode["passed"] for mode in report["modes"].values())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
