#!/usr/bin/env bash
set -euo pipefail
archive=${1:?usage: verify-backup.sh ARCHIVE}
sha256sum -c "$archive.sha256"
tar --zstd -tf "$archive" | grep -q '^Pal/Saved/'
tar --zstd -tf "$archive" | grep -q '^Pal/Saved/SaveGames/'
echo "verified: $archive"

