#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/palworld-common.sh"
[[ $# -eq 4 && "$1" =~ ^[0-9]+$ && "$2" == --execute && "$3" == --confirm && "$4" == "INSTALL UPDATE" ]] || {
    echo "usage: ops-update.sh TARGET_BUILD --execute --confirm 'INSTALL UPDATE'" >&2
    exit 64
}
target=$1
build_state=$("$SCRIPT_DIR/steam-build-manager.py" list)
pin=$(python3 -c 'import json,sys; print((json.load(sys.stdin).get("pin") or {}).get("build_id") or "")' <<<"$build_state")
[[ -z "$pin" ]] || { echo "build $pin is pinned; unpin it before installing an update" >&2; exit 65; }
status=$("$SCRIPT_DIR/update-status.py")
remote=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("remote_build") or "")' <<<"$status")
available=$(python3 -c 'import json,sys; print("true" if json.load(sys.stdin).get("update_available") else "false")' <<<"$status")
[[ "$available" == true && "$remote" == "$target" ]] || {
    echo "reviewed target build no longer matches the available Steam build" >&2
    exit 65
}
minutes=${PALWORLD_UPDATE_WARN_MINUTES:-30}
[[ "$minutes" =~ ^[0-9]+$ && "$minutes" -le 1440 ]] || { echo "invalid update warning minutes" >&2; exit 64; }
exec "$SCRIPT_DIR/maintenance.sh" update "$((minutes * 60))"
