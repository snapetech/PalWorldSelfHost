#!/usr/bin/env python3
import importlib.util, json, os, pathlib, socket, subprocess, time

here = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ops_lib", here / "ops-lib.py")
ops = importlib.util.module_from_spec(spec); spec.loader.exec_module(ops)

backup_root = pathlib.Path(os.environ["PALWORLD_BACKUP_LOCAL_ROOT"])
max_age = int(os.environ.get("PALWORLD_BACKUP_MAX_AGE_HOURS", "26")) * 3600
issues = []
if subprocess.run(["systemctl", "is-active", "--quiet", "palworld.service"]).returncode: issues.append("service is not active")
properties = dict(line.split("=", 1) for line in subprocess.run(["systemctl", "show", "palworld.service", "-p", "NRestarts"], text=True, capture_output=True).stdout.splitlines() if "=" in line)
restarts = int(properties.get("NRestarts", "0")); crash_limit = int(os.environ.get("PALWORLD_CRASH_LIMIT", "5"))
if restarts >= crash_limit: issues.append(f"crash-loop guard: {restarts} automatic restarts")
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try: s.bind(("127.0.0.1", int(os.environ.get("PALWORLD_PORT", "8211"))))
except OSError: pass
else: issues.append("UDP game port is not occupied"); s.close()
files = sorted(backup_root.glob("palworld-*.tar.zst"), key=lambda p:p.stat().st_mtime, reverse=True)
if not files: issues.append("no local backup exists")
elif time.time() - files[0].stat().st_mtime > max_age: issues.append("local backup is stale")
rest = subprocess.run(["/usr/local/lib/palworld/rest-client.py", "metrics"], text=True, capture_output=True)
if rest.returncode: issues.append("REST API health check failed")
state = ops.STATE / "health.json"; previous = ops.read_json(state, {})
ops.atomic_json(state, {"checked_at": int(time.time()), "ok": not issues, "issues": issues})
if previous.get("ok") is not None and previous.get("ok") != (not issues):
    ops.notify("Palworld recovered" if not issues else "Palworld health failure", "; ".join(issues) or "All health checks pass", "info" if not issues else "error")
    ops.audit("health.transition", "ok" if not issues else "failed", issues=issues)
if issues:
    message = "Palworld health failure on " + socket.gethostname() + ": " + "; ".join(issues)
    raise SystemExit(message)
