#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/palworld-common.sh"
acquire_mutation_lock
if [[ ${1:-} != --force ]] && find "$(dirname "$PALWORLD_INSTALL_DIR")" "$PALWORLD_INSTALL_DIR" -name appmanifest_2394010.acf -print -quit 2>/dev/null | grep -q .; then
    status=$("$SCRIPT_DIR/update-status.py" 2>/dev/null || true)
    [[ "$status" == *'"update_available": true'* ]] || { ops_event audit update skipped --details "already current"; render_settings; exit 0; }
fi
"$PALWORLD_STEAMCMD" +force_install_dir "$PALWORLD_INSTALL_DIR" +login anonymous +app_update 2394010 validate +quit
render_settings
