#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

image_from_env_file=""
if [[ -f .env ]]; then
  image_from_env_file="$(
    awk -F= '/^[[:space:]]*YAKUDOKU_TEXLIVE_IMAGE[[:space:]]*=/{print substr($0, index($0, "=") + 1)}' .env \
      | tail -n 1 \
      | sed -E 's/[[:space:]]+#.*$//; s/^[[:space:]]+//; s/[[:space:]]+$//; s/^"//; s/"$//; s/^'\''//; s/'\''$//'
  )"
fi

image="${YAKUDOKU_TEXLIVE_IMAGE:-${image_from_env_file:-yakudoku-texlive-ja:latest}}"

if docker image inspect "$image" >/dev/null 2>&1; then
  echo "TeX Live image already exists: $image"
  exit 0
fi

echo "Building TeX Live image for Japanese PDF builds: $image"
echo "This is a one-time setup and can take several minutes."
docker build -f docker/texlive/Dockerfile -t "$image" .
