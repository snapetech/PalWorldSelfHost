#!/usr/bin/env bash
set -euo pipefail
"$(dirname "$0")/graceful-shutdown.sh" 300
trap 'systemctl start palworld.service' EXIT
backup_rc=0
update_rc=0
"$(dirname "$0")/backup.sh" || backup_rc=$?
"$(dirname "$0")/update.sh" || update_rc=$?
systemctl start palworld.service
trap - EXIT
if (( backup_rc != 0 || update_rc != 0 )); then
    echo "maintenance completed with errors: backup_rc=$backup_rc update_rc=$update_rc" >&2
    exit 1
fi
