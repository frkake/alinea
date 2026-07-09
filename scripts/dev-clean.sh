#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SELF_PID="$$"
PARENT_PID="${PPID:-0}"

is_repo_process() {
  local pid="$1"
  local cmdline="$2"
  local cwd

  cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
  case "$cwd" in
    "$ROOT"|"$ROOT"/*) return 0 ;;
  esac
  [[ "$cmdline" == *"$ROOT"* ]]
}

is_dev_process() {
  local cmdline="$1"

  [[ "$cmdline" =~ (^|[[:space:]])pnpm([[:space:]][^[:space:]]+)*[[:space:]]dev([[:space:]]|$) ]] \
    || [[ "$cmdline" =~ (^|[[:space:]])turbo[[:space:]]dev([[:space:]]|$) ]] \
    || [[ "$cmdline" == *"uvicorn alinea_api.main:app"* ]] \
    || [[ "$cmdline" == *"arq alinea_worker.main"* ]] \
    || [[ "$cmdline" =~ (^|[[:space:]])next[[:space:]]dev([[:space:]]|$) ]] \
    || [[ "$cmdline" =~ (^|[[:space:]])wxt([[:space:]]|$) ]] \
    || [[ "$cmdline" == *"/wxt/bin/wxt.mjs"* ]]
}

pids=()
while IFS= read -r -d "" proc; do
  pid="${proc#/proc/}"
  [[ "$pid" =~ ^[0-9]+$ ]] || continue
  [[ "$pid" == "$SELF_PID" || "$pid" == "$PARENT_PID" ]] && continue
  [[ -r "/proc/$pid/cmdline" ]] || continue

  cmdline="$(tr "\0" " " <"/proc/$pid/cmdline" 2>/dev/null || true)"
  [[ -n "$cmdline" ]] || continue

  if is_repo_process "$pid" "$cmdline" && is_dev_process "$cmdline"; then
    pids+=("$pid")
  fi
done < <(find /proc -maxdepth 1 -type d -regex "/proc/[0-9]+" -print0 2>/dev/null)

if (( ${#pids[@]} == 0 )); then
  echo "No alinea dev processes found."
  exit 0
fi

echo "Stopping alinea dev processes: ${pids[*]}"
kill -TERM "${pids[@]}" 2>/dev/null || true

still_running=()
for _ in {1..20}; do
  still_running=()
  for pid in "${pids[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      still_running+=("$pid")
    fi
  done
  (( ${#still_running[@]} == 0 )) && break
  sleep 0.2
done

if (( ${#still_running[@]} > 0 )); then
  echo "Force stopping stubborn processes: ${still_running[*]}"
  kill -KILL "${still_running[@]}" 2>/dev/null || true
fi

echo "Clean."
