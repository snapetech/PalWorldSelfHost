#!/usr/bin/env python3
"""Typed, capability-gated PalDefender player and coordinate actions."""

import argparse
import hashlib
import importlib.util
import json
import math
import os
import pathlib
import re


HERE = pathlib.Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ops = load("player_actions_ops", "ops-lib.py")
console = load("player_actions_console", "rcon-console.py")

ACTION_SCHEMA = {
    "teleport": {"command": "tp", "confirmation": "TELEPORT PLAYER", "description": "Teleport one online player to bounded world coordinates"},
    "spawn_pal": {"command": "spawnpal", "confirmation": "SPAWN PAL", "description": "Spawn one bounded-level wild Pal at bounded world coordinates"},
    "grant_item": {"command": "give", "confirmation": "GRANT ITEM", "description": "Grant a bounded item quantity to one online player"},
    "grant_pal": {"command": "givepal", "confirmation": "GRANT PAL", "description": "Grant one bounded-level Pal to one online player"},
    "grant_status": {"command": "givestats", "confirmation": "GRANT STATUS POINTS", "description": "Grant bounded status points to one online player"},
}
IDENTITY = re.compile(r"[A-Za-z0-9_:@.-]{3,128}")
ASSET_ID = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,79}")


def advertised():
    catalog, discovered = console.discovered_catalog()
    names = {item["command"].casefold(): item["command"] for item in catalog}
    return names, discovered


def capabilities():
    names, discovered = advertised()
    actions = {}
    for name, schema in ACTION_SCHEMA.items():
        actions[name] = {
            **schema,
            "available": schema["command"] in names,
            "advertised_command": names.get(schema["command"]),
        }
    return {"backend": "paldefender", "discovery_succeeded": discovered, "actions": actions}


def bounded_int(value, name, minimum, maximum):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer") from None
    if str(value).strip() != str(number) or not minimum <= number <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return number


def coordinate(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite coordinate")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a finite coordinate") from None
    if not math.isfinite(number) or abs(number) > 10_000_000:
        raise ValueError(f"{name} must be finite and within world bounds")
    return round(number, 3)


def normalize(payload):
    if not isinstance(payload, dict):
        raise ValueError("action payload must be an object")
    action = str(payload.get("action", ""))
    if action not in ACTION_SCHEMA:
        raise ValueError("unknown typed player action")
    result = {"action": action}
    if action != "spawn_pal":
        target = str(payload.get("target", "")).strip()
        if not IDENTITY.fullmatch(target):
            raise ValueError("target must be a 3-128 character platform identity")
        result["target"] = target
    if action in {"teleport", "spawn_pal"}:
        result.update({axis: coordinate(payload.get(axis, 0 if axis == "z" else None), axis) for axis in ("x", "y", "z")})
    if action == "spawn_pal":
        pal_id = str(payload.get("pal_id", "")).strip()
        if not ASSET_ID.fullmatch(pal_id): raise ValueError("pal_id is invalid")
        result.update({"pal_id": pal_id, "level": bounded_int(payload.get("level"), "level", 1, 100)})
    elif action == "teleport":
        pass
    elif action == "grant_item":
        item_id = str(payload.get("item_id", "")).strip()
        if not ASSET_ID.fullmatch(item_id): raise ValueError("item_id is invalid")
        result.update({"item_id": item_id, "amount": bounded_int(payload.get("amount"), "amount", 1, 9999)})
    elif action == "grant_pal":
        pal_id = str(payload.get("pal_id", "")).strip()
        if not ASSET_ID.fullmatch(pal_id): raise ValueError("pal_id is invalid")
        result.update({"pal_id": pal_id, "level": bounded_int(payload.get("level"), "level", 1, 100)})
    else:
        result["points"] = bounded_int(payload.get("points"), "points", 1, 9999)
    return result


def reviewed_sha(payload):
    safe = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(b"palworld-player-action-v1\0" + safe).hexdigest()


def command_for(payload, actual):
    if payload["action"] == "spawn_pal":
        return f"{actual} {payload['pal_id']} {payload['x']:.3f} {payload['y']:.3f} {payload['z']:.3f} {payload['level']}"
    target = payload["target"]
    if payload["action"] == "teleport":
        return f"{actual} {target} {payload['x']:.3f} {payload['y']:.3f} {payload['z']:.3f}"
    if payload["action"] == "grant_item":
        return f"{actual} {target} {payload['item_id']} {payload['amount']}"
    if payload["action"] == "grant_pal":
        return f"{actual} {target} {payload['pal_id']} {payload['level']}"
    return f"{actual} {target} {payload['points']}"


def plan(raw):
    payload = normalize(raw)
    names, discovered = advertised()
    required = ACTION_SCHEMA[payload["action"]]["command"]
    if not discovered or required not in names:
        raise ValueError(f"running PalDefender did not advertise required command {required}")
    public_payload = dict(payload)
    target = public_payload.pop("target", None)
    target_hash = hashlib.sha256(target.encode()).hexdigest()[:16] if target else None
    return {
        "action": payload["action"], "available": True,
        "confirmation": ACTION_SCHEMA[payload["action"]]["confirmation"],
        "reviewed_sha256": reviewed_sha(payload), "target_hash": target_hash,
        "parameters": public_payload, "command": names[required],
        "verification": "post-position" if payload["action"] == "teleport" and "getpos" in names else "command-acknowledged",
    }


def parse_position(output):
    matches = re.findall(r"(?i)(?:x|y|z)\s*[=:]\s*(-?\d+(?:\.\d+)?)", output[:4096])
    if len(matches) >= 3:
        return {axis: float(value) for axis, value in zip(("x", "y", "z"), matches[:3])}
    return None


def require_application_success(output):
    text = str(output or "").strip()
    rejected = re.search(
        r"(?i)(?:^|\b)(invalid|failed|failure|could not|cannot|not found|unknown|denied|rejected|error)(?:\b|:)",
        text[:4096],
    )
    if rejected:
        raise RuntimeError(f"PalDefender rejected the command: {text[:500]}")
    return text


def execute(raw, expected_sha256, confirmation, actor="operator"):
    payload = normalize(raw)
    schema = ACTION_SCHEMA[payload["action"]]
    if confirmation != schema["confirmation"]:
        raise ValueError(f"exact confirmation {schema['confirmation']} is required")
    if expected_sha256 != reviewed_sha(payload):
        raise ValueError("action differs from the reviewed plan")
    names, discovered = advertised()
    required = schema["command"]
    if not discovered or required not in names:
        raise ValueError(f"running PalDefender did not advertise required command {required}")
    before = None
    target = payload.get("target")
    if target and "getpos" in names:
        try: before = parse_position(console.rcon.execute(f"{names['getpos']} {payload['target']}"))
        except Exception: before = None
    result = console.execute(command_for(payload, names[required]), actor=actor, source="player-action", confirm="EXECUTE RCON")
    require_application_success(result.get("output", ""))
    after = None
    if payload["action"] == "teleport" and "getpos" in names:
        try: after = parse_position(console.rcon.execute(f"{names['getpos']} {payload['target']}"))
        except Exception: after = None
    target_hash = hashlib.sha256(target.encode()).hexdigest()[:16] if target else None
    verification = "command-acknowledged"
    if payload["action"] == "teleport" and after:
        delta_xy = math.hypot(after["x"] - payload["x"], after["y"] - payload["y"])
        verification = "destination-observed" if delta_xy <= 250 else "position-mismatch"
        if verification == "position-mismatch":
            ops.audit("player-action.execute", "failed", action_type=payload["action"], target_hash=target_hash, verification=verification, delta_xy=round(delta_xy, 3))
            raise RuntimeError("teleport command returned but the observed player position did not reach the reviewed destination")
    else:
        delta_xy = None
    ops.audit("player-action.execute", "ok", action_type=payload["action"], target_hash=target_hash, verification=verification)
    return {
        "ok": True, "action": payload["action"], "target_hash": target_hash,
        "before_position": before, "after_position": after,
        "verification": verification, "destination_delta_xy": round(delta_xy, 3) if delta_xy is not None else None,
        "output": result["output"][-2000:],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("capabilities")
    plan_parser = sub.add_parser("plan"); plan_parser.add_argument("payload")
    run_parser = sub.add_parser("execute"); run_parser.add_argument("payload"); run_parser.add_argument("--expected-sha256", required=True); run_parser.add_argument("--confirm", default=""); run_parser.add_argument("--actor", default="operator")
    args = parser.parse_args()
    try:
        if args.command == "capabilities": result = capabilities()
        elif args.command == "plan": result = plan(json.loads(args.payload))
        else: result = execute(json.loads(args.payload), args.expected_sha256, args.confirm, args.actor)
        print(json.dumps(result, indent=2))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)[:500]}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
