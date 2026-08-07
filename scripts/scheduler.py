#!/usr/bin/env python3
"""Run announcements, restarts, and reversible timed settings events."""

import importlib.util
import json
import pathlib
import subprocess
import time


HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("ops_lib", HERE / "ops-lib.py")
ops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops)
JOBS_FILE = ops.STATE / "jobs.json"
EVENT_PRESETS = {
    "double_xp": {"label": "Double XP", "updates": {"ExpRate": 2.0}},
    "resource_rush": {"label": "Resource rush", "updates": {"CollectionDropRate": 2.0, "EnemyDropItemRate": 2.0}},
    "capture_weekend": {"label": "Capture weekend", "updates": {"PalCaptureRate": 2.0, "PalSpawnNumRate": 1.25}},
}


def execute(arguments):
    return subprocess.run(arguments, check=False).returncode


def restart(message):
    return execute(["sudo", "-n", str(HERE / "graceful-restart.sh"), message])


def restore_event(job):
    patch = job.get("restore_patch")
    if not isinstance(patch, dict):
        return 2
    result = execute([
        str(HERE / "settings-manager.py"), "restore-patch", json.dumps(patch),
        "--confirm", "RESTORE EVENT SETTINGS",
    ])
    if result == 0:
        result = restart(f"Ending timed event: {job.get('event_label', 'settings event')}")
    return result


def start_event(job, now):
    preset = EVENT_PRESETS.get(job.get("preset"))
    if not preset:
        return 2
    updates = preset["updates"]
    current = ops.read_json(ops.STATE / "settings-overrides.json", {})
    job["restore_patch"] = {
        key: {"present": key in current, "value": current.get(key)} for key in updates
    }
    job["event_label"] = preset["label"]
    result = execute([
        str(HERE / "settings-manager.py"), "apply", json.dumps(updates),
        "--confirm", "APPLY SETTINGS",
    ])
    if result == 0:
        result = restart(f"Starting timed event: {preset['label']}")
    if result != 0:
        restore_event(job)
        return result
    job["phase"] = "active"
    job["started_at"] = now
    job["next_run"] = now + int(job["duration_seconds"])
    return 0


def run_due_jobs(now=None):
    now = int(now or time.time())
    jobs = ops.read_json(JOBS_FILE, [])
    changed = False
    for job in jobs:
        if not job.get("enabled", True):
            continue
        kind = job.get("type")
        due = int(job.get("next_run", job.get("due_at", now + 1))) <= now
        if kind == "settings_event" and job.get("phase") == "active" and job.get("cancel_requested"):
            due = True
        if not due:
            continue
        if kind == "announcement":
            result = execute([str(HERE / "rest-client.py"), "announce", "--message", job.get("message", "")])
        elif kind == "restart":
            result = restart(job.get("message", "Scheduled restart"))
        elif kind == "rcon":
            payload = json.dumps({"actor": "scheduler", "source": "scheduler", "confirm": "EXECUTE RCON"})
            if job.get("saved_id") is not None:
                result = execute([str(HERE / "rcon-console.py"), "saved-execute", str(job["saved_id"]), payload])
            else:
                payload = json.dumps({
                    "command": job.get("command", ""), "actor": "scheduler",
                    "source": "scheduler", "confirm": "EXECUTE RCON",
                })
                result = execute([str(HERE / "rcon-console.py"), "execute", payload])
        elif kind == "settings_event" and job.get("phase") == "active":
            result = restore_event(job)
            if result == 0:
                job["phase"] = "restored"
                job["restored_at"] = now
                job["enabled"] = False
                job.pop("restore_patch", None)
            else:
                job["next_run"] = now + 60
        elif kind == "settings_event":
            result = start_event(job, now)
            if result != 0:
                job["enabled"] = False
                job["phase"] = "failed"
        else:
            result = 2
        ops.audit("scheduler.run", "ok" if result == 0 else "failed", job_id=job.get("id"), type=kind, phase=job.get("phase"))
        if kind != "settings_event":
            if job.get("interval_seconds"):
                job["next_run"] = now + int(job["interval_seconds"])
            else:
                job["enabled"] = False
        job["last_run"] = now
        job["last_result"] = result
        changed = True
    if changed:
        ops.atomic_json(JOBS_FILE, jobs)
    return jobs


if __name__ == "__main__":
    run_due_jobs()
