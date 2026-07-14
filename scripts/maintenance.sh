#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/palworld-common.sh"
acquire_mutation_lock
export PALWORLD_LOCK_HELD=true
reason=${1:-scheduled}
wait_seconds=${2:-${PALWORLD_MAINTENANCE_WAIT_SECONDS:-300}}
ops_event phase announcing --details "$reason"
ops_event notify maintenance "Palworld maintenance is starting ($reason)."
"$SCRIPT_DIR/graceful-shutdown.sh" "$wait_seconds"
trap 'systemctl start palworld.service' EXIT
ops_event phase stopped --details "$reason"
backup_rc=0 update_rc=0
ops_event phase backing-up --details "$reason"
"$SCRIPT_DIR/backup.sh" "$([[ "$reason" == update ]] && echo protected || echo daily)" || backup_rc=$?
ops_event phase updating --details "$reason"
"$SCRIPT_DIR/update.sh" || update_rc=$?
ops_event phase starting --details "$reason"
systemctl start palworld.service
ops_event phase verifying --details "$reason"
for _ in $(seq 1 30); do
    "$SCRIPT_DIR/rest-client.py" info >/dev/null 2>&1 && break
    sleep 2
done
"$SCRIPT_DIR/rest-client.py" info >/dev/null
trap - EXIT
if (( backup_rc != 0 || update_rc != 0 )); then
    ops_event phase failed --details "backup_rc=$backup_rc update_rc=$update_rc"
    ops_event notify maintenance-failed "Palworld maintenance failed: backup=$backup_rc update=$update_rc" --severity error
    exit 1
fi
ops_event phase complete --details "$reason"
ops_event notify maintenance-complete "Palworld maintenance completed successfully."
