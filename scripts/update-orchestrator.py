#!/usr/bin/env python3
import importlib.util, json, os, pathlib, subprocess, time

here = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ops_lib", here / "ops-lib.py")
ops = importlib.util.module_from_spec(spec); spec.loader.exec_module(ops)
status_file = ops.STATE / "update.json"
check = subprocess.run([str(here / "update-status.py")], text=True, capture_output=True, timeout=180)
if check.returncode:
    ops.audit("update.check", "failed", error=check.stderr[-1000:]); raise SystemExit(check.returncode)
status = json.loads(check.stdout); now = int(time.time()); prior = ops.read_json(status_file, {})
status["checked_at"] = now
if not status.get("update_available"):
    status["first_seen_at"] = None; ops.atomic_json(status_file, status); ops.audit("update.check", "current", **status); raise SystemExit(0)
status["first_seen_at"] = prior.get("first_seen_at") or now
players_result = subprocess.run([str(here / "rest-client.py"), "players"], text=True, capture_output=True)
players = json.loads(players_result.stdout).get("players", []) if players_result.returncode == 0 else []
status["players_online"] = len(players); ops.atomic_json(status_file, status)
max_deferral = int(os.environ.get("PALWORLD_UPDATE_MAX_DEFERRAL_HOURS", "12")) * 3600
forced = now - status["first_seen_at"] >= max_deferral
if players and not forced:
    subprocess.run([str(here / "rest-client.py"), "announce", "--message",
                    f"A server update is ready and will install when the server is empty (maximum deferral {max_deferral//3600}h)."], check=False)
    ops.notify("update-deferred", f"Build {status.get('remote_build')} deferred for {len(players)} online player(s).")
    ops.audit("update.defer", "ok", players=len(players), forced=False, remote_build=status.get("remote_build")); raise SystemExit(0)
wait = int(os.environ.get("PALWORLD_UPDATE_WARN_MINUTES", "30")) * 60 if players else 0
ops.notify("update-starting", f"Installing Palworld build {status.get('remote_build')}.")
result = subprocess.run([str(here / "maintenance.sh"), "update", str(wait)])
if result.returncode: raise SystemExit(result.returncode)
status.update({"installed_at": int(time.time()), "first_seen_at": None}); ops.atomic_json(status_file, status)
