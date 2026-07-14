#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/palworld-common.sh"
render_settings
[[ "${1:-}" == --render-only ]] && exit 0
cd "$PALWORLD_INSTALL_DIR"
exec ./PalServer.sh -port="$PALWORLD_PORT" -publiclobby -useperfthreads -NoAsyncLoadingThread -UseMultithreadForDS
