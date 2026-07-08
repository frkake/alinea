#!/usr/bin/env bash
set -euo pipefail

api_host="${API_HOST:-127.0.0.1}"
api_port="${API_PORT:-8000}"
api_url="${API_INTERNAL_URL:-http://${api_host}:${api_port}}"
health_url="${api_url%/}/api/healthz"

health_ok() {
  curl -fsS --max-time 2 "$health_url" >/dev/null 2>&1
}

if health_ok; then
  echo "Yakudoku API already running at ${health_url}; reusing it."
  while health_ok; do
    sleep 2
  done
  echo "Existing Yakudoku API at ${health_url} stopped." >&2
  exit 1
fi

if lsof -nP -iTCP:"$api_port" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port ${api_port} is already in use, but ${health_url} is not healthy." >&2
  lsof -nP -iTCP:"$api_port" -sTCP:LISTEN >&2 || true
  exit 1
fi

exec uv run --no-sync uvicorn yakudoku_api.main:app --reload --host "$api_host" --port "$api_port"
