#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

image_from_env_file=""
if [[ -f .env ]]; then
  image_from_env_file="$(
    awk -F= '/^[[:space:]]*ALINEA_TEXLIVE_IMAGE[[:space:]]*=/{print substr($0, index($0, "=") + 1)}' .env \
      | tail -n 1 \
      | sed -E 's/[[:space:]]+#.*$//; s/^[[:space:]]+//; s/[[:space:]]+$//; s/^"//; s/"$//; s/^'\''//; s/'\''$//'
  )"
fi

image="${ALINEA_TEXLIVE_IMAGE:-${image_from_env_file:-alinea-texlive-ja:latest}}"

docker_cmd=(bash scripts/dev-docker.sh)

if "${docker_cmd[@]}" image inspect "$image" >/dev/null 2>&1; then
  echo "TeX Live image already exists: $image"
  exit 0
fi

echo "Building TeX Live image for Japanese PDF builds: $image"
echo "This is a one-time setup and can take several minutes."

build_args=()
for proxy_var in HTTP_PROXY HTTPS_PROXY FTP_PROXY NO_PROXY http_proxy https_proxy ftp_proxy no_proxy; do
  if [[ -n "${!proxy_var:-}" ]]; then
    build_args+=(--build-arg "$proxy_var")
  fi
done

"${docker_cmd[@]}" build "${build_args[@]}" -f docker/texlive/Dockerfile -t "$image" .
