#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

COMMIT=05038060aa68f9187ae9923b2388ca8db40e58d1
declare -A FILES=(
    [kraken.cpp]=20c48a30db54efa7681f8c9c674f2a16f081373872d033885a6fdf244b9cff01
    [bitknit.cpp]=6e448d52fe485032e8b7aaf3efff2f0efd6394c776d0c6b14f91d2e1d286ed73
    [lzna.cpp]=8fc3fa71d814918f4a286d076c38a155ae8d93375281d796e2feba8ab866c712
)

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

for name in "${!FILES[@]}"; do
    url="https://raw.githubusercontent.com/powzix/ooz/${COMMIT}/${name}"
    curl -fsSL "$url" -o "$workdir/$name"
    actual=$(sha256sum "$workdir/$name" | cut -d' ' -f1)
    expected=${FILES[$name]}
    if [[ "$actual" != "$expected" ]]; then
        echo "refusing: $name sha256 $actual does not match reviewed $expected" >&2
        exit 1
    fi
done

# Upstream main() collides with our own entrypoint and pulls in a
# proprietary-DLL-loading benchmark path we neither need nor want to build.
sed 's/^int main(int argc, char \*argv\[\]) {/int unused_kraken_main(int argc, char *argv[]) {/' \
    "$workdir/kraken.cpp" > "$workdir/kraken.cpp.renamed"
awk '/^typedef int WINAPI OodLZ_CompressFunc/{exit} {print}' "$workdir/kraken.cpp.renamed" > "$workdir/kraken_lib.cpp"

g++ -O2 -msse2 -I. -o palworld_decompress \
    palworld_decompress.cpp "$workdir/kraken_lib.cpp" "$workdir/bitknit.cpp" "$workdir/lzna.cpp"

echo "built ./palworld_decompress"
