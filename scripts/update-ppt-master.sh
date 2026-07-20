#!/usr/bin/env bash
#
# Reproducibly evaluate a ppt-master submodule update *candidate* without ever
# auto-committing or auto-deploying it.
#
#   pnpm ppt-master:update [revision]
#
# Behaviour:
#   - Records the current pinned commit and venv lock (so we can roll back).
#   - Fetches, then checks out the requested tag/commit. With no argument, the
#     candidate is upstream ``origin/main``'s current commit.
#   - Rebuilds the dedicated .venv-ppt-master and runs verify + smoke against
#     the candidate.
#   - On success: leaves the submodule pointer + venv updated as an *unstaged
#     diff* for a human to review and commit. Nothing is committed or deployed.
#   - On failure: restores the submodule pointer and venv lock to the recorded
#     baseline, leaving no diff.
#
# This script never passes LLM API keys anywhere and the smoke it runs uses no
# network/LLM.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SUBMODULE="vendor/ppt-master"
VENV_DIR=".venv-ppt-master"
VENV_LOCK=".venv-ppt-master.lock.txt"

log() { printf '[ppt-master:update] %s\n' "$*"; }
fail() { printf '[ppt-master:update][FAIL] %s\n' "$*" >&2; exit 1; }

[[ -d "$SUBMODULE" ]] || fail "submodule missing: run 'git submodule update --init $SUBMODULE'"

# 1) Record the rollback baseline.
BASELINE_COMMIT="$(git -C "$SUBMODULE" rev-parse HEAD)"
log "current pinned commit: $BASELINE_COMMIT"
BASELINE_LOCK=""
if [[ -f "$VENV_LOCK" ]]; then
  BASELINE_LOCK="$(cat "$VENV_LOCK")"
fi

# 2) Resolve the update candidate.
log "fetching upstream..."
git -C "$SUBMODULE" fetch --tags origin

if [[ $# -ge 1 && -n "${1:-}" ]]; then
  CANDIDATE_REF="$1"
else
  CANDIDATE_REF="origin/main"
  log "no revision given; evaluating upstream main"
fi

CANDIDATE_COMMIT="$(git -C "$SUBMODULE" rev-parse "$CANDIDATE_REF^{commit}")" \
  || fail "cannot resolve revision: $CANDIDATE_REF"
log "candidate: $CANDIDATE_REF -> $CANDIDATE_COMMIT"

rollback() {
  log "rolling back submodule to $BASELINE_COMMIT"
  git -C "$SUBMODULE" checkout --quiet "$BASELINE_COMMIT" || true
  # Rebuild the venv from the recorded baseline lock so state is fully restored.
  rm -rf "$VENV_DIR"
  if [[ -n "$BASELINE_LOCK" ]]; then
    uv venv --python 3.12 "$VENV_DIR" >/dev/null
    printf '%s\n' "$BASELINE_LOCK" > "$VENV_LOCK"
    # shellcheck disable=SC2046
    uv pip install --python "$VENV_DIR/bin/python" $(printf '%s\n' "$BASELINE_LOCK") >/dev/null 2>&1 || true
  fi
}

trap 'code=$?; if [[ $code -ne 0 ]]; then rollback; fi' EXIT

# 3) Check out the candidate and rebuild the dedicated venv.
git -C "$SUBMODULE" checkout --quiet "$CANDIDATE_COMMIT"

log "rebuilding $VENV_DIR"
rm -rf "$VENV_DIR"
uv venv --python 3.12 "$VENV_DIR" >/dev/null
# Native (offline) PPTX export deps only. Upstream never reaches the network at
# runtime, so we install just what svg_to_pptx's native path needs.
PPT_DEPS=("python-pptx==1.0.2" "lxml==6.1.1" "pillow==12.3.0")
uv pip install --python "$VENV_DIR/bin/python" "${PPT_DEPS[@]}" >/dev/null
printf '%s\n' "${PPT_DEPS[@]}" > "$VENV_LOCK"

# 4) Verify + smoke against the candidate.
log "verifying pinned structure (revision override = $CANDIDATE_COMMIT)"
uv run --no-sync python scripts/verify-ppt-master.py --revision "$CANDIDATE_COMMIT"

log "running offline smoke"
PPT_MASTER_PYTHON="$ROOT_DIR/$VENV_DIR/bin/python" \
  uv run --no-sync python -m alinea_worker.presentation.smoke

# 5) Success: keep the diff, do not commit or deploy.
trap - EXIT
log "OK — candidate $CANDIDATE_COMMIT passed verify + smoke"
log "The submodule pointer and $VENV_LOCK are left as an unstaged diff."
log "Review, then run: git -C $SUBMODULE rev-parse HEAD  and commit deliberately."
