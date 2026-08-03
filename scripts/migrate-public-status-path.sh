#!/usr/bin/env bash
set -euo pipefail

env_file=${PALWORLD_ENV_FILE:-/etc/palworld-server.env}
public_dir=${PALWORLD_PUBLIC_DIR:-/srv/static/palworld}
public_user=${PALWORLD_USER:-palworld}
public_group=${PALWORLD_GROUP:-palworld}

[[ "$public_dir" == /* ]] || { echo "public directory must be absolute" >&2; exit 1; }
[[ -f "$env_file" ]] || { echo "environment file not found: $env_file" >&2; exit 1; }

python3 - "$env_file" "$public_dir" <<'PY'
import os
import pathlib
import stat
import sys
import tempfile

path = pathlib.Path(sys.argv[1])
public_dir = sys.argv[2]
original = path.read_text()
lines = original.splitlines(keepends=True)
found = False
for index, line in enumerate(lines):
    if line.startswith("PALWORLD_PUBLIC_DIR="):
        lines[index] = f"PALWORLD_PUBLIC_DIR={public_dir}\n"
        found = True
        break
if not found:
    raise SystemExit("PALWORLD_PUBLIC_DIR is missing from the environment file")

updated = "".join(lines)
if updated != original:
    metadata = path.stat()
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, stat.S_IMODE(metadata.st_mode))
        os.fchown(descriptor, metadata.st_uid, metadata.st_gid)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(updated)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
PY

install -d -o "$public_user" -g "$public_group" -m 0755 "$public_dir"
