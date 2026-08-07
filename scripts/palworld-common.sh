#!/usr/bin/env bash
set -euo pipefail

ENV_FILE=${PALWORLD_ENV_FILE:-/etc/palworld-server.env}
[[ -r "$ENV_FILE" ]] || { echo "missing $ENV_FILE" >&2; exit 1; }
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${PALWORLD_INSTALL_DIR:?}"
: "${PALWORLD_USER:=palworld}"
: "${PALWORLD_GROUP:=palworld}"
: "${PALWORLD_STEAMCMD:=/usr/local/bin/steamcmd}"
: "${PALWORLD_STATE_DIR:?}"
: "${PALWORLD_RUNTIME:=native-linux}"
: "${PALWORLD_WINE_PREFIX:=$PALWORLD_STATE_DIR/wine-prefix}"
: "${PALWORLD_BACKUP_LOCAL_ROOT:?}"
: "${PALWORLD_PORT:=8211}"
: "${PALWORLD_PUBLIC_IP:=}"
: "${PALWORLD_BIND_IP:=}"
: "${PALWORLD_SERVER_NAME:?}"
: "${PALWORLD_SERVER_DESCRIPTION:=Palworld community server}"
: "${PALWORLD_PLAYER_EXP_RATE:=0.5}"
: "${PALWORLD_ADMIN_PASSWORD:?}"
: "${PALWORLD_REST_PORT:=8212}"
: "${PALWORLD_RCON_ENABLED:=false}"
: "${PALWORLD_RCON_PORT:=25575}"

case "$PALWORLD_RUNTIME" in
    native-linux) PALWORLD_CONFIG_PLATFORM=LinuxServer ;;
    wine-windows) PALWORLD_CONFIG_PLATFORM=WindowsServer ;;
    *) echo "PALWORLD_RUNTIME must be native-linux or wine-windows" >&2; exit 1 ;;
esac
export PALWORLD_RUNTIME PALWORLD_WINE_PREFIX PALWORLD_CONFIG_PLATFORM

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SETTINGS="$PALWORLD_INSTALL_DIR/Pal/Saved/Config/$PALWORLD_CONFIG_PLATFORM/PalWorldSettings.ini"

acquire_mutation_lock() {
    [[ "${PALWORLD_LOCK_HELD:-false}" == true ]] && return 0
    mkdir -p "$PALWORLD_STATE_DIR"
    exec 9>"$PALWORLD_STATE_DIR/mutation.lock"
    flock -n 9 || { echo "another PalWorldSelfHost mutation is already running" >&2; exit 75; }
    export PALWORLD_LOCK_HELD=true
}

ops_event() { "$SCRIPT_DIR/ops-event.py" "$@"; }

render_settings() {
    local args=(
        "$PALWORLD_INSTALL_DIR/DefaultPalWorldSettings.ini" "$SETTINGS" \
        --name "$PALWORLD_SERVER_NAME" --description "$PALWORLD_SERVER_DESCRIPTION" --port "$PALWORLD_PORT" \
        --public-ip "$PALWORLD_PUBLIC_IP" \
        --player-exp "$PALWORLD_PLAYER_EXP_RATE" \
        --admin-password "$PALWORLD_ADMIN_PASSWORD" --rest-port "$PALWORLD_REST_PORT" \
        --rcon-port "$PALWORLD_RCON_PORT" \
        --overrides "$PALWORLD_STATE_DIR/settings-overrides.json" \
        --raw-base "$PALWORLD_STATE_DIR/settings-raw.ini"
    )
    [[ "$PALWORLD_RCON_ENABLED" != true ]] || args+=(--rcon-enabled)
    "$SCRIPT_DIR/render-settings.py" "${args[@]}"
}
