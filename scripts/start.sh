#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/palworld-common.sh"
render_settings
"$SCRIPT_DIR/server-config-manager.py" render >/dev/null
[[ "${1:-}" == --render-only ]] && exit 0
cd "$PALWORLD_INSTALL_DIR"
args=(-port="$PALWORLD_PORT" -publicport="$PALWORLD_PORT")
mapfile -t configured_args < <("$SCRIPT_DIR/server-config-manager.py" launch-args)
args+=("${configured_args[@]}")
[[ -z "$PALWORLD_PUBLIC_IP" ]] || args+=(-publicip="$PALWORLD_PUBLIC_IP")
[[ -z "$PALWORLD_BIND_IP" ]] || args+=(-ip="$PALWORLD_BIND_IP")
[[ "$PALWORLD_RCON_ENABLED" != true ]] || args+=(-RCONPort="$PALWORLD_RCON_PORT")
if [[ "$PALWORLD_RUNTIME" == native-linux ]]; then
    exec ./PalServer.sh "${args[@]}"
fi

[[ -f ./PalServer.exe ]] || { echo "wine-windows runtime requires $PALWORLD_INSTALL_DIR/PalServer.exe" >&2; exit 1; }
command -v wine >/dev/null || { echo "wine-windows runtime requires wine" >&2; exit 1; }
command -v xvfb-run >/dev/null || { echo "wine-windows runtime requires xvfb-run" >&2; exit 1; }
mkdir -p "$PALWORLD_WINE_PREFIX"
export WINEPREFIX="$PALWORLD_WINE_PREFIX" WINEARCH=win64 WINEDEBUG="${PALWORLD_WINEDEBUG:--all}"
# PalDefender and UE4SS use separate reviewed proxy DLLs. Native-first is
# required for Wine to load them; absent files safely fall through to builtins.
export WINEDLLOVERRIDES="${PALWORLD_WINE_DLL_OVERRIDES:-d3d9=n,b;dwmapi=n,b}"
exec xvfb-run -a wine ./PalServer.exe -RenderOffScreen "${args[@]}"
