#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/palworld-common.sh"
acquire_mutation_lock
export PALWORLD_LOCK_HELD=true
message=${1:-"Server restart in 60 seconds"}
"$SCRIPT_DIR/rest-client.py" announce --message "$message" || true
sleep "${PALWORLD_RESTART_WARN_SECONDS:-60}"
"$SCRIPT_DIR/rest-client.py" save
sleep 5
systemctl restart palworld.service
for _ in {1..30}; do
  "$SCRIPT_DIR/rest-client.py" info >/dev/null 2>&1 && { ops_event audit restart ok; exit 0; }
  sleep 2
done
ops_event audit restart failed
exit 1
