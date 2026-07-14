#!/usr/bin/env python3
"""Create a redacted support bundle without credentials or player identifiers."""
import json, os, pathlib, platform, re, subprocess, tarfile, tempfile, time

state = pathlib.Path(os.environ.get("PALWORLD_STATE_DIR", "/var/lib/palworld")); output = pathlib.Path.cwd() / f"palworld-support-{int(time.time())}.tar.gz"
def command(*args):
    p = subprocess.run(args, text=True, capture_output=True, timeout=30); return (p.stdout + p.stderr)[-100000:]
def redact(value):
    value = re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', '<ip-redacted>', value)
    value = re.sub(r'(?i)(steam_|account_|user_?id[=: ]+)[A-Za-z0-9_-]+', r'\1<id-redacted>', value)
    return value
with tempfile.TemporaryDirectory() as temporary:
    root = pathlib.Path(temporary); (root / "system.txt").write_text(platform.platform() + "\n" + command("systemctl", "status", "palworld.service", "--no-pager"))
    (root / "journal.txt").write_text(redact(command("journalctl", "-u", "palworld.service", "-n", "500", "--no-pager")))
    (root / "listeners.txt").write_text(redact(command("ss", "-lntup"))); (root / "disk.txt").write_text(command("df", "-h"))
    for name in ("health.json", "maintenance.json", "update.json"):
        source = state / name
        if source.exists(): (root / name).write_text(source.read_text())
    env = pathlib.Path("/etc/palworld-server.env")
    if env.exists():
        redacted = re.sub(r'(?im)^([^#\n]*(?:password|token|secret|webhook|rclone)[^=]*)=.*$', r'\1=<redacted>', env.read_text())
        (root / "environment.redacted").write_text(redacted)
    with tarfile.open(output, "w:gz") as tar: tar.add(root, arcname="palworld-support")
print(output)
