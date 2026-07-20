#!/usr/bin/env bash
#
# Offline smoke: build a native PPTX from the checked-in fixture SVGs using the
# pinned ppt-master submodule. The *conversion itself* uses no network and no
# LLM (the adapter's env allow-list omits every proxy/API-key variable).
#
# The dedicated venv (.venv-ppt-master) is provisioned once if missing — that
# bootstrap step is the only thing that may touch the network. Re-runs with the
# venv present perform zero external communication.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VENV_DIR=".venv-ppt-master"
VENV_LOCK=".venv-ppt-master.lock.txt"
PPT_DEPS=("python-pptx==1.0.2" "lxml==6.1.1" "pillow==12.3.0")

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "[ppt-master:smoke] provisioning $VENV_DIR (one-time bootstrap)"
  uv venv --python 3.12 "$VENV_DIR" >/dev/null
  uv pip install --python "$VENV_DIR/bin/python" "${PPT_DEPS[@]}" >/dev/null
  printf '%s\n' "${PPT_DEPS[@]}" > "$VENV_LOCK"
fi

# The conversion runs fully offline; the adapter drops proxy/API-key env vars.
PPT_MASTER_PYTHON="$ROOT_DIR/$VENV_DIR/bin/python" \
  uv run --no-sync python -m alinea_worker.presentation.smoke
