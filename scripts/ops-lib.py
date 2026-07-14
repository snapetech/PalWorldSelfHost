#!/usr/bin/env python3
"""Shared single-instance operations primitives for PalWorldSelfHost."""

from __future__ import annotations

import fcntl
import json
import os
import pathlib
import socket
import subprocess
import time
import urllib.request
from contextlib import contextmanager

STATE = pathlib.Path(os.environ.get("PALWORLD_STATE_DIR", "/var/lib/palworld"))


def atomic_json(path: pathlib.Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.chmod(temporary, 0o660)
    temporary.replace(path)


def read_json(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, PermissionError, json.JSONDecodeError):
        return default


@contextmanager
def operation_lock(name: str = "mutation", blocking: bool = True):
    STATE.mkdir(parents=True, exist_ok=True)
    lock_path = STATE / f"{name}.lock"
    with lock_path.open("a+") as handle:
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        fcntl.flock(handle, flags)
        handle.seek(0); handle.truncate()
        handle.write(f"{os.getpid()} {int(time.time())}\n"); handle.flush()
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def audit(action: str, result: str, **details) -> dict:
    event = {"timestamp": int(time.time()), "host": socket.gethostname(),
             "actor": os.environ.get("SUDO_USER") or os.environ.get("USER") or "system",
             "action": action, "result": result, **details}
    STATE.mkdir(parents=True, exist_ok=True)
    audit_file = STATE / "audit.jsonl"
    with audit_file.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.write(json.dumps(event, sort_keys=True) + "\n")
        handle.flush(); os.fsync(handle.fileno())
    os.chmod(audit_file, 0o660)
    return event


def maintenance_phase(phase: str, **details) -> None:
    current = read_json(STATE / "maintenance.json", {})
    started = current.get("started_at", int(time.time())) if phase != "announcing" else int(time.time())
    payload = {"phase": phase, "result": "failed" if phase == "failed" else ("ok" if phase == "complete" else "running"),
               "started_at": started, "updated_at": int(time.time()), **details}
    if phase in {"failed", "complete"}: payload["finished_at"] = int(time.time())
    atomic_json(STATE / "maintenance.json", payload)
    audit("maintenance.phase", "ok", phase=phase, **details)


def notify(event: str, message: str, severity: str = "info") -> None:
    payload = {"event": event, "message": message, "severity": severity,
               "host": socket.gethostname(), "timestamp": int(time.time())}
    generic = os.environ.get("PALWORLD_WEBHOOK_URL", "").strip()
    discord = os.environ.get("PALWORLD_DISCORD_WEBHOOK_URL", "").strip()
    ntfy = os.environ.get("PALWORLD_NTFY_URL", "").strip()
    gotify = os.environ.get("PALWORLD_GOTIFY_URL", "").strip()
    requests = []
    if generic:
        requests.append(urllib.request.Request(generic, json.dumps(payload).encode(),
                        {"Content-Type": "application/json"}, method="POST"))
    if discord:
        requests.append(urllib.request.Request(discord, json.dumps({"content": message}).encode(),
                        {"Content-Type": "application/json"}, method="POST"))
    if ntfy:
        requests.append(urllib.request.Request(ntfy, message.encode(),
                        {"Title": f"Palworld: {event}", "Priority": "4" if severity == "error" else "3"}, method="POST"))
    if gotify:
        requests.append(urllib.request.Request(gotify, json.dumps({"title": event, "message": message,
                        "priority": 8 if severity == "error" else 4}).encode(),
                        {"Content-Type": "application/json"}, method="POST"))
    failures = []
    for request in requests:
        try:
            urllib.request.urlopen(request, timeout=10).close()
        except Exception as exc:
            failures.append(str(exc))
    command = os.environ.get("PALWORLD_ALERT_COMMAND", "").strip()
    if command:
        completed = subprocess.run([command, message], check=False)
        if completed.returncode: failures.append(f"alert command exited {completed.returncode}")
    audit("notification", "error" if failures else "ok", event=event, severity=severity, failures=failures)
