#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/palworld-common.sh"
: "${PALWORLD_RCLONE_DEST:?}"
: "${PALWORLD_BACKUP_RETENTION_DAYS:=14}"

if [[ "${PALWORLD_REQUIRE_LVM_BACKUP:-false}" == true ]]; then
    source_name=$(findmnt -n -o SOURCE --target "$PALWORLD_BACKUP_LOCAL_ROOT")
    [[ "$source_name" == /dev/mapper/* || "$source_name" == /dev/*-* ]] || {
        echo "$PALWORLD_BACKUP_LOCAL_ROOT is not backed by an identifiable LVM logical volume ($source_name)" >&2
        exit 1
    }
fi

stamp=$(date -u +%Y%m%dT%H%M%SZ)
archive="$PALWORLD_BACKUP_LOCAL_ROOT/palworld-$stamp.tar.zst"
mkdir -p "$PALWORLD_BACKUP_LOCAL_ROOT"
if systemctl is-active --quiet palworld.service; then
    "$SCRIPT_DIR/rest-client.py" save >/dev/null || {
        echo "refusing live backup because the world save request failed" >&2
        exit 1
    }
fi
tar --zstd -C "$PALWORLD_INSTALL_DIR" -cf "$archive" Pal/Saved DefaultPalWorldSettings.ini
sha256sum "$archive" > "$archive.sha256"
"$SCRIPT_DIR/verify-backup.sh" "$archive"
rclone copyto "$archive" "$PALWORLD_RCLONE_DEST/$(basename "$archive")" --immutable
rclone copyto "$archive.sha256" "$PALWORLD_RCLONE_DEST/$(basename "$archive.sha256")" --immutable
find "$PALWORLD_BACKUP_LOCAL_ROOT" -maxdepth 1 -type f \
    \( -name 'palworld-*.tar.zst' -o -name 'palworld-*.tar.zst.sha256' \) \
    -mtime "+$PALWORLD_BACKUP_RETENTION_DAYS" -delete
