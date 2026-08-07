#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/palworld-common.sh"
action=${1:-}
case "$action" in
    start)
        systemctl start palworld.service
        "$SCRIPT_DIR/auto-pause.py" resumed >/dev/null
        "$SCRIPT_DIR/ops-event.py" audit operator.start ok --details on-demand
        ;;
    stop)
        [[ "${2:-}" == "STOP SERVER" ]] || { echo "confirmation required" >&2; exit 2; }
        "$SCRIPT_DIR/graceful-shutdown.sh" 30 "Server is stopping by administrator request."
        "$SCRIPT_DIR/ops-event.py" audit operator.stop ok --details graceful
        ;;
    *) echo "usage: $0 start | stop 'STOP SERVER'" >&2; exit 2 ;;
esac
