#!/usr/bin/env python3
"""Loopback-only PalWorldSelfHost operational API and static console."""

import json, os, shutil, subprocess, time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
BACKUPS = Path(os.environ["PALWORLD_BACKUP_LOCAL_ROOT"])
TOKEN = os.environ["PALWORLD_OPS_TOKEN"]
LIB = Path("/usr/local/lib/palworld")


def run(*args):
    p = subprocess.run(args, text=True, capture_output=True, timeout=20)
    return {"ok": p.returncode == 0, "code": p.returncode, "output": (p.stdout + p.stderr).strip()[-4000:]}


def rest(endpoint):
    p = subprocess.run([str(LIB / "rest-client.py"), endpoint], text=True, capture_output=True, timeout=10)
    if p.returncode: return None
    return json.loads(p.stdout)


def newest_backup():
    files = sorted(BACKUPS.glob("palworld-*.tar.zst"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files: return None
    p = files[0]
    return {"name": p.name, "bytes": p.stat().st_size, "age_seconds": int(time.time() - p.stat().st_mtime)}


def status():
    service = run("systemctl", "is-active", "palworld.service")
    timer = run("systemctl", "show", "palworld-maintenance.timer", "-p", "NextElapseUSecRealtime", "--value")
    disk = shutil.disk_usage(BACKUPS)
    settings = rest("settings") or {}
    return {"service": service["output"], "metrics": rest("metrics"), "info": rest("info"),
            "players": rest("players"), "settings": settings, "backup": newest_backup(),
            "next_maintenance": timer["output"], "disk": {"free": disk.free, "total": disk.total}}


def public_status():
    metrics = rest("metrics") or {}
    info = rest("info") or {}
    roster = (rest("players") or {}).get("players", [])
    players = [{key: player.get(key) for key in
                ("name", "level", "ping", "location_x", "location_y")}
               for player in roster]
    maintenance = run("systemctl", "show", "palworld-maintenance.timer", "-p", "NextElapseUSecRealtime", "--value")["output"]
    return {"generated_at": int(time.time()), "online": run("systemctl", "is-active", "palworld.service")["output"] == "active",
            "name": info.get("servername"), "version": info.get("version"),
            "uptime": metrics.get("uptime", 0), "day": metrics.get("days"),
            "next_maintenance": maintenance, "player_count": len(players),
            "max_players": metrics.get("maxplayernum", 32), "players": players}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs): super().__init__(*args, directory=str(ROOT / "static"), **kwargs)
    def log_message(self, fmt, *args): pass
    def send_json(self, payload, code=200):
        body = json.dumps(payload).encode(); self.send_response(code)
        self.send_header("Content-Type", "application/json"); self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if urlparse(self.path).path == "/api/public-status": return self.send_json(public_status())
        if urlparse(self.path).path == "/api/status": return self.send_json(status())
        return super().do_GET()
    def do_POST(self):
        if self.headers.get("X-Palworld-Ops-Token") != TOKEN: return self.send_json({"error": "unauthorized"}, 401)
        action = urlparse(self.path).path.removeprefix("/api/action/")
        commands = {"save": [str(LIB / "rest-client.py"), "save"],
                    "restart": ["sudo", "-n", "systemctl", "restart", "palworld.service"],
                    "backup": ["sudo", "-n", str(LIB / "backup.sh")]}
        if action not in commands: return self.send_json({"error": "unknown action"}, 404)
        return self.send_json(run(*commands[action]))


if __name__ == "__main__":
    ThreadingHTTPServer((os.environ.get("PALWORLD_OPS_BIND", "127.0.0.1"), int(os.environ.get("PALWORLD_OPS_PORT", "8213"))), Handler).serve_forever()
