#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/palworld-common.sh"
acquire_mutation_lock
if [[ ${1:-} != --force ]] && find "$(dirname "$PALWORLD_INSTALL_DIR")" "$PALWORLD_INSTALL_DIR" -name appmanifest_2394010.acf -print -quit 2>/dev/null | grep -q .; then
    status=$("$SCRIPT_DIR/update-status.py" 2>/dev/null || true)
    [[ "$status" == *'"update_available": true'* ]] || { ops_event audit update skipped --details "already current"; render_settings; exit 0; }
fi
snapshot_build=
if find "$(dirname "$PALWORLD_INSTALL_DIR")" "$PALWORLD_INSTALL_DIR" -name appmanifest_2394010.acf -print -quit 2>/dev/null | grep -q .; then
    snapshot=$("$SCRIPT_DIR/steam-build-manager.py" snapshot)
    snapshot_build=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["build_id"])' <<<"$snapshot")
fi
steamcmd=("$PALWORLD_STEAMCMD")
[[ "$PALWORLD_RUNTIME" != wine-windows ]] || steamcmd+=(+@sSteamCmdForcePlatformType windows)
steamcmd+=(+force_install_dir "$PALWORLD_INSTALL_DIR" +login anonymous +app_update 2394010 validate +quit)
update_rc=0
if (( EUID == 0 )); then
    runuser -u "$PALWORLD_USER" -- env HOME="$PALWORLD_STATE_DIR" "${steamcmd[@]}" || update_rc=$?
else
    "${steamcmd[@]}" || update_rc=$?
fi
if (( update_rc == 0 )); then render_settings || update_rc=$?; fi
if (( update_rc != 0 )) && [[ -n "$snapshot_build" ]]; then
    "$SCRIPT_DIR/steam-build-manager.py" restore "$snapshot_build" --confirm "ROLLBACK BUILD"
    render_settings
    ops_event audit update executable-rollback --details "restored build $snapshot_build after rc=$update_rc"
fi
exit "$update_rc"
