#!/usr/bin/env python3
import json, os, pathlib, socket, subprocess, time

backup_root = pathlib.Path(os.environ["PALWORLD_BACKUP_LOCAL_ROOT"])
max_age = int(os.environ.get("PALWORLD_BACKUP_MAX_AGE_HOURS", "26")) * 3600
issues = []
if subprocess.run(["systemctl", "is-active", "--quiet", "palworld.service"]).returncode: issues.append("service is not active")
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try: s.bind(("127.0.0.1", int(os.environ.get("PALWORLD_PORT", "8211"))))
except OSError: pass
else: issues.append("UDP game port is not occupied"); s.close()
files = sorted(backup_root.glob("palworld-*.tar.zst"), key=lambda p:p.stat().st_mtime, reverse=True)
if not files: issues.append("no local backup exists")
elif time.time() - files[0].stat().st_mtime > max_age: issues.append("local backup is stale")
rest = subprocess.run(["/usr/local/lib/palworld/rest-client.py", "metrics"], text=True, capture_output=True)
if rest.returncode: issues.append("REST API health check failed")
state = pathlib.Path("/var/lib/palworld/health.json"); state.parent.mkdir(parents=True, exist_ok=True)
state.write_text(json.dumps({"checked_at": int(time.time()), "ok": not issues, "issues": issues}) + "\n")
if issues:
    message = "Palworld health failure on " + socket.gethostname() + ": " + "; ".join(issues)
    command = os.environ.get("PALWORLD_ALERT_COMMAND", "").strip()
    if command: subprocess.run([command, message], check=False)
    raise SystemExit(message)
