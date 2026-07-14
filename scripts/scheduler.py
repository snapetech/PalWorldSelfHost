#!/usr/bin/env python3
import importlib.util, json, pathlib, subprocess, time

here = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ops_lib", here / "ops-lib.py")
ops = importlib.util.module_from_spec(spec); spec.loader.exec_module(ops)
jobs_file = ops.STATE / "jobs.json"; jobs = ops.read_json(jobs_file, [])
now = int(time.time()); changed = False
for job in jobs:
    if not job.get("enabled", True) or int(job.get("next_run", job.get("due_at", now + 1))) > now: continue
    kind = job.get("type"); result = 1
    if kind == "announcement":
        result = subprocess.run([str(here / "rest-client.py"), "announce", "--message", job.get("message", "")]).returncode
    elif kind == "restart":
        result = subprocess.run(["sudo", "-n", str(here / "graceful-restart.sh"), job.get("message", "Scheduled restart")]).returncode
    ops.audit("scheduler.run", "ok" if result == 0 else "failed", job_id=job.get("id"), type=kind)
    if job.get("interval_seconds"):
        job["next_run"] = now + int(job["interval_seconds"])
    else: job["enabled"] = False
    job["last_run"] = now; job["last_result"] = result; changed = True
if changed: ops.atomic_json(jobs_file, jobs)
