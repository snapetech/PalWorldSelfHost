#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/palworld-common.sh"
[[ $# -eq 4 && "$1" =~ ^[0-9]+$ && "$2" == --execute && "$3" == --confirm && "$4" == "ROLLBACK BUILD" ]] || {
    echo "usage: ops-build-rollback.sh TARGET_BUILD --execute --confirm 'ROLLBACK BUILD'" >&2
    exit 64
}
target=$1
acquire_mutation_lock
export PALWORLD_LOCK_HELD=true
current=$("$SCRIPT_DIR/steam-build-manager.py" list | python3 -c 'import json,sys; print(json.load(sys.stdin)["current_build"])')
[[ "$current" != "$target" ]] || { echo "target build is already installed" >&2; exit 64; }
"$SCRIPT_DIR/steam-build-manager.py" plan "$target" >/dev/null
was_active=false
if systemctl is-active --quiet palworld.service; then
    was_active=true
    roster=$("$SCRIPT_DIR/rest-client.py" players) || { echo "cannot prove the active player roster is empty" >&2; exit 1; }
    count=$(python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("players", [])))' <<<"$roster")
    (( count == 0 )) || { echo "refusing build rollback while $count player(s) are online" >&2; exit 1; }
fi
protected_output=$("$SCRIPT_DIR/backup.sh" protected)
protected=$(tail -n 1 <<<"$protected_output")
if [[ "$was_active" == true ]]; then "$SCRIPT_DIR/graceful-shutdown.sh" 0 "Build rollback"; fi
rollback_snapshot=$("$SCRIPT_DIR/steam-build-manager.py" snapshot)
rollback_build=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["build_id"])' <<<"$rollback_snapshot")
trap 'systemctl start palworld.service' EXIT
ops_event audit build-rollback started --details "$current -> $target; save=$(basename "$protected")"
rollback_rc=0
"$SCRIPT_DIR/steam-build-manager.py" restore "$target" --confirm "ROLLBACK BUILD" || rollback_rc=$?
if (( rollback_rc == 0 )); then render_settings || rollback_rc=$?; fi
if (( rollback_rc == 0 )); then
    systemctl start palworld.service || rollback_rc=$?
    if (( rollback_rc == 0 )); then
        for _ in $(seq 1 45); do
            "$SCRIPT_DIR/rest-client.py" info >/dev/null 2>&1 && break
            sleep 2
        done
    fi
    if (( rollback_rc == 0 )) && "$SCRIPT_DIR/rest-client.py" info >/dev/null 2>&1; then
        "$SCRIPT_DIR/steam-build-manager.py" pin "$target" >/dev/null
        trap - EXIT
        ops_event audit build-rollback ok --details "$current -> $target; pinned"
        echo "rolled back executable build $current to $target; save backup: $protected"
        exit 0
    fi
    rollback_rc=1
fi
systemctl stop palworld.service || true
rollback_error=
if ! "$SCRIPT_DIR/steam-build-manager.py" restore "$rollback_build" --confirm "ROLLBACK BUILD"; then rollback_error="executable rollback restore failed"; fi
render_settings || rollback_error="${rollback_error:+$rollback_error; }settings render failed"
if systemctl start palworld.service; then
    for _ in $(seq 1 45); do
        "$SCRIPT_DIR/rest-client.py" info >/dev/null 2>&1 && break
        sleep 2
    done
    "$SCRIPT_DIR/rest-client.py" info >/dev/null 2>&1 || rollback_error="${rollback_error:+$rollback_error; }recovered build REST health failed"
else
    rollback_error="${rollback_error:+$rollback_error; }service restart failed"
fi
trap - EXIT
ops_event audit build-rollback failed --details "$current -> $target; automatic_restore=$rollback_build; $rollback_error"
if [[ -n "$rollback_error" ]]; then
    echo "build rollback failed and automatic executable recovery was incomplete: $rollback_error; saves: $protected" >&2
else
    echo "build rollback failed; prior executable build $rollback_build was restored; saves: $protected" >&2
fi
exit "$rollback_rc"
