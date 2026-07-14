#!/usr/bin/env bash
set -euo pipefail
[[ $(id -u) -eq 0 ]] || { echo "run as root" >&2; exit 1; }
repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
stamp=$(date -u +%Y%m%dT%H%M%SZ); mkdir -p /var/lib/palworld/migrations
cp -a /etc/palworld-server.env "/var/lib/palworld/migrations/env-$stamp" 
PALWORLD_SKIP_UPDATE=true "$repo/scripts/install.sh"
echo "$stamp $(git -C "$repo" rev-parse HEAD 2>/dev/null || echo unknown)" > /var/lib/palworld/version
