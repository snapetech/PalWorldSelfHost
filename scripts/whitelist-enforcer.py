#!/usr/bin/env python3
"""Kick online players absent from the explicitly enabled Palworld whitelist."""

import argparse
import hashlib
import importlib.util
import json
import pathlib
import subprocess
import time


HERE = pathlib.Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ops = load("ops_lib", "ops-lib.py")
manager = load("whitelist_manager", "whitelist-manager.py")


def run_json(*arguments):
    result = subprocess.run(arguments, text=True, capture_output=True, timeout=20)
    if result.returncode:
        raise RuntimeError((result.stdout + result.stderr).strip()[-500:] or "command failed")
    return json.loads(result.stdout)


def identity_hash(user_id):
    return hashlib.sha256(user_id.encode()).hexdigest()[:12]


def enforce(now=None, dry_run=False):
    now = int(now or time.time())
    with manager.mutation_lock():
        current = manager.state()
        result = {"checked_at": now, "enabled": current["enabled"], "grace": False,
                  "online": 0, "allowed": 0, "rejected": 0, "failures": 0}
        if not current["enabled"]:
            return result
        if int(current.get("grace_until") or 0) > now:
            result["grace"] = True
            return result
        roster = run_json(str(HERE / "rest-client.py"), "players")
        players = roster.get("players", [])
        if not isinstance(players, list):
            raise RuntimeError("REST player response is malformed; no enforcement attempted")
        allowed = {str(entry["user_id"]).casefold() for entry in current["entries"]}
        result["online"] = len(players)
        for player in players:
            user_id = str(player.get("userId") or player.get("userid") or "").strip()
            if not user_id:
                result["failures"] += 1
                ops.audit("whitelist.enforce", "failed", reason="missing-user-id")
                continue
            if user_id.casefold() in allowed:
                result["allowed"] += 1
                continue
            if dry_run:
                result["rejected"] += 1
                continue
            kick = subprocess.run(
                [str(HERE / "rest-client.py"), "kick", "--userid", user_id,
                 "--message", "This server currently requires whitelist approval."],
                text=True, capture_output=True, timeout=20,
            )
            if kick.returncode:
                result["failures"] += 1
                ops.audit("whitelist.reject", "failed", identity_hash=identity_hash(user_id))
            else:
                result["rejected"] += 1
                ops.audit("whitelist.reject", "ok", identity_hash=identity_hash(user_id))
        current["last_enforcement"] = result
        ops.atomic_json(manager.STATE_FILE, current)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(enforce(dry_run=args.dry_run), indent=2))
    except Exception as exc:
        ops.audit("whitelist.enforce", "failed", reason=str(exc)[:200])
        print(json.dumps({"ok": False, "error": str(exc)[:500]}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
