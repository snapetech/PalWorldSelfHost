#!/usr/bin/env python3
"""Transactional state manager for the enforced Palworld allow-list."""

import argparse
import contextlib
import fcntl
import importlib.util
import json
import pathlib
import re
import time


HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("ops_lib", HERE / "ops-lib.py")
ops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops)
STATE_FILE = ops.STATE / "whitelist.json"
MAX_ENTRIES = 500


@contextlib.contextmanager
def mutation_lock():
    ops.STATE.mkdir(parents=True, exist_ok=True)
    with (ops.STATE / "whitelist.lock").open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def state():
    data = ops.read_json(STATE_FILE, {})
    return {
        "enabled": bool(data.get("enabled", False)),
        "enabled_at": data.get("enabled_at"),
        "grace_until": data.get("grace_until"),
        "updated_at": data.get("updated_at"),
        "entries": data.get("entries", []) if isinstance(data.get("entries", []), list) else [],
        "last_enforcement": data.get("last_enforcement"),
    }


def normalize(entries):
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise ValueError(f"entries must be an array of at most {MAX_ENTRIES} players")
    result, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("each whitelist entry must be an object")
        user_id = str(entry.get("user_id", "")).strip()
        name = str(entry.get("name", "")).strip()
        if not 3 <= len(user_id) <= 128 or not re.fullmatch(r"[A-Za-z0-9_.:@+\-]+", user_id):
            raise ValueError("user_id must be 3-128 platform-identity characters")
        if len(name) > 80 or any(character in name for character in "\r\n\0"):
            raise ValueError("name must be a single line of at most 80 characters")
        key = user_id.casefold()
        if key in seen:
            raise ValueError(f"duplicate whitelist identity: {user_id}")
        seen.add(key)
        result.append({"user_id": user_id, "name": name})
    return sorted(result, key=lambda item: (item["name"].casefold(), item["user_id"].casefold()))


def plan(entries):
    current = state(); proposed = normalize(entries)
    before = {item["user_id"].casefold(): item for item in current["entries"]}
    after = {item["user_id"].casefold(): item for item in proposed}
    return {
        "enabled": current["enabled"], "before_count": len(before), "after_count": len(after),
        "added": [after[key] for key in sorted(after.keys() - before.keys())],
        "removed": [before[key] for key in sorted(before.keys() - after.keys())],
        "renamed": [after[key] for key in sorted(before.keys() & after.keys()) if before[key].get("name") != after[key].get("name")],
    }


def replace(entries, confirm):
    if confirm != "REPLACE WHITELIST":
        raise ValueError("exact confirmation REPLACE WHITELIST is required")
    with mutation_lock():
        current = state(); proposed = normalize(entries); changes = plan(proposed)
        current.update({"entries": proposed, "updated_at": int(time.time())})
        ops.atomic_json(STATE_FILE, current)
    ops.audit("whitelist.replace", "ok", before=changes["before_count"], after=changes["after_count"])
    return {**changes, "entries": proposed}


def set_enabled(enabled, confirm):
    expected = "ENABLE WHITELIST" if enabled else "DISABLE WHITELIST"
    if confirm != expected:
        raise ValueError(f"exact confirmation {expected} is required")
    with mutation_lock():
        current = state()
        if enabled and not current["entries"]:
            raise ValueError("refusing to enable an empty whitelist; add at least one identity first")
        now = int(time.time())
        current.update({
            "enabled": enabled, "enabled_at": now if enabled else None,
            "grace_until": now + 60 if enabled else None, "updated_at": now,
        })
        ops.atomic_json(STATE_FILE, current)
    ops.audit("whitelist.enable" if enabled else "whitelist.disable", "ok", count=len(current["entries"]))
    return current


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("status")
    plan_parser = sub.add_parser("plan"); plan_parser.add_argument("entries")
    replace_parser = sub.add_parser("replace"); replace_parser.add_argument("entries"); replace_parser.add_argument("--confirm", default="")
    enable = sub.add_parser("enable"); enable.add_argument("--confirm", default="")
    disable = sub.add_parser("disable"); disable.add_argument("--confirm", default="")
    args = parser.parse_args()
    try:
        if args.action == "status": result = state()
        elif args.action == "plan": result = plan(json.loads(args.entries))
        elif args.action == "replace": result = replace(json.loads(args.entries), args.confirm)
        elif args.action == "enable": result = set_enabled(True, args.confirm)
        else: result = set_enabled(False, args.confirm)
        print(json.dumps(result, indent=2))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)[:500]}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
