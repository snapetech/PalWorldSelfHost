#!/usr/bin/env bash
set -euo pipefail

ENV_FILE=${PALWORLD_ENV_FILE:-/etc/palworld-server.env}
[[ -r "$ENV_FILE" ]] || { echo "missing $ENV_FILE" >&2; exit 1; }
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${PALWORLD_INSTALL_DIR:?}"
: "${PALWORLD_STEAMCMD:=/usr/local/bin/steamcmd}"
: "${PALWORLD_BACKUP_LOCAL_ROOT:?}"
: "${PALWORLD_PORT:=8211}"
: "${PALWORLD_SERVER_NAME:?}"
: "${PALWORLD_SERVER_DESCRIPTION:=Palworld community server}"
: "${PALWORLD_PLAYER_EXP_RATE:=0.5}"
: "${PALWORLD_ADMIN_PASSWORD:?}"
: "${PALWORLD_REST_PORT:=8212}"

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SETTINGS="$PALWORLD_INSTALL_DIR/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"

acquire_mutation_lock() {
    [[ "${PALWORLD_LOCK_HELD:-false}" == true ]] && return 0
    mkdir -p "$PALWORLD_STATE_DIR"
    exec 9>"$PALWORLD_STATE_DIR/mutation.lock"
    flock -n 9 || { echo "another PalWorldSelfHost mutation is already running" >&2; exit 75; }
    export PALWORLD_LOCK_HELD=true
}

ops_event() { "$SCRIPT_DIR/ops-event.py" "$@"; }

render_settings() {
    "$SCRIPT_DIR/render-settings.py" \
        "$PALWORLD_INSTALL_DIR/DefaultPalWorldSettings.ini" "$SETTINGS" \
        --name "$PALWORLD_SERVER_NAME" --description "$PALWORLD_SERVER_DESCRIPTION" --port "$PALWORLD_PORT" \
        --player-exp "$PALWORLD_PLAYER_EXP_RATE" \
        --admin-password "$PALWORLD_ADMIN_PASSWORD" --rest-port "$PALWORLD_REST_PORT" \
        --overrides "$PALWORLD_STATE_DIR/settings-overrides.json"
}
