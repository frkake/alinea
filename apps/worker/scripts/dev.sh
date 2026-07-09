#!/usr/bin/env bash
set -euo pipefail

# The worker handles long-running jobs. In normal dev we avoid arq --watch so a
# source/cache write does not SIGTERM an in-flight ingest and leave it queued
# for retry. Use pnpm --filter @alinea/worker dev:watch when hot reload is
# worth the cancellation risk.
export PYTHONDONTWRITEBYTECODE=1

pids=()

wait_for_tcp() {
  local host="$1"
  local port="$2"
  local name="$3"
  local timeout_s="${4:-60}"

  echo "Waiting for ${name} at ${host}:${port}..."
  for ((i = 1; i <= timeout_s; i++)); do
    if (echo >"/dev/tcp/${host}/${port}") >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  echo "Timed out waiting for ${name} at ${host}:${port}" >&2
  return 1
}

redis_url="${REDIS_URL:-redis://localhost:6379/0}"
redis_addr="${redis_url#redis://}"
redis_addr="${redis_addr#*@}"
redis_host="${redis_addr%%[:/]*}"
redis_port="6379"
if [[ "$redis_addr" == *:* ]]; then
  redis_port="${redis_addr#*:}"
  redis_port="${redis_port%%/*}"
fi
wait_for_tcp "${redis_host:-localhost}" "${redis_port:-6379}" "Redis" "${ALINEA_WORKER_REDIS_WAIT_TIMEOUT:-60}"

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if ((${#pids[@]} > 0)); then
    kill -TERM "${pids[@]}" 2>/dev/null || true
    wait "${pids[@]}" 2>/dev/null || true
  fi
  exit "$status"
}

trap cleanup EXIT INT TERM

uv run --no-sync arq alinea_worker.main.InteractiveWorker &
pids+=("$!")

uv run --no-sync arq alinea_worker.main.BulkWorker &
pids+=("$!")

wait -n "${pids[@]}"
