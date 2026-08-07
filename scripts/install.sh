#!/usr/bin/env bash
set -euo pipefail
[[ $(id -u) -eq 0 ]] || { echo "run as root" >&2; exit 1; }

adopt_existing=false
adoption_fingerprint=
adoption_confirmation=
while (($#)); do
    case "$1" in
        --adopt-existing) adopt_existing=true; shift ;;
        --adoption-fingerprint) [[ $# -ge 2 ]] || { echo "--adoption-fingerprint requires a value" >&2; exit 2; }; adoption_fingerprint=$2; shift 2 ;;
        --confirm) [[ $# -ge 2 ]] || { echo "--confirm requires a value" >&2; exit 2; }; adoption_confirmation=$2; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done
if [[ "$adopt_existing" == true ]]; then
    [[ "$adoption_fingerprint" =~ ^[0-9a-f]{64}$ ]] || { echo "adoption requires the reviewed 64-character fingerprint" >&2; exit 2; }
    [[ "$adoption_confirmation" == "ADOPT EXISTING INSTALL" ]] || { echo "adoption requires --confirm 'ADOPT EXISTING INSTALL'" >&2; exit 2; }
fi

repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
env_file=${PALWORLD_ENV_FILE:-/etc/palworld-server.env}
[[ -r "$env_file" ]] || { echo "create $env_file from config/palworld-server.env.example" >&2; exit 1; }
set -a
# shellcheck disable=SC1090
source "$env_file"
set +a
: "${PALWORLD_INSTALL_DIR:?}"
: "${PALWORLD_STATE_DIR:=/var/lib/palworld}"
: "${PALWORLD_BACKUP_LOCAL_ROOT:?}"
: "${PALWORLD_RCLONE_DEST:?}"
: "${PALWORLD_USER:=palworld}"
: "${PALWORLD_GROUP:=palworld}"
: "${PALWORLD_PUBLIC_DIR:=/srv/static/palworld}"
: "${PALWORLD_REQUIRE_LVM_BACKUP:=false}"

[[ -z "${PALWORLD_EXPECTED_HOSTNAME:-}" || "$(hostname)" == "$PALWORLD_EXPECTED_HOSTNAME" ]] || {
    echo "refusing install on $(hostname), expected $PALWORLD_EXPECTED_HOSTNAME" >&2
    exit 1
}
[[ "$PALWORLD_USER" == palworld && "$PALWORLD_GROUP" == palworld ]] || {
    echo "the packaged systemd units require PALWORLD_USER=palworld and PALWORLD_GROUP=palworld" >&2
    exit 1
}

getent group "$PALWORLD_GROUP" >/dev/null || groupadd --system "$PALWORLD_GROUP"
id "$PALWORLD_USER" >/dev/null 2>&1 || useradd --system --gid "$PALWORLD_GROUP" --home-dir /var/lib/palworld --create-home --shell /usr/sbin/nologin "$PALWORLD_USER"
chown root:"$PALWORLD_GROUP" "$env_file"
chmod 0640 "$env_file"
install -d -o "$PALWORLD_USER" -g "$PALWORLD_GROUP" "$PALWORLD_INSTALL_DIR" "$PALWORLD_BACKUP_LOCAL_ROOT" "$PALWORLD_STATE_DIR"
chown root:"$PALWORLD_GROUP" "$PALWORLD_STATE_DIR"; chmod 2770 "$PALWORLD_STATE_DIR"
if [[ "$adopt_existing" == true ]]; then
    if [[ -e "$PALWORLD_STATE_DIR/adoption-receipt.json" ]]; then
        python3 "$repo/scripts/adopt-existing.py" verify --install-dir "$PALWORLD_INSTALL_DIR" --state-dir "$PALWORLD_STATE_DIR" \
            --fingerprint "$adoption_fingerprint" >/dev/null
    else
        python3 "$repo/scripts/adopt-existing.py" adopt --install-dir "$PALWORLD_INSTALL_DIR" --state-dir "$PALWORLD_STATE_DIR" \
            --fingerprint "$adoption_fingerprint" --confirm "$adoption_confirmation" >/dev/null
    fi
    manifest=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["files"]["appmanifest"]["path"])' "$PALWORLD_STATE_DIR/adoption-receipt.json")
    chown -R "$PALWORLD_USER:$PALWORLD_GROUP" "$PALWORLD_INSTALL_DIR"
    chown "$PALWORLD_USER:$PALWORLD_GROUP" "$manifest" "$(dirname "$manifest")"
fi
findmnt --target "$PALWORLD_BACKUP_LOCAL_ROOT" >/dev/null
if [[ "$PALWORLD_REQUIRE_LVM_BACKUP" == true ]]; then
    source_name=$(findmnt -n -o SOURCE --target "$PALWORLD_BACKUP_LOCAL_ROOT")
    [[ "$source_name" == /dev/mapper/* || "$source_name" == /dev/*-* ]] || {
        echo "$PALWORLD_BACKUP_LOCAL_ROOT is not on an identifiable LVM logical volume" >&2
        exit 1
    }
fi
if ! timeout 30 rclone lsd "$PALWORLD_RCLONE_DEST" >/dev/null; then
    echo "warning: rclone destination preflight failed; local deployment will continue and offsite backup will retry during maintenance" >&2
fi

install -d -m 0755 /usr/local/lib/palworld
install -m 0755 "$repo/scripts/"*.sh "$repo/scripts/render-settings.py" /usr/local/lib/palworld/
install -m 0755 "$repo/scripts/"*.py /usr/local/lib/palworld/
install -m 0755 "$repo/scripts/palworldctl" /usr/local/bin/palworldctl
install -d -m 0755 /usr/local/share/palworldselfhost/deploy/wine-modded/PalWorldSelfHostRelay/Scripts
install -m 0644 "$repo/deploy/wine-modded/PalWorldSelfHostRelay/Scripts/main.lua" /usr/local/share/palworldselfhost/deploy/wine-modded/PalWorldSelfHostRelay/Scripts/main.lua
install -m 0644 "$repo/deploy/wine-modded/PalWorldSelfHostRelay/enabled.txt" /usr/local/share/palworldselfhost/deploy/wine-modded/PalWorldSelfHostRelay/enabled.txt
install -d -m 0755 /usr/local/share/palworld-ops/static
install -m 0755 "$repo/admin/ops-server.py" /usr/local/share/palworld-ops/ops-server.py
install -m 0644 "$repo/admin/static/"* /usr/local/share/palworld-ops/static/
install -d -m 0755 /usr/local/share/palworld-bootstrap/static
install -m 0644 "$repo/bootstrap/static/"* /usr/local/share/palworld-bootstrap/static/
install -d -o "$PALWORLD_USER" -g "$PALWORLD_GROUP" -m 0755 "$PALWORLD_PUBLIC_DIR"
install -m 0644 "$repo/public/"* "$PALWORLD_PUBLIC_DIR/"
install -m 0644 "$repo/systemd/palworld.service" /etc/systemd/system/palworld.service
install -m 0644 "$repo/systemd/palworld-maintenance.service" /etc/systemd/system/palworld-maintenance.service
install -m 0644 "$repo/systemd/palworld-maintenance.timer" /etc/systemd/system/palworld-maintenance.timer
install -m 0644 "$repo/systemd/palworld-ops.service" /etc/systemd/system/palworld-ops.service
install -m 0644 "$repo/systemd/palworld-health.service" /etc/systemd/system/palworld-health.service
install -m 0644 "$repo/systemd/palworld-health.timer" /etc/systemd/system/palworld-health.timer
install -m 0644 "$repo/systemd/palworld-exposure.service" /etc/systemd/system/palworld-exposure.service
install -m 0644 "$repo/systemd/palworld-exposure.timer" /etc/systemd/system/palworld-exposure.timer
install -m 0644 "$repo/systemd/palworld-update-check.service" /etc/systemd/system/palworld-update-check.service
install -m 0644 "$repo/systemd/palworld-update-check.timer" /etc/systemd/system/palworld-update-check.timer
install -m 0644 "$repo/systemd/palworld-restore-drill.service" /etc/systemd/system/palworld-restore-drill.service
install -m 0644 "$repo/systemd/palworld-restore-drill.timer" /etc/systemd/system/palworld-restore-drill.timer
install -m 0644 "$repo/systemd/palworld-scheduler.service" /etc/systemd/system/palworld-scheduler.service
install -m 0644 "$repo/systemd/palworld-scheduler.timer" /etc/systemd/system/palworld-scheduler.timer
install -m 0644 "$repo/systemd/palworld-history.service" /etc/systemd/system/palworld-history.service
install -m 0644 "$repo/systemd/palworld-history.timer" /etc/systemd/system/palworld-history.timer
install -m 0644 "$repo/systemd/palworld-save-intelligence.service" /etc/systemd/system/palworld-save-intelligence.service
install -m 0644 "$repo/systemd/palworld-save-intelligence.timer" /etc/systemd/system/palworld-save-intelligence.timer
install -m 0644 "$repo/systemd/palworld-auto-pause.service" /etc/systemd/system/palworld-auto-pause.service
install -m 0644 "$repo/systemd/palworld-auto-pause.timer" /etc/systemd/system/palworld-auto-pause.timer
install -m 0644 "$repo/systemd/palworld-game-data.service" /etc/systemd/system/palworld-game-data.service
install -m 0644 "$repo/systemd/palworld-game-data.timer" /etc/systemd/system/palworld-game-data.timer
install -m 0644 "$repo/systemd/palworld-rcon-firewall.service" /etc/systemd/system/palworld-rcon-firewall.service
install -m 0644 "$repo/systemd/palworld-hairpin-nat.service" /etc/systemd/system/palworld-hairpin-nat.service
install -m 0644 "$repo/systemd/palworld-whitelist.service" /etc/systemd/system/palworld-whitelist.service
install -m 0644 "$repo/systemd/palworld-whitelist.timer" /etc/systemd/system/palworld-whitelist.timer
install -m 0644 "$repo/systemd/palworld-backup-"* /etc/systemd/system/
install -m 0644 "$repo/systemd/palworld-bot.service" /etc/systemd/system/palworld-bot.service
install -m 0644 "$repo/systemd/palworld-game-hook.service" /etc/systemd/system/palworld-game-hook.service
install -m 0644 "$repo/systemd/palworld-public-status.service" /etc/systemd/system/palworld-public-status.service
install -m 0644 "$repo/systemd/palworld-public-status.timer" /etc/systemd/system/palworld-public-status.timer
install -o root -g root -m 0440 "$repo/config/palworld-ops.sudoers" /etc/sudoers.d/palworld-ops

if [[ "$adopt_existing" == true ]]; then
    : # The reviewed existing game tree must not be updated or rendered before service handoff.
elif [[ "${PALWORLD_SKIP_UPDATE:-false}" != true ]]; then
    sudo -u "$PALWORLD_USER" env HOME="$(getent passwd "$PALWORLD_USER" | cut -d: -f6)" PALWORLD_ENV_FILE="$env_file" bash -c 'set -a; source "$PALWORLD_ENV_FILE"; set +a; /usr/local/lib/palworld/preflight.py'
    sudo -u "$PALWORLD_USER" env HOME="$(getent passwd "$PALWORLD_USER" | cut -d: -f6)" PALWORLD_ENV_FILE="$env_file" /usr/local/lib/palworld/update.sh --force
else
    sudo -u "$PALWORLD_USER" env HOME="$(getent passwd "$PALWORLD_USER" | cut -d: -f6)" PALWORLD_ENV_FILE="$env_file" /usr/local/lib/palworld/start.sh --render-only 2>/dev/null || true
fi
install -o root -g "$PALWORLD_GROUP" -m 0640 "$env_file" "$PALWORLD_STATE_DIR/env-known-good"
systemctl daemon-reload
systemctl enable --now palworld-rcon-firewall.service palworld-hairpin-nat.service palworld.service palworld-maintenance.timer palworld-ops.service palworld-health.timer palworld-exposure.timer palworld-update-check.timer palworld-restore-drill.timer palworld-scheduler.timer palworld-history.timer palworld-save-intelligence.timer palworld-auto-pause.timer palworld-game-data.timer palworld-whitelist.timer palworld-backup-hourly.timer palworld-backup-weekly.timer palworld-public-status.timer
if [[ -n "${PALWORLD_MATRIX_ACCESS_TOKEN:-}" || -n "${PALWORLD_DISCORD_BOT_TOKEN:-}" ]]; then
    systemctl enable --now palworld-bot.service
fi
if [[ "${PALWORLD_RUNTIME:-native-linux}" == wine-windows && "${PALWORLD_CHAT_RELAY_ENABLED:-false}" == true && -n "${PALWORLD_DISCORD_BOT_TOKEN:-}" ]]; then
    systemctl enable --now palworld-game-hook.service
fi
