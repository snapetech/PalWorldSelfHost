#!/usr/bin/env bash
set -euo pipefail
repo=$(cd -- "$(dirname -- "$0")/.." && pwd); version=${1:-$(git -C "$repo" describe --tags --always | sed 's/^v//')}
stage=$(mktemp -d); trap 'rm -rf "$stage"' EXIT
root="$stage/palworldselfhost_$version"; mkdir -p "$root/DEBIAN" "$root/usr/share/palworldselfhost"
cp -a "$repo"/{admin,config,public,scripts,systemd,README.md,LICENSE,SECURITY.md} "$root/usr/share/palworldselfhost/"
cat > "$root/DEBIAN/control" <<EOF
Package: palworldselfhost
Version: $version
Architecture: all
Maintainer: PalWorldSelfHost contributors
Depends: python3, curl, rclone, zstd, tar, util-linux, systemd
Description: Single-world Palworld dedicated server operations toolkit
EOF
dpkg-deb --root-owner-group --build "$root" "$repo/palworldselfhost_${version}_all.deb"
