#!/usr/bin/env bash
set -euo pipefail

if docker version >/dev/null 2>&1; then
  exec docker "$@"
fi

probe_output="$(docker version 2>&1 >/dev/null || true)"
user_name="${USER:-$(id -un)}"

if [[ "$probe_output" == *"permission denied"* ]] \
  && command -v sg >/dev/null 2>&1 \
  && getent group docker | awk -F: -v user="$user_name" '
    $1 == "docker" {
      split($4, members, ",")
      for (i in members) {
        if (members[i] == user) found = 1
      }
    }
    END { exit found ? 0 : 1 }
  '; then
  printf -v docker_command "%q " docker "$@"
  exec sg docker -c "$docker_command"
fi

printf "%s\n" "$probe_output" >&2
cat >&2 <<'EOF'
Docker is not accessible from this shell.
If this is a stale login session after joining the docker group, run `newgrp docker`
or open a new terminal, then retry.
EOF
exit 1
