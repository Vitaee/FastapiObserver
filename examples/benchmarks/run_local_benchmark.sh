#!/usr/bin/env bash
set -euo pipefail

REQUESTS="${REQUESTS:-50000}"
CONCURRENCY="${CONCURRENCY:-200}"
HOST="${HOST:-127.0.0.1}"
PORT_BASELINE="${PORT_BASELINE:-9001}"
PORT_OBSERVER="${PORT_OBSERVER:-9002}"

if ! command -v hey >/dev/null 2>&1; then
  echo "error: 'hey' is required. Install from https://github.com/rakyll/hey"
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "error: 'uv' is required to run uvicorn commands in this benchmark script."
  exit 1
fi

run_case() {
  local case_name="$1"
  local app_target="$2"
  local port="$3"

  echo ""
  echo "== ${case_name} =="
  uv run uvicorn "${app_target}" --host "${HOST}" --port "${port}" --workers 1 >/tmp/fastapiobserver-benchmark-"${case_name}".log 2>&1 &
  local server_pid=$!

  cleanup() {
    if kill -0 "${server_pid}" >/dev/null 2>&1; then
      kill "${server_pid}" >/dev/null 2>&1 || true
      wait "${server_pid}" 2>/dev/null || true
    fi
  }
  trap cleanup EXIT

  local ready=0
  for _ in $(seq 1 50); do
    if curl -fsS "http://${HOST}:${port}/ping" >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 0.1
  done

  if [[ "${ready}" -ne 1 ]]; then
    echo "error: server did not become ready for ${case_name}"
    cleanup
    trap - EXIT
    exit 1
  fi

  hey -n "${REQUESTS}" -c "${CONCURRENCY}" "http://${HOST}:${port}/ping"

  cleanup
  trap - EXIT
}

run_case "baseline" "examples.benchmarks.plain_fastapi:app" "${PORT_BASELINE}"
run_case "observer" "examples.benchmarks.observer_fastapi:app" "${PORT_OBSERVER}"
