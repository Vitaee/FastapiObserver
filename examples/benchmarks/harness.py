#!/usr/bin/env python3
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from typing import TypedDict

# Requirements: `ab` (ApacheBench; preinstalled on macOS, `apache2-utils` on Debian/Ubuntu)
# and `uv`. Docker is only needed for the tracing scenarios (S3, S4, S5, H4).
# Usage: python examples/benchmarks/harness.py [SCENARIO ...]

SCENARIOS = [
    "S0", # Baseline
    "S1", # Observer Minimal
    "S2", # Observer + Metrics
    "S3", # Observer + Tracing (Collector Up)
    "S4", # Observer + All Features (Collector Up)
    "S5", # Observer + All Features (Collector Down)
    "H4", # Hand-wired logs + metrics + tracing (Collector Up)
]
TRACING_SCENARIOS = {"S3", "S4", "S5", "H4"}

RUNS_PER_SCENARIO = 5
WARMUP_DURATION_SEC = 3
BENCH_DURATION_SEC = 5
CONCURRENCY = 200
ENDPOINT = "http://127.0.0.1:8001/items"
DATA_PAYLOAD = (
    '{"name":"BenchmarkItem","description":"A test item for benchmarking",'
    '"price":42.99,"tags":["test","benchmark"]}'
)
METHOD = "POST"
CONTENT_TYPE = "application/json"

class BenchmarkResult(TypedDict):
    rps: list[float]
    p50: list[float]
    p90: list[float]
    p95: list[float]
    p99: list[float]


def run_ab(duration: int) -> dict[str, float]:
    """Runs ApacheBench (`ab`) and parses requests/sec and latency percentiles."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as payload:
        payload.write(DATA_PAYLOAD)
    cmd = [
        "ab", "-q", "-k",
        "-t", str(duration),
        "-n", "10000000",
        "-c", str(CONCURRENCY),
        "-p", payload.name,
        "-T", CONTENT_TYPE,
        ENDPOINT,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    finally:
        os.unlink(payload.name)
    if result.returncode != 0:
        raise RuntimeError(f"ab failed: {result.stderr}")

    out = result.stdout
    if "Non-2xx responses" in out:
        raise RuntimeError(f"ab saw non-2xx responses:\n{out}")
    metrics = {}

    rps_match = re.search(r"Requests per second:\s+([\d\.]+)", out)
    if rps_match:
        metrics["rps"] = float(rps_match.group(1))

    # ab reports latency percentiles in whole milliseconds.
    for p_level, key in [("50", "p50"), ("90", "p90"), ("95", "p95"), ("99", "p99")]:
        p_match = re.search(fr"^\s+{p_level}%\s+(\d+)", out, re.MULTILINE)
        if p_match:
            metrics[key] = float(p_match.group(1))

    return metrics

def manage_collector(up: bool) -> None:
    try:
        # We assume the time_saved docker-compose file is our reference collector
        compose_file = "examples/time_saved/docker-compose.yml"
        if up:
            subprocess.run(
                ["docker", "compose", "-f", compose_file, "up", "-d"],
                check=True,
                capture_output=True,
            )
        else:
            subprocess.run(
                ["docker", "compose", "-f", compose_file, "down"],
                check=True,
                capture_output=True,
            )
    except Exception as e:
        raise RuntimeError(f"Failed to manage collector: {e}") from e
        
def start_uvicorn(scenario: str) -> subprocess.Popen:
    env = os.environ.copy()
    env["BENCHMARK_SCENARIO"] = scenario
    
    cmd = [
        "uv", "run", "uvicorn", "examples.benchmarks.app:app",
        "--port", "8001", "--workers", "1",
    ]
    
    # Run uv python script manually if nested
    if "VIRTUAL_ENV" in env:
        python_bin = os.path.join(env["VIRTUAL_ENV"], "bin", "python")
        cmd = [
            python_bin, "-m", "uvicorn", "examples.benchmarks.app:app",
            "--port", "8001", "--workers", "1",
        ]
        
    proc = subprocess.Popen(
        cmd, 
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(2) # Give uvicorn time to bind
    return proc

def format_stats(values: list[float], decimal_places: int = 2) -> str:
    if not values:
        return "N/A"
    median = statistics.median(values)
    if len(values) > 1:
        stdev = statistics.stdev(values)
        return f"{median:.{decimal_places}f} (±{stdev:.{decimal_places}f})"
    return f"{median:.{decimal_places}f}"

def main():
    print("Starting Advanced Benchmark Protocol")
    print(f"Concurrency: {CONCURRENCY}, Repetitions: {RUNS_PER_SCENARIO}, Endpoint: {ENDPOINT}")
    print("-" * 60)
    
    results: dict[str, BenchmarkResult] = {}
    scenarios = sys.argv[1:] or SCENARIOS

    for scenario in scenarios:
        print(f"\nEvaluating Scenario: {scenario}")
        # Determine collector state
        if scenario == "S5":
            print("  Bringing collector DOWN for Resilience test...")
            manage_collector(False)
        elif scenario in TRACING_SCENARIOS:
            manage_collector(True)
            
        print("  Starting Uvicorn...")
        proc = start_uvicorn(scenario)
        
        try:
            print(f"  Warmup ({WARMUP_DURATION_SEC}s)...")
            run_ab(WARMUP_DURATION_SEC)
            
            res: BenchmarkResult = {"rps": [], "p50": [], "p90": [], "p95": [], "p99": []}
            
            for i in range(1, RUNS_PER_SCENARIO + 1):
                print(f"  Run {i}/{RUNS_PER_SCENARIO}...", end="", flush=True)
                metrics = run_ab(BENCH_DURATION_SEC)
                print(f" {metrics.get('rps', 0):.0f} rps, p50: {metrics.get('p50', 0):.1f}ms")
                
                for k, v in metrics.items():
                    if k in res:
                        res[k].append(v)
            
            results[scenario] = res
            
        finally:
            print("  Terminating Uvicorn...")
            proc.terminate()
            proc.wait()
            
    print("\n" + "=" * 80)
    print("FINAL RESULTS (Median ± StDev)")
    print("=" * 80)
    print(
        f"| {'Scenario':<5} | {'RPS':<15} | {'P50 Latency (ms)':<18} | "
        f"{'P95 Latency (ms)':<18} | {'P99 Latency (ms)':<18} |"
    )
    print("|-------|-----------------|--------------------|--------------------|--------------------|")
    
    for scenario in scenarios:
        res = results[scenario]
        rps = format_stats(res['rps'], 0)
        p50 = format_stats(res['p50'], 1)
        p95 = format_stats(res['p95'], 1)
        p99 = format_stats(res['p99'], 1)
        
        print(f"| {scenario:<5} | {rps:<15} | {p50:<18} | {p95:<18} | {p99:<18} |")
        
    # Merge, so running a subset of scenarios keeps the others' results.
    results_path = "examples/benchmarks/results.json"
    if os.path.exists(results_path):
        with open(results_path) as f:
            results = {**json.load(f), **results}
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    main()
