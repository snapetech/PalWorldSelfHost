#!/usr/bin/env bash
set -euo pipefail
[[ $(id -u) -eq 0 ]] || { echo "run as root" >&2; exit 1; }

action=${1:-}
confirmation=${2:-}
case "$action" in
  enable) expected="ENABLE PRIVATE RCON"; desired=true ;;
  disable) expected="DISABLE PRIVATE RCON"; desired=false ;;
  *) echo "usage: $0 enable|disable 'EXACT CONFIRMATION'" >&2; exit 2 ;;
esac
[[ "$confirmation" == "$expected" ]] || { echo "exact confirmation $expected is required" >&2; exit 2; }

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
source "$SCRIPT_DIR/palworld-common.sh"
acquire_mutation_lock
export PALWORLD_LOCK_HELD=true

roster=$("$SCRIPT_DIR/rest-client.py" players)
online=$(python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("players", [])))' <<<"$roster")
[[ "$online" == 0 ]] || { echo "refusing RCON transition while $online player(s) are online" >&2; exit 1; }

mkdir -p "$PALWORLD_STATE_DIR/rcon-env-snapshots"
snapshot="$PALWORLD_STATE_DIR/rcon-env-snapshots/env-$(date -u +%Y%m%dT%H%M%SZ)"
install -o root -g "$PALWORLD_GROUP" -m 0640 "$ENV_FILE" "$snapshot"

rewrite_env() {
  local value=$1 temporary
  temporary=$(mktemp "${ENV_FILE}.rcon.XXXXXX")
  awk -v value="$value" '
    BEGIN { found=0 }
    /^PALWORLD_RCON_ENABLED=/ { print "PALWORLD_RCON_ENABLED=" value; found=1; next }
    { print }
    END { if (!found) print "PALWORLD_RCON_ENABLED=" value }
  ' "$ENV_FILE" >"$temporary"
  install -o root -g "$PALWORLD_GROUP" -m 0640 "$temporary" "$ENV_FILE"
  rm -f "$temporary"
}

rollback() {
  install -o root -g "$PALWORLD_GROUP" -m 0640 "$snapshot" "$ENV_FILE"
  systemctl reset-failed palworld.service || true
  systemctl restart palworld.service || true
  "$SCRIPT_DIR/ops-event.py" audit rcon-toggle failed --details="automatic environment rollback applied"
}
trap rollback ERR

if [[ "$action" == enable ]]; then
  systemctl start palworld-rcon-firewall.service
  "$SCRIPT_DIR/rcon-firewall.py" check >/dev/null
fi
"$SCRIPT_DIR/backup.sh" protected >/dev/null
rewrite_env "$desired"
systemctl reset-failed palworld.service
systemctl restart palworld.service

for _ in {1..60}; do
  "$SCRIPT_DIR/rest-client.py" info >/dev/null 2>&1 && break
  sleep 2
done
"$SCRIPT_DIR/rest-client.py" info >/dev/null
if [[ "$action" == enable ]]; then
  rcon_result=$("$SCRIPT_DIR/rcon-client.py" Info) || { echo "$rcon_result" >&2; false; }
else
  ! "$SCRIPT_DIR/rcon-client.py" Info >/dev/null 2>&1 || { echo "RCON still accepted commands after disable" >&2; false; }
fi
trap - ERR
"$SCRIPT_DIR/ops-event.py" audit rcon-toggle ok --details="$action"
printf '{"ok":true,"enabled":%s,"snapshot":"%s"}\n' "$desired" "$(basename "$snapshot")"
