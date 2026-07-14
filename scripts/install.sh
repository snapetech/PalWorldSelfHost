#!/usr/bin/env bash
set -euo pipefail
[[ $(id -u) -eq 0 ]] || { echo "run as root" >&2; exit 1; }

repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
env_file=/etc/palworld-server.env
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
: "${PALWORLD_PUBLIC_DIR:=/var/www/palworld}"
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
install -d -m 0755 /usr/local/share/palworld-ops/static
install -m 0755 "$repo/admin/ops-server.py" /usr/local/share/palworld-ops/ops-server.py
install -m 0644 "$repo/admin/static/"* /usr/local/share/palworld-ops/static/
install -d -m 0755 "$PALWORLD_PUBLIC_DIR"
install -m 0644 "$repo/public/"* "$PALWORLD_PUBLIC_DIR/"
install -m 0644 "$repo/systemd/palworld.service" /etc/systemd/system/palworld.service
install -m 0644 "$repo/systemd/palworld-maintenance.service" /etc/systemd/system/palworld-maintenance.service
install -m 0644 "$repo/systemd/palworld-maintenance.timer" /etc/systemd/system/palworld-maintenance.timer
install -m 0644 "$repo/systemd/palworld-ops.service" /etc/systemd/system/palworld-ops.service
install -m 0644 "$repo/systemd/palworld-health.service" /etc/systemd/system/palworld-health.service
install -m 0644 "$repo/systemd/palworld-health.timer" /etc/systemd/system/palworld-health.timer
install -m 0644 "$repo/systemd/palworld-update-check.service" /etc/systemd/system/palworld-update-check.service
install -m 0644 "$repo/systemd/palworld-update-check.timer" /etc/systemd/system/palworld-update-check.timer
install -m 0644 "$repo/systemd/palworld-restore-drill.service" /etc/systemd/system/palworld-restore-drill.service
install -m 0644 "$repo/systemd/palworld-restore-drill.timer" /etc/systemd/system/palworld-restore-drill.timer
install -m 0644 "$repo/systemd/palworld-scheduler.service" /etc/systemd/system/palworld-scheduler.service
install -m 0644 "$repo/systemd/palworld-scheduler.timer" /etc/systemd/system/palworld-scheduler.timer
install -m 0644 "$repo/systemd/palworld-history.service" /etc/systemd/system/palworld-history.service
install -m 0644 "$repo/systemd/palworld-history.timer" /etc/systemd/system/palworld-history.timer
install -m 0644 "$repo/systemd/palworld-backup-"* /etc/systemd/system/
install -m 0644 "$repo/systemd/palworld-bot.service" /etc/systemd/system/palworld-bot.service
install -o root -g root -m 0440 "$repo/config/palworld-ops.sudoers" /etc/sudoers.d/palworld-ops

if [[ "${PALWORLD_SKIP_UPDATE:-false}" != true ]]; then
    sudo -u "$PALWORLD_USER" env HOME="$(getent passwd "$PALWORLD_USER" | cut -d: -f6)" PALWORLD_ENV_FILE="$env_file" bash -c 'set -a; source "$PALWORLD_ENV_FILE"; set +a; /usr/local/lib/palworld/preflight.py'
    sudo -u "$PALWORLD_USER" env HOME="$(getent passwd "$PALWORLD_USER" | cut -d: -f6)" PALWORLD_ENV_FILE="$env_file" /usr/local/lib/palworld/update.sh --force
else
    sudo -u "$PALWORLD_USER" env HOME="$(getent passwd "$PALWORLD_USER" | cut -d: -f6)" PALWORLD_ENV_FILE="$env_file" /usr/local/lib/palworld/start.sh --render-only 2>/dev/null || true
fi
systemctl daemon-reload
systemctl enable --now palworld.service palworld-maintenance.timer palworld-ops.service palworld-health.timer palworld-update-check.timer palworld-restore-drill.timer palworld-scheduler.timer palworld-history.timer palworld-backup-hourly.timer palworld-backup-weekly.timer
if [[ -n "${PALWORLD_MATRIX_ACCESS_TOKEN:-}" ]]; then systemctl enable --now palworld-bot.service; fi
