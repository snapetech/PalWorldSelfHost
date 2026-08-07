#!/usr/bin/env python3
"""Opt-in empty-roster auto-pause controller with fail-open safety."""

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import time


HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("ops_lib", HERE / "ops-lib.py")
ops = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(ops)
STATE = ops.STATE / "auto-pause.json"


def threshold_seconds():
    try: minutes = int(os.environ.get("PALWORLD_AUTO_PAUSE_EMPTY_MINUTES", "30"))
    except ValueError: minutes = 30
    return max(5, min(minutes, 1440)) * 60


def decision(now, prior, *, enabled, service_active, players):
    state = dict(prior or {})
    state["checked_at"] = now
    if not enabled:
        return {"action": "none", "status": "disabled"}, {"checked_at": now, "status": "disabled"}
    if not service_active:
        status = "paused" if state.get("auto_paused") else "inactive"
        state["status"] = status
        return {"action": "none", "status": status}, state
    if state.get("auto_paused"):
        state = {"checked_at": now, "status": "active", "empty_since": now, "auto_paused": False, "resumed_at": now}
    if players is None:
        state["status"] = "roster-unavailable"
        state.pop("empty_since", None)
        return {"action": "none", "status": "roster-unavailable"}, state
    if players > 0:
        state.update({"status": "active", "players": players, "auto_paused": False})
        state.pop("empty_since", None)
        return {"action": "none", "status": "active"}, state
    state.setdefault("empty_since", now)
    state.update({"status": "counting-down", "players": 0, "auto_paused": False})
    remaining = threshold_seconds() - (now - int(state["empty_since"]))
    if remaining > 0:
        return {"action": "none", "status": "counting-down", "remaining_seconds": remaining}, state
    return {"action": "pause", "status": "due"}, state


def service_active():
    return subprocess.run(["systemctl", "is-active", "--quiet", "palworld.service"]).returncode == 0


def roster():
    process = subprocess.run([str(HERE / "rest-client.py"), "players"], text=True, capture_output=True, timeout=20)
    if process.returncode:
        return None
    try: return len(json.loads(process.stdout).get("players", []))
    except json.JSONDecodeError: return None


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "resumed":
        now = int(time.time())
        state = {"checked_at": now, "status": "active", "empty_since": now, "auto_paused": False, "resumed_at": now}
        ops.atomic_json(STATE, state)
        print(json.dumps(state, indent=2))
        return
    if len(sys.argv) != 1:
        raise SystemExit("usage: auto-pause.py [resumed]")
    now = int(time.time())
    enabled = os.environ.get("PALWORLD_AUTO_PAUSE_ENABLED", "false").lower() == "true"
    active = service_active()
    players = roster() if enabled and active else None
    action, state = decision(now, ops.read_json(STATE, {}), enabled=enabled, service_active=active, players=players)
    if action["action"] == "pause":
        try:
            with ops.operation_lock(blocking=False):
                if not service_active():
                    state.update({"status": "inactive", "auto_paused": False})
                elif roster() != 0:
                    state.update({"status": "roster-changed", "auto_paused": False}); state.pop("empty_since", None)
                else:
                    process = subprocess.run(
                        [str(HERE / "graceful-shutdown.sh"), "30", "Server is auto-pausing after the configured empty period."],
                        text=True, capture_output=True, timeout=180, env={**os.environ, "PALWORLD_LOCK_HELD": "true"},
                    )
                    if process.returncode:
                        raise RuntimeError((process.stdout + process.stderr).strip()[-2000:])
                    state.update({"status": "paused", "auto_paused": True, "paused_at": int(time.time())})
                    ops.audit("auto-pause", "ok", empty_minutes=threshold_seconds() // 60)
                    ops.notify("Palworld auto-paused", "The empty server stopped cleanly; an administrator can resume it on demand.")
        except BlockingIOError:
            state["status"] = "deferred-mutation"
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            state.update({"status": "failed", "error": str(exc)[:500]})
            ops.audit("auto-pause", "failed", error=str(exc))
    ops.atomic_json(STATE, state)
    print(json.dumps(state, indent=2))


if __name__ == "__main__": main()
