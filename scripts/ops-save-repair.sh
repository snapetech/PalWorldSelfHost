#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/palworld-common.sh"

command_name="${1:-}"
payload="${2:-}"
save_root="$PALWORLD_INSTALL_DIR/Pal/Saved/SaveGames/0"
mapfile -t worlds < <(find "$save_root" -mindepth 2 -maxdepth 2 -type f -name Level.sav -printf '%h\n' 2>/dev/null | sort -u)
if (( ${#worlds[@]} == 0 )); then
  echo '{"status":"blocked","error":"save repair found no managed world"}'
  exit 2
fi
if (( ${#worlds[@]} == 1 )); then
  world="${worlds[0]}"
else
  settings="$PALWORLD_INSTALL_DIR/Pal/Saved/Config/$PALWORLD_CONFIG_PLATFORM/GameUserSettings.ini"
  dedicated_name=""
  if [[ -f "$settings" && ! -L "$settings" ]]; then
    dedicated_name="$(sed -n 's/^DedicatedServerName=\([0-9A-Fa-f]\{32\}\)[[:space:]]*$/\1/p' "$settings" | tail -n 1)"
  fi
  matches=()
  for candidate in "${worlds[@]}"; do
    candidate_name="${candidate##*/}"
    if [[ "${candidate_name,,}" == "${dedicated_name,,}" ]]; then
      matches+=("$candidate")
    fi
  done
  if [[ -z "$dedicated_name" || ${#matches[@]} -ne 1 ]]; then
    echo '{"status":"blocked","error":"multiple worlds exist and DedicatedServerName does not select exactly one target"}'
    exit 2
  fi
  world="${matches[0]}"
fi

case "$command_name" in
  diagnose)
    [[ $# -eq 1 ]] || { echo "usage: $0 diagnose" >&2; exit 2; }
    exec "$(dirname "$0")/save-repair.py" diagnose --world "$world"
    ;;
  plan)
    [[ $# -eq 2 ]] || { echo "usage: $0 plan PAYLOAD_JSON" >&2; exit 2; }
    if systemctl is-active --quiet palworld.service; then
      roster="$("$(dirname "$0")/rest-client.py" players)"
      ROSTER="$roster" python3 - <<'PY'
import json, os, sys
try:
    players = json.loads(os.environ["ROSTER"]).get("players")
except (json.JSONDecodeError, AttributeError):
    raise SystemExit("player roster was not valid JSON")
if not isinstance(players, list):
    raise SystemExit("player roster did not contain a players array")
if players:
    raise SystemExit("online players must be zero before planning an offline save mutation")
PY
      "$(dirname "$0")/rest-client.py" save >/dev/null
    fi
    exec "$(dirname "$0")/save-repair.py" plan "$payload" --world "$world" --install-dir "$PALWORLD_INSTALL_DIR"
    ;;
  apply)
    [[ $# -eq 4 ]] || { echo "usage: $0 apply PAYLOAD_JSON REVIEWED_SHA256 CONFIRMATION" >&2; exit 2; }
    exec "$(dirname "$0")/save-repair.py" apply "$payload" --world "$world" \
      --install-dir "$PALWORLD_INSTALL_DIR" --state-dir "$PALWORLD_STATE_DIR" \
      --reviewed-sha256 "$3" --confirm "$4"
    ;;
  *)
    echo "usage: $0 diagnose | plan PAYLOAD_JSON | apply PAYLOAD_JSON REVIEWED_SHA256 CONFIRMATION" >&2
    exit 2
    ;;
esac
