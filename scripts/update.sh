#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/palworld-common.sh"
"$PALWORLD_STEAMCMD" +force_install_dir "$PALWORLD_INSTALL_DIR" +login anonymous +app_update 2394010 validate +quit
render_settings
