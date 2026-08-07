#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/palworld-common.sh"
name=${1:?usage: ops-restore.sh BACKUP_NAME [restore options]}
shift
[[ "$name" =~ ^palworld-[A-Za-z0-9_.-]+\.tar\.zst$ && "$name" != */* ]] || {
    echo "invalid managed backup name" >&2
    exit 2
}
archive="$PALWORLD_BACKUP_LOCAL_ROOT/$name"
[[ -f "$archive" ]] || { echo "managed backup does not exist: $name" >&2; exit 1; }
exec "$SCRIPT_DIR/restore.py" "$archive" "$@"
