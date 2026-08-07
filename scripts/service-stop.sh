#!/usr/bin/env bash
set -u
source "$(dirname "$0")/palworld-common.sh"
# ExecStop runs while the game process is still alive. Request an authenticated
# flush and immediate application stop; systemd retains its timeout/kill fallback
# when REST is unavailable or the game is already gone.
"$SCRIPT_DIR/rest-client.py" save >/dev/null 2>&1 || true
"$SCRIPT_DIR/rest-client.py" stop >/dev/null 2>&1 || true
exit 0
