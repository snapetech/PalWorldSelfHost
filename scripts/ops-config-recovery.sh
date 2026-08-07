#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/palworld-common.sh"
kind=${1:-}; target=${2:-}; confirmation=${3:-}
[[ "$kind" == world || "$kind" == engine || "$kind" == world-options ]] || { echo "invalid recovery kind" >&2; exit 2; }
[[ "$confirmation" == "RECOVER CONFIG" ]] || { echo "confirmation required" >&2; exit 2; }
"$SCRIPT_DIR/graceful-shutdown.sh" 0 "Configuration recovery" || systemctl stop palworld.service
command=("$SCRIPT_DIR/config-recovery.py" repair "$kind" --confirm "RECOVER CONFIG")
[[ -z "$target" ]] || command+=(--target "$target")
runuser -u "$PALWORLD_USER" --preserve-environment -- "${command[@]}"
