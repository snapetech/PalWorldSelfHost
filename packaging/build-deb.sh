#!/usr/bin/env bash
set -euo pipefail
repo=$(cd -- "$(dirname -- "$0")/.." && pwd); version=${1:-$(git -C "$repo" describe --tags --always | sed 's/^v//')}
stage=$(mktemp -d); trap 'rm -rf "$stage"' EXIT
root="$stage/palworldselfhost_$version"; mkdir -p "$root/DEBIAN" "$root/usr/share/palworldselfhost"
cp -a "$repo"/{admin,bootstrap,config,deploy,docs,public,scripts,systemd,README.md,LICENSE,SECURITY.md} "$root/usr/share/palworldselfhost/"
find "$root/usr/share/palworldselfhost" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$root/usr/share/palworldselfhost" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
cat > "$root/DEBIAN/control" <<EOF
Package: palworldselfhost
Version: $version
Architecture: all
Maintainer: PalWorldSelfHost contributors
Depends: python3, python3-websocket, curl, rclone, rsync, zstd, tar, util-linux, systemd, nftables
Recommends: wine64, xvfb
Description: Palworld dedicated server operations and multi-instance toolkit
EOF
dpkg-deb --root-owner-group --build "$root" "$repo/palworldselfhost_${version}_all.deb"
