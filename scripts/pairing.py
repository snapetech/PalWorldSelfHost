#!/usr/bin/env python3
"""Create and consume short-lived, one-use operations-console pairing codes."""

import argparse
import fcntl
import hashlib
import hmac
import json
import os
import pathlib
import secrets
import tempfile
import time


STATE = pathlib.Path(os.environ.get("PALWORLD_STATE_DIR", "/var/lib/palworld"))
PAIRINGS = STATE / "pairing.json"
ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
ROLES = {"viewer", "moderator", "admin"}


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, separators=(",", ":"))
            stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, 0o660)
        os.replace(temporary, path)
    finally:
        try: os.unlink(temporary)
        except FileNotFoundError: pass


def load(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, list) else []
    except (FileNotFoundError, PermissionError, json.JSONDecodeError):
        return []


def digest(salt, code):
    return hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()


def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = (path.parent / "pairing.lock").open("a+")
    fcntl.flock(handle, fcntl.LOCK_EX)
    return handle


def create(role, ttl=300, path=PAIRINGS, now=None):
    if role not in ROLES:
        raise ValueError("role must be viewer, moderator or admin")
    if not isinstance(ttl, int) or ttl < 60 or ttl > 600:
        raise ValueError("pairing TTL must be 60-600 seconds")
    now = int(time.time() if now is None else now)
    code = "".join(secrets.choice(ALPHABET) for _ in range(12))
    salt = secrets.token_hex(16)
    handle = locked(path)
    try:
        records = [row for row in load(path) if isinstance(row, dict) and row.get("expires_at", 0) > now]
        records = records[-9:]
        records.append({"id": secrets.token_hex(8), "salt": salt, "digest": digest(salt, code),
                        "role": role, "created_at": now, "expires_at": now + ttl})
        atomic_json(path, records)
    finally:
        handle.close()
    return {"code": code, "role": role, "expires_at": now + ttl}


def consume(code, path=PAIRINGS, now=None):
    if not isinstance(code, str) or len(code.strip()) != 12:
        return None
    code = code.strip().upper()
    if any(character not in ALPHABET for character in code):
        return None
    now = int(time.time() if now is None else now)
    matched = None
    handle = locked(path)
    try:
        retained = []
        for row in load(path):
            if not isinstance(row, dict) or row.get("expires_at", 0) <= now or row.get("role") not in ROLES:
                continue
            expected = row.get("digest", "")
            actual = digest(str(row.get("salt", "")), code)
            if matched is None and hmac.compare_digest(expected, actual):
                matched = row["role"]
            else:
                retained.append(row)
        atomic_json(path, retained)
    finally:
        handle.close()
    return matched


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create_parser = sub.add_parser("create")
    create_parser.add_argument("--role", choices=sorted(ROLES), default="viewer")
    create_parser.add_argument("--ttl", type=int, default=300)
    create_parser.add_argument("--confirm", default="")
    sub.add_parser("status")
    args = parser.parse_args()
    if args.command == "create":
        if args.confirm != "CREATE PAIRING CODE":
            raise SystemExit("confirmation required")
        result = create(args.role, args.ttl)
    else:
        now = int(time.time()); rows = load(PAIRINGS)
        result = {"pending": sum(isinstance(row, dict) and row.get("expires_at", 0) > now for row in rows),
                  "expires_at": sorted(row["expires_at"] for row in rows if isinstance(row, dict) and row.get("expires_at", 0) > now)}
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
