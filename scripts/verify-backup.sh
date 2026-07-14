#!/usr/bin/env bash
set -euo pipefail
archive=${1:?usage: verify-backup.sh ARCHIVE}
sha256sum -c "$archive.sha256"
listing=$(mktemp); trap 'rm -f "$listing"' EXIT
tar --zstd -tf "$archive" > "$listing"
grep -q '^Pal/Saved/' "$listing"
grep -q '^Pal/Saved/SaveGames/' "$listing"
echo "verified: $archive"
