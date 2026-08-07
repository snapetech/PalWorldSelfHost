#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/palworld-common.sh"
acquire_mutation_lock
export PALWORLD_LOCK_HELD=true
reason=${1:-scheduled}
wait_seconds=${2:-${PALWORLD_MAINTENANCE_WAIT_SECONDS:-300}}
previous_build=
if [[ "$reason" == update ]]; then
    previous_build=$("$SCRIPT_DIR/steam-build-manager.py" list 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("current_build") or "")' || true)
fi
ops_event phase announcing --details "$reason"
ops_event notify maintenance "Palworld maintenance is starting ($reason)."
trap 'systemctl start palworld.service' EXIT
"$SCRIPT_DIR/graceful-shutdown.sh" "$wait_seconds"
ops_event phase stopped --details "$reason"
backup_rc=0 update_rc=0
ops_event phase backing-up --details "$reason"
"$SCRIPT_DIR/backup.sh" "$([[ "$reason" == update ]] && echo protected || echo daily)" || backup_rc=$?
ops_event phase updating --details "$reason"
if (( backup_rc == 0 )); then
    "$SCRIPT_DIR/update.sh" || update_rc=$?
else
    update_rc=78
    ops_event audit update skipped --details "verified backup failed rc=$backup_rc"
fi
ops_event phase starting --details "$reason"
start_rc=0
systemctl start palworld.service || start_rc=$?
ops_event phase verifying --details "$reason"
health_rc=$start_rc
if (( start_rc == 0 )); then
    for _ in $(seq 1 30); do
        "$SCRIPT_DIR/rest-client.py" info >/dev/null 2>&1 && break
        sleep 2
    done
    "$SCRIPT_DIR/rest-client.py" info >/dev/null 2>&1 || health_rc=$?
fi
if [[ "$reason" == update && -n "$previous_build" ]] && (( health_rc != 0 )); then
    ops_event audit update health-failed --details "attempting executable rollback to $previous_build"
    systemctl stop palworld.service || true
    rollback_rc=0
    "$SCRIPT_DIR/steam-build-manager.py" restore "$previous_build" --confirm "ROLLBACK BUILD" || rollback_rc=$?
    if (( rollback_rc == 0 )); then render_settings || rollback_rc=$?; fi
    if (( rollback_rc == 0 )); then systemctl start palworld.service || rollback_rc=$?; fi
    if (( rollback_rc == 0 )); then
        for _ in $(seq 1 30); do
            "$SCRIPT_DIR/rest-client.py" info >/dev/null 2>&1 && break
            sleep 2
        done
        "$SCRIPT_DIR/rest-client.py" info >/dev/null 2>&1 || rollback_rc=$?
    fi
    if (( rollback_rc == 0 )); then
        "$SCRIPT_DIR/steam-build-manager.py" pin "$previous_build" >/dev/null || rollback_rc=$?
    fi
    if (( rollback_rc == 0 )); then
        ops_event audit update executable-rollback --details "restored and pinned build $previous_build after health failure"
    else
        ops_event audit update rollback-failed --details "build=$previous_build rc=$rollback_rc"
    fi
fi
trap - EXIT
if (( backup_rc != 0 || update_rc != 0 || health_rc != 0 )); then
    ops_event phase failed --details "backup_rc=$backup_rc update_rc=$update_rc health_rc=$health_rc"
    ops_event notify maintenance-failed "Palworld maintenance failed: backup=$backup_rc update=$update_rc health=$health_rc" --severity error
    exit 1
fi
ops_event phase complete --details "$reason"
ops_event notify maintenance-complete "Palworld maintenance completed successfully."
