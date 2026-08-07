#!/usr/bin/env bash
set -euo pipefail

here=$(cd -- "$(dirname -- "$0")" && pwd)
environment_file=${1:-$here/palworld.env}
image='docker.io/thijsvanloef/palworld-server-docker:v2.6.0@sha256:85ae20d8756dd398ec24f4253a59b80907e5ffe411148a67b2c80c4527a8855e'
arm_manifest='sha256:e09c1e16db753e01113e0f8a1f7f8b3af46664e51646e3d265b5b72bec113218'

fail() { printf 'ARM64 preflight refused: %s\n' "$*" >&2; exit 64; }

architecture=$(uname -m)
[[ "$architecture" == arm64 || "$architecture" == aarch64 ]] || fail "host architecture is $architecture, not ARM64"
[[ $(getconf PAGESIZE) == 4096 ]] || fail "Box64 backend requires a 4096-byte host page size"
command -v docker >/dev/null 2>&1 || fail "Docker Engine with Compose v2 is required"
docker_architecture=$(docker info --format '{{.Architecture}}')
[[ "$docker_architecture" == arm64 || "$docker_architecture" == aarch64 ]] || fail "Docker daemon is not running natively on ARM64"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"

[[ -f "$environment_file" && ! -L "$environment_file" ]] || fail "environment file must be a regular non-symlink"
permissions=$(stat -c '%a' "$environment_file")
(( (8#$permissions & 077) == 0 )) || fail "environment file must not be accessible by group or other users"

read_value() { awk -F= -v name="$1" '$1 == name {sub(/^[^=]*=/, ""); print; found=1; exit} END {if (!found) exit 1}' "$environment_file"; }
for key in SERVER_NAME ADMIN_PASSWORD PUBLIC_IP; do
  value=$(read_value "$key") || fail "$key is missing"
  [[ -n "$value" && "$value" != *'<'* && "$value" != *'>'* ]] || fail "$key is empty or still a placeholder"
done
admin_password=$(read_value ADMIN_PASSWORD)
(( ${#admin_password} >= 16 )) || fail "ADMIN_PASSWORD must contain at least 16 characters"
device=$(read_value ARM64_DEVICE 2>/dev/null || printf generic)
[[ "$device" =~ ^(generic|m1|rpi5|adlink)$ ]] || fail "ARM64_DEVICE must be generic, m1, rpi5, or adlink"

manifest=$(docker buildx imagetools inspect "$image")
grep -Fq "Digest:    sha256:85ae20d8756dd398ec24f4253a59b80907e5ffe411148a67b2c80c4527a8855e" <<<"$manifest" || fail "image index digest did not match the reviewed release"
grep -Fq "${arm_manifest}" <<<"$manifest" || fail "reviewed ARM64 image manifest is absent"
PALWORLD_ARM64_ENV_FILE="$environment_file" docker compose --env-file "$environment_file" -f "$here/compose.yaml" config --quiet
printf 'ARM64 preflight passed: native ARM64 Docker, 4 KiB pages, reviewed image %s.\n' "$arm_manifest"
