#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/palworld-common.sh"
: "${PALWORLD_RCLONE_DEST:?}"
: "${PALWORLD_BACKUP_RETENTION_DAYS:=14}"
mutation_lock_was_held="${PALWORLD_LOCK_HELD:-false}"
acquire_mutation_lock
tier=${1:-daily}
[[ "$tier" =~ ^(hourly|daily|weekly|protected)$ ]] || { echo "invalid backup tier: $tier" >&2; exit 2; }

if [[ "${PALWORLD_REQUIRE_LVM_BACKUP:-false}" == true ]]; then
    source_name=$(findmnt -n -o SOURCE --target "$PALWORLD_BACKUP_LOCAL_ROOT")
    [[ "$source_name" == /dev/mapper/* || "$source_name" == /dev/*-* ]] || {
        echo "$PALWORLD_BACKUP_LOCAL_ROOT is not backed by an identifiable LVM logical volume ($source_name)" >&2
        exit 1
    }
fi

stamp=$(date -u +%Y%m%dT%H%M%SZ)
archive="$PALWORLD_BACKUP_LOCAL_ROOT/palworld-$tier-$stamp.tar.zst"
mkdir -p "$PALWORLD_BACKUP_LOCAL_ROOT"
if systemctl is-active --quiet palworld.service; then
    if [[ "${PALWORLD_BACKUP_ALREADY_SAVED:-false}" == true ]]; then
        [[ "$mutation_lock_was_held" == true ]] || {
            echo "PALWORLD_BACKUP_ALREADY_SAVED requires the held mutation lock" >&2
            exit 1
        }
    else
        "$SCRIPT_DIR/rest-client.py" save >/dev/null || {
            echo "refusing live backup because the world save request failed" >&2
            exit 1
        }
    fi
fi
staging=$(mktemp -d "$PALWORLD_BACKUP_LOCAL_ROOT/.backup-staging-XXXXXX")
staged_archive="$staging/$(basename "$archive")"
trap 'rm -rf "$staging"' EXIT
tar --zstd -C "$PALWORLD_INSTALL_DIR" -cf "$staged_archive" Pal/Saved DefaultPalWorldSettings.ini
read -r digest _ < <(sha256sum "$staged_archive")
printf '%s  %s\n' "$digest" "$(basename "$archive")" > "$staged_archive.sha256"
"$SCRIPT_DIR/verify-backup.sh" "$staged_archive"
"$SCRIPT_DIR/backup-manifest.py" "$staged_archive" "$tier" > "$staged_archive.manifest.json"
chmod 0640 "$staged_archive" "$staged_archive.sha256" "$staged_archive.manifest.json"
if [[ $(id -u) -eq 0 ]]; then
    chown "$PALWORLD_USER:$PALWORLD_GROUP" "$staged_archive" "$staged_archive.sha256" "$staged_archive.manifest.json"
fi
# Publish sidecars first and the archive last so inventory/download clients can
# never observe a partially written archive.
mv "$staged_archive.sha256" "$archive.sha256"
mv "$staged_archive.manifest.json" "$archive.manifest.json"
mv "$staged_archive" "$archive"
rmdir "$staging"
trap - EXIT
offsite_rc=0
rclone copyto "$archive" "$PALWORLD_RCLONE_DEST/$(basename "$archive")" --immutable || offsite_rc=$?
if (( offsite_rc == 0 )); then rclone copyto "$archive.sha256" "$PALWORLD_RCLONE_DEST/$(basename "$archive.sha256")" --immutable || offsite_rc=$?; fi
if (( offsite_rc == 0 )); then rclone copyto "$archive.manifest.json" "$PALWORLD_RCLONE_DEST/$(basename "$archive.manifest.json")" --immutable || offsite_rc=$?; fi
"$SCRIPT_DIR/prune-backups.py"
if (( offsite_rc != 0 )); then
    ops_event audit backup offsite-failed --details "$(basename "$archive")"
    ops_event notify backup-failed "Local backup verified, but offsite upload failed: $(basename "$archive")" --severity error
    echo "$archive"
    exit "$offsite_rc"
fi
ops_event audit backup ok --details "$(basename "$archive")"
echo "$archive"
