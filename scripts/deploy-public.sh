#!/usr/bin/env bash
set -euo pipefail

repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
source_dir="$repo/public"
target_dir=${PALWORLD_PUBLIC_DIR:-/srv/static/palworld}
parent=$(dirname -- "$target_dir")
staging=$(mktemp -d "$parent/.palworld-public.XXXXXX")

cleanup() { rm -rf -- "$staging"; }
trap cleanup EXIT

cp -a "$source_dir/." "$staging/"
if [[ -f "$target_dir/status.json" ]]; then
    cp -a "$target_dir/status.json" "$staging/status.json"
fi

chmod -R a+rX "$staging"
if [[ -d "$target_dir" ]]; then
    old="$parent/.palworld-public.old.$$"
    mv -- "$target_dir" "$old"
    mv -- "$staging" "$target_dir"
    rm -rf -- "$old"
else
    mv -- "$staging" "$target_dir"
fi
trap - EXIT

test -s "$target_dir/index.html"
test -s "$target_dir/style.css"
echo "Published Palworld static assets to $target_dir"
