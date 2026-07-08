#!/usr/bin/env bash
set -euo pipefail

api_url="${API_INTERNAL_URL:-http://localhost:8000}"
health_url="${api_url%/}/api/healthz"
timeout_s="${YAKUDOKU_WEB_API_WAIT_TIMEOUT:-60}"

echo "Waiting for Yakudoku API at ${health_url}..."
for ((i = 1; i <= timeout_s; i++)); do
  if curl -fsS --max-time 2 "$health_url" >/dev/null 2>&1; then
    exec next dev "$@"
  fi
  sleep 1
done

echo "Timed out waiting for Yakudoku API at ${health_url}" >&2
exit 1
