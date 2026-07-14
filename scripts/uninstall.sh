#!/usr/bin/env bash
set -euo pipefail
[[ $(id -u) -eq 0 ]] || { echo "run as root" >&2; exit 1; }
[[ ${1:-} == --confirm-remove-services ]] || { echo "This preserves worlds, backups, config and state. Re-run with --confirm-remove-services"; exit 2; }
systemctl disable --now 'palworld*.timer' palworld.service palworld-ops.service 2>/dev/null || true
rm -f /etc/systemd/system/palworld*.service /etc/systemd/system/palworld*.timer /etc/sudoers.d/palworld-ops /usr/local/bin/palworldctl
rm -rf /usr/local/lib/palworld /usr/local/share/palworld-ops
systemctl daemon-reload
echo "Services removed. World, backups, /etc/palworld-server.env, public assets, and state were preserved."
