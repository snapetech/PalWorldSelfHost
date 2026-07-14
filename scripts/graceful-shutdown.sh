#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/palworld-common.sh"
wait_seconds=${1:-300}
message=${2:-Nightly maintenance begins at 5:00 AM. Please get somewhere safe.}
"$SCRIPT_DIR/rest-client.py" save >/dev/null
"$SCRIPT_DIR/rest-client.py" shutdown --wait "$wait_seconds" --message "$message" >/dev/null
deadline=$((SECONDS + wait_seconds + 90))
while systemctl is-active --quiet palworld.service && (( SECONDS < deadline )); do sleep 2; done
systemctl stop palworld.service

