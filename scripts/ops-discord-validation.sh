#!/usr/bin/env bash
set -euo pipefail
ENV_FILE=${PALWORLD_ENV_FILE:-/etc/palworld-server.env}
[[ -r "$ENV_FILE" ]] || { echo "missing $ENV_FILE" >&2; exit 1; }
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
exec "$(dirname "$0")/discord-validation.py" "$@"
