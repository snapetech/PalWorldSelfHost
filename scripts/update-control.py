#!/usr/bin/env python3
"""Read-only Steam update check and operator plan generation."""

import argparse
import importlib.util
import json
import pathlib
import subprocess
import time


HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("ops_lib", HERE / "ops-lib.py")
ops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops)


def run_json(*command, timeout=180):
    process = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
    if process.returncode:
        raise RuntimeError((process.stdout + process.stderr).strip()[-2000:] or "command failed")
    return json.loads(process.stdout)


def check():
    status = dict(run_json(str(HERE / "update-status.py")))
    now = int(time.time())
    prior = ops.read_json(ops.STATE / "update.json", {})
    build_state = run_json(str(HERE / "steam-build-manager.py"), "list")
    pin = build_state.get("pin", {})
    status.update({
        "checked_at": now, "first_seen_at": prior.get("first_seen_at"), "pin": pin,
        "snapshots": len(build_state.get("snapshots", [])),
    })
    status["blocked_by_pin"] = bool(
        pin.get("build_id") and str(pin["build_id"]) == str(status.get("local_build"))
        and str(pin["build_id"]) != str(status.get("remote_build"))
    )
    if status.get("update_available") and not status["first_seen_at"]:
        status["first_seen_at"] = now
    if not status.get("update_available"):
        status["first_seen_at"] = None
    ops.atomic_json(ops.STATE / "update.json", status)
    ops.audit("update.browser-check", "ok", local_build=status.get("local_build"), remote_build=status.get("remote_build"))
    return status


def plan():
    status = check()
    players = run_json(str(HERE / "rest-client.py"), "players", timeout=30).get("players", [])
    build_state = run_json(str(HERE / "steam-build-manager.py"), "list")
    target = status.get("remote_build")
    snapshots = build_state.get("snapshots", [])
    return {
        **status, "players_online": len(players),
        "target_build": target,
        "executable": bool(status.get("update_available") and not status.get("blocked_by_pin") and target and str(target).isdigit()),
        "rollback_available": bool(snapshots),
        "rollback_builds": sorted(
            {str(item.get("build_id")) for item in snapshots if str(item.get("build_id", "")).isdigit()},
            key=int,
            reverse=True,
        ),
        "steps": [
            "announce the exact-confirmed maintenance window",
            "save and stop the world through the private REST API",
            "publish and verify a protected backup",
            "snapshot and verify the current executable tree without Pal/Saved",
            f"install and validate Steam build {target or 'unknown'}",
            "render managed settings and start the world",
            "verify the private REST info endpoint",
            "automatically restore the prior executable snapshot if installation or health validation fails",
        ],
        "rollback_note": (
            "Verified local executable snapshots can be exact-confirmed from the Builds panel; "
            "rollback preserves Pal/Saved and pins the restored build after REST health succeeds."
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["check", "plan"])
    args = parser.parse_args()
    try:
        print(json.dumps(check() if args.command == "check" else plan(), indent=2))
    except (RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        ops.audit("update.browser-check", "failed", error=str(exc)[-1000:])
        raise SystemExit(str(exc))


if __name__ == "__main__":
    main()
