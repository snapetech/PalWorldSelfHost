#!/usr/bin/env python3
"""Loopback-only PalWorldSelfHost operational API and static console."""
import importlib.util, json, os, pathlib, shutil, subprocess, time, uuid
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parent; BACKUPS = pathlib.Path(os.environ["PALWORLD_BACKUP_LOCAL_ROOT"])
TOKEN = os.environ["PALWORLD_OPS_TOKEN"]; LIB = pathlib.Path("/usr/local/lib/palworld")
spec = importlib.util.spec_from_file_location("ops_lib", LIB / "ops-lib.py")
ops = importlib.util.module_from_spec(spec); spec.loader.exec_module(ops)

def run(*args, timeout=30):
    p = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    return {"ok": p.returncode == 0, "code": p.returncode, "output": (p.stdout + p.stderr).strip()[-100000:]}

def rest(endpoint, *args):
    result = run(str(LIB / "rest-client.py"), endpoint, *args)
    if not result["ok"]: return None
    return json.loads(result["output"])

def newest_backup():
    files = sorted(BACKUPS.glob("palworld-*.tar.zst"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files: return None
    p = files[0]; manifest = ops.read_json(pathlib.Path(str(p) + ".manifest.json"), {})
    return {"name": p.name, "bytes": p.stat().st_size, "age_seconds": int(time.time() - p.stat().st_mtime), "manifest": manifest}

def audit_tail(limit=100):
    try: lines = (ops.STATE / "audit.jsonl").read_text().splitlines()[-limit:]
    except FileNotFoundError: return []
    return [json.loads(line) for line in reversed(lines)]

def status():
    settings = rest("settings") or {}; disk = shutil.disk_usage(BACKUPS)
    timer = run("systemctl", "show", "palworld-maintenance.timer", "-p", "NextElapseUSecRealtime", "--value")
    return {"service": run("systemctl", "is-active", "palworld.service")["output"], "metrics": rest("metrics"),
            "info": rest("info"), "players": rest("players"), "settings": settings, "backup": newest_backup(),
            "next_maintenance": timer["output"], "maintenance": ops.read_json(ops.STATE / "maintenance.json", {}),
            "update": ops.read_json(ops.STATE / "update.json", {}), "health": ops.read_json(ops.STATE / "health.json", {}),
            "disk": {"free": disk.free, "total": disk.total}, "jobs": ops.read_json(ops.STATE / "jobs.json", [])}

def public_status():
    metrics = rest("metrics") or {}; info = rest("info") or {}; roster = (rest("players") or {}).get("players", [])
    players = [{key: player.get(key) for key in ("name", "level", "ping", "location_x", "location_y")} for player in roster]
    maintenance = run("systemctl", "show", "palworld-maintenance.timer", "-p", "NextElapseUSecRealtime", "--value")["output"]
    history = ops.read_json(ops.STATE / "public-history.json", [])[-672:]
    return {"generated_at": int(time.time()), "online": run("systemctl", "is-active", "palworld.service")["output"] == "active",
            "name": info.get("servername"), "version": info.get("version"), "uptime": metrics.get("uptime", 0),
            "day": metrics.get("days"), "next_maintenance": maintenance, "maintenance_result": ops.read_json(ops.STATE / "maintenance.json", {}),
            "player_count": len(players), "max_players": metrics.get("maxplayernum", 32), "players": players, "history": history}

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs): super().__init__(*args, directory=str(ROOT / "static"), **kwargs)
    def log_message(self, fmt, *args): pass
    def send_json(self, payload, code=200):
        body = json.dumps(payload).encode(); self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store"); self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def body(self):
        size = min(int(self.headers.get("Content-Length", "0")), 65536)
        return json.loads(self.rfile.read(size) or b"{}")
    def authorized(self): return self.headers.get("X-Palworld-Ops-Token") == TOKEN
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/public-status": return self.send_json(public_status())
        if path == "/api/status": return self.send_json(status())
        if path == "/api/audit": return self.send_json(audit_tail()) if self.authorized() else self.send_json({"error": "unauthorized"}, 401)
        if path == "/api/settings/schema":
            if not self.authorized(): return self.send_json({"error": "unauthorized"}, 401)
            result = run(str(LIB / "settings-manager.py"), "schema"); return self.send_json(json.loads(result["output"]) if result["ok"] else result, 200 if result["ok"] else 500)
        return super().do_GET()
    def do_POST(self):
        if not self.authorized(): return self.send_json({"error": "unauthorized"}, 401)
        path = urlparse(self.path).path; data = self.body()
        simple = {"save": [str(LIB / "rest-client.py"), "save"],
                  "restart": ["sudo", "-n", str(LIB / "graceful-restart.sh")],
                  "backup": ["sudo", "-n", str(LIB / "backup.sh"), "daily"]}
        if path.startswith("/api/action/"):
            action = path.rsplit("/", 1)[-1]
            if action in simple:
                result = run(*simple[action], timeout=3600); ops.audit(f"operator.{action}", "ok" if result["ok"] else "failed"); return self.send_json(result)
        if path == "/api/announce":
            result = run(str(LIB / "rest-client.py"), "announce", "--message", str(data.get("message", ""))); ops.audit("operator.announce", "ok" if result["ok"] else "failed"); return self.send_json(result)
        if path in {"/api/kick", "/api/ban", "/api/unban"}:
            action = path[5:]
            if os.environ.get("PALWORLD_MODERATION_ENABLED", "false").lower() != "true": return self.send_json({"error": "moderation disabled"}, 403)
            if action == "ban" and data.get("confirm") != "BAN PLAYER": return self.send_json({"error": "confirmation required"}, 400)
            result = run(str(LIB / "rest-client.py"), action, "--userid", str(data.get("userid", "")), "--message", str(data.get("message", "")))
            ops.audit(f"moderation.{action}", "ok" if result["ok"] else "failed", userid=data.get("userid"), reason=data.get("message", "")); return self.send_json(result)
        if path in {"/api/settings/plan", "/api/settings/apply"}:
            action = path.rsplit("/", 1)[-1]; command = [str(LIB / "settings-manager.py"), action, json.dumps(data.get("updates", {}))]
            if action == "apply": command += ["--confirm", str(data.get("confirm", ""))]
            result = run(*command); return self.send_json(json.loads(result["output"]) if result["ok"] else result, 200 if result["ok"] else 400)
        if path == "/api/jobs":
            jobs = ops.read_json(ops.STATE / "jobs.json", []); job = {"id": uuid.uuid4().hex, "enabled": True, **data}; jobs.append(job)
            ops.atomic_json(ops.STATE / "jobs.json", jobs); ops.audit("scheduler.create", "ok", job_id=job["id"]); return self.send_json(job, 201)
        if path.startswith("/api/jobs/") and path.endswith("/cancel"):
            job_id = path.split("/")[3]; jobs = ops.read_json(ops.STATE / "jobs.json", []); found = False
            for job in jobs:
                if job.get("id") == job_id: job["enabled"] = False; found = True
            ops.atomic_json(ops.STATE / "jobs.json", jobs); ops.audit("scheduler.cancel", "ok" if found else "missing", job_id=job_id)
            return self.send_json({"ok": found}, 200 if found else 404)
        return self.send_json({"error": "unknown action"}, 404)

if __name__ == "__main__":
    ThreadingHTTPServer((os.environ.get("PALWORLD_OPS_BIND", "127.0.0.1"), int(os.environ.get("PALWORLD_OPS_PORT", "8213"))), Handler).serve_forever()
