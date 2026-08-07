#!/usr/bin/env python3
"""Private, role-aware PalWorldSelfHost operational API and console."""
import hmac
import importlib.util
import json
import math
import os
import pathlib
import re
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
from collections import defaultdict, deque
from http import cookies
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import parse_qs, unquote, urlparse

ROOT = pathlib.Path(__file__).resolve().parent
BACKUPS = pathlib.Path(os.environ.get("PALWORLD_BACKUP_LOCAL_ROOT", "/var/backups/palworld"))
PUBLIC = pathlib.Path(os.environ.get("PALWORLD_PUBLIC_DIR", "/srv/static/palworld"))
LIB = pathlib.Path(os.environ.get("PALWORLD_LIB_DIR", "/usr/local/lib/palworld"))
if not (LIB / "ops-lib.py").is_file():
    candidate = ROOT.parent / "scripts"
    if (candidate / "ops-lib.py").is_file():
        LIB = candidate

ADMIN_TOKEN = os.environ.get("PALWORLD_OPS_TOKEN", "")
MODERATOR_TOKEN = os.environ.get("PALWORLD_OPS_MODERATOR_TOKEN", "")
VIEWER_TOKEN = os.environ.get("PALWORLD_OPS_VIEWER_TOKEN", "")
INTEGRATION_TOKEN = os.environ.get("PALWORLD_OPS_INTEGRATION_TOKEN", "")
SESSION_TTL = max(300, min(int(os.environ.get("PALWORLD_OPS_SESSION_TTL_SECONDS", "28800")), 86400))
SECURE_COOKIE = os.environ.get("PALWORLD_OPS_SECURE_COOKIE", "false").lower() == "true"
ROLE_LEVEL = {"viewer": 1, "moderator": 2, "admin": 3}
LOG_UNITS = {
    "palworld.service",
    "palworld-ops.service",
    "palworld-maintenance.service",
    "palworld-health.service",
    "palworld-update-check.service",
    "palworld-save-intelligence.service",
    "palworld-game-data.service",
    "palworld-backup-hourly.service",
    "palworld-backup-weekly.service",
}
EVENT_PRESETS = {"double_xp", "resource_rush", "capture_weekend"}

spec = importlib.util.spec_from_file_location("ops_lib", LIB / "ops-lib.py")
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)
pairing_spec = importlib.util.spec_from_file_location("pairing", LIB / "pairing.py")
pairing = importlib.util.module_from_spec(pairing_spec)
pairing_spec.loader.exec_module(pairing)

SESSIONS = {}
SESSION_LOCK = threading.Lock()
LOGIN_ATTEMPTS = defaultdict(deque)
LOGIN_LOCK = threading.Lock()


def run(*args, timeout=30, cwd=None):
    try:
        process = subprocess.run(args, text=True, capture_output=True, timeout=timeout, cwd=cwd)
        return {
            "ok": process.returncode == 0,
            "code": process.returncode,
            "output": (process.stdout + process.stderr).strip()[-100000:],
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "code": 124, "output": str(exc)[-100000:]}


def rest(endpoint, *args):
    result = run(str(LIB / "rest-client.py"), endpoint, *args)
    if not result["ok"]:
        return None
    try:
        return json.loads(result["output"])
    except json.JSONDecodeError:
        return None


def secure_equal(left, right):
    return bool(left and right) and hmac.compare_digest(str(left), str(right))


def role_for_token(value):
    if secure_equal(value, ADMIN_TOKEN):
        return "admin"
    if secure_equal(value, MODERATOR_TOKEN):
        return "moderator"
    if secure_equal(value, VIEWER_TOKEN):
        return "viewer"
    return None


def new_session(role):
    now = int(time.time())
    session_id = secrets.token_urlsafe(32)
    session = {
        "role": role,
        "csrf": secrets.token_urlsafe(24),
        "created_at": now,
        "expires_at": now + SESSION_TTL,
    }
    with SESSION_LOCK:
        SESSIONS[session_id] = session
        expired = [key for key, value in SESSIONS.items() if value["expires_at"] <= now]
        for key in expired:
            SESSIONS.pop(key, None)
    return session_id, session


def get_session(session_id):
    if not session_id:
        return None
    now = int(time.time())
    with SESSION_LOCK:
        session = SESSIONS.get(session_id)
        if not session or session["expires_at"] <= now:
            SESSIONS.pop(session_id, None)
            return None
        return dict(session)


def revoke_session(session_id):
    if session_id:
        with SESSION_LOCK:
            SESSIONS.pop(session_id, None)


def login_allowed(address, now=None):
    now = now or time.time()
    with LOGIN_LOCK:
        attempts = LOGIN_ATTEMPTS[address]
        while attempts and attempts[0] < now - 300:
            attempts.popleft()
        if len(attempts) >= 10:
            return False
        attempts.append(now)
        return True


def bounded_integer(query, name, default, minimum, maximum):
    raw = query.get(name, [str(default)])[0]
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer")
    return max(minimum, min(value, maximum))


def redact_text(value):
    value = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<ip-redacted>", str(value))
    value = re.sub(
        r"(?i)\b(steam_|account_|user_?id[=: ]+)[A-Za-z0-9_-]+",
        r"\1<id-redacted>",
        value,
    )
    value = re.sub(
        r"(?i)(password|token|secret|authorization)([=: ]+)[^\s,;]+",
        r"\1\2<redacted>",
        value,
    )
    return value


def redact(value):
    sensitive_keys = {
        "ip", "userid", "user_id", "steamid", "platformid", "authorization",
        "adminpassword", "serverpassword", "password", "token", "secret",
    }
    if isinstance(value, dict):
        return {
            key: ("<redacted>" if key.lower() in sensitive_keys else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return redact_text(value) if isinstance(value, str) else value


def newest_backup():
    backups = list_backups(limit=1)
    return backups[0] if backups else None


def backup_path(name):
    decoded = unquote(name)
    if decoded != pathlib.Path(decoded).name or not re.fullmatch(r"palworld-[A-Za-z0-9_.-]+\.tar\.zst", decoded):
        return None
    candidate = (BACKUPS / decoded).resolve()
    try:
        candidate.relative_to(BACKUPS.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def backup_record(path):
    manifest_path = pathlib.Path(str(path) + ".manifest.json")
    manifest = ops.read_json(manifest_path, {})
    return {
        "name": path.name,
        "bytes": path.stat().st_size,
        "created_at": int(path.stat().st_mtime),
        "age_seconds": max(0, int(time.time() - path.stat().st_mtime)),
        "tier": manifest.get("tier") or path.name.split("-", 2)[1],
        "checksum": pathlib.Path(str(path) + ".sha256").is_file(),
        "manifest": manifest,
    }


def list_backups(limit=200):
    if not BACKUPS.is_dir():
        return []
    files = sorted(BACKUPS.glob("palworld-*.tar.zst"), key=lambda path: path.stat().st_mtime, reverse=True)
    return [backup_record(path) for path in files[: max(1, min(limit, 500))]]


def audit_tail(limit=100):
    try:
        lines = (ops.STATE / "audit.jsonl").read_text().splitlines()[-limit:]
    except FileNotFoundError:
        return []
    events = []
    for line in reversed(lines):
        try:
            events.append(redact(json.loads(line)))
        except json.JSONDecodeError:
            continue
    return events


def history_samples(hours=24, limit=720):
    database = ops.STATE / "history.sqlite3"
    if not database.is_file():
        return []
    since = int(time.time()) - max(1, min(hours, 24 * 90)) * 3600
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        rows = connection.execute(
            "select ts,players,fps,frame_ms,uptime,day,rss_bytes,cpu_ticks,restarts "
            "from samples where ts >= ? order by ts",
            (since,),
        ).fetchall()
        connection.close()
    except sqlite3.Error:
        return []
    stride = max(1, math.ceil(len(rows) / max(10, min(limit, 2000))))
    selected = rows[::stride]
    if rows and selected[-1] != rows[-1]:
        selected.append(rows[-1])
    keys = ("timestamp", "players", "fps", "frame_ms", "uptime", "day", "rss_bytes", "cpu_ticks", "restarts")
    return [dict(zip(keys, row)) for row in selected]


def player_history(role="viewer", limit=200, search=""):
    database = ops.STATE / "history.sqlite3"
    if not database.is_file(): return []
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        rows = connection.execute(
            "select user_key,name,first_seen,last_seen,sessions,total_seconds,online,joined_at "
            "from player_presence where lower(name) like ? order by online desc,last_seen desc limit ?",
            (f"%{search.casefold()}%", max(1, min(limit, 500))),
        ).fetchall()
        connection.close()
    except sqlite3.Error:
        return []
    keys = ("user_key", "name", "first_seen", "last_seen", "sessions", "total_seconds", "online", "joined_at")
    result = [dict(zip(keys, row)) for row in rows]
    if ROLE_LEVEL.get(role, 0) < ROLE_LEVEL["moderator"]:
        for player in result: player.pop("user_key", None)
    return result


def save_intelligence(role="viewer"):
    data = ops.read_json(ops.STATE / "save-intelligence.json", {})
    if not data:
        return {"status": "never_scanned", "counts": {}, "schema": {}, "players": [],
                "guilds": [], "bases": [], "map_objects": []}
    if ROLE_LEVEL.get(role, 0) >= ROLE_LEVEL["moderator"]:
        return data

    identifier_keys = {
        "uid", "instance_id", "owner_uid", "admin_uid", "world", "sha256",
    }

    def hide(value):
        if isinstance(value, dict):
            return {key: ("<redacted>" if key in identifier_keys else hide(item))
                    for key, item in value.items()}
        if isinstance(value, list):
            return [hide(item) for item in value]
        return value

    return hide(data)


def game_data():
    data = ops.read_json(ops.STATE / "game-data.json", {})
    if not data:
        return {"state": "never_polled", "counts": {}, "actors": []}
    captured = data.get("captured_at")
    if captured and (not isinstance(captured, (int, float)) or time.time() - captured > 180):
        data["state"] = "stale"
        data["actors"] = []
    return data


def map_locations():
    raw = ops.read_json(PUBLIC / "locations.json", [])
    if not isinstance(raw, list) or len(raw) > 2000:
        return []
    result = []
    for row in raw:
        location = row.get("location", {}) if isinstance(row, dict) else {}
        values = [location.get(axis) for axis in ("X", "Y", "Z")]
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in values):
            continue
        result.append({
            "id": str(row.get("id", ""))[:100], "label": str(row.get("label", ""))[:160],
            "type": str(row.get("type", ""))[:40],
            "position": dict(zip(("x", "y", "z"), values)),
        })
    return result


def integration_openapi():
    paths = {}
    for path, summary in {
        "/api/v1/integration/status": "Sanitized server and player status",
        "/api/v1/integration/events": "Bounded redacted operational events",
        "/api/v1/integration/game-data": "Aggregate optional Game Data capability and counts",
    }.items():
        paths[path] = {"get": {"summary": summary, "security": [{"integrationToken": []}],
            "responses": {"200": {"description": "Success"}, "401": {"description": "Invalid integration token"}}}}
    return {"openapi": "3.1.0", "info": {"title": "PalWorldSelfHost read-only integration API", "version": "1"},
            "paths": paths, "components": {"securitySchemes": {"integrationToken": {
                "type": "http", "scheme": "bearer", "description": "Dedicated read-only integration token",
            }}}}


def integration_game_data():
    data = game_data()
    return {key: data.get(key) for key in (
        "state", "captured_at", "last_attempt_at", "last_error", "http_status",
        "fps", "average_fps", "counts", "truncated",
    )}


def status(role="admin"):
    settings = rest("settings") or {}
    try:
        disk = shutil.disk_usage(BACKUPS)
    except FileNotFoundError:
        disk = shutil.disk_usage(BACKUPS.parent if BACKUPS.parent.exists() else "/")
    timer = run("systemctl", "show", "palworld-maintenance.timer", "-p", "NextElapseUSecRealtime", "--value")
    players = rest("players") or {}
    if role == "viewer":
        players = redact(players)
        settings = redact(settings)
    return {
        "service": run("systemctl", "is-active", "palworld.service")["output"],
        "metrics": rest("metrics"),
        "info": rest("info"),
        "players": players,
        "settings": settings,
        "backup": newest_backup(),
        "next_maintenance": timer["output"],
        "maintenance": ops.read_json(ops.STATE / "maintenance.json", {}),
        "update": ops.read_json(ops.STATE / "update.json", {}),
        "health": ops.read_json(ops.STATE / "health.json", {}),
        "auto_pause": ops.read_json(ops.STATE / "auto-pause.json", {}),
        "exposure": redact(ops.read_json(ops.STATE / "exposure.json", {})),
        "disk": {"free": disk.free, "total": disk.total},
        "jobs": ops.read_json(ops.STATE / "jobs.json", []),
        "role": role,
    }


def public_status():
    metrics = rest("metrics") or {}
    info = rest("info") or {}
    roster = (rest("players") or {}).get("players", [])
    players = [
        {key: player.get(key) for key in ("name", "level", "ping", "location_x", "location_y")}
        for player in roster
    ]
    maintenance = run("systemctl", "show", "palworld-maintenance.timer", "-p", "NextElapseUSecRealtime", "--value")["output"]
    history = ops.read_json(ops.STATE / "public-history.json", [])[-672:]
    return {
        "generated_at": int(time.time()),
        "online": run("systemctl", "is-active", "palworld.service")["output"] == "active",
        "name": info.get("servername"),
        "version": info.get("version"),
        "uptime": metrics.get("uptime", 0),
        "day": metrics.get("days"),
        "next_maintenance": maintenance,
        "maintenance_result": ops.read_json(ops.STATE / "maintenance.json", {}),
        "player_count": len(players),
        "max_players": metrics.get("maxplayernum", 32),
        "players": players,
        "history": history,
    }


class Handler(SimpleHTTPRequestHandler):
    server_version = "PalWorldSelfHost/1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "static"), **kwargs)

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        super().end_headers()

    def send_json(self, payload, code=200, extra_headers=None):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_download(self, path, content_type="application/zstd", download_name=None):
        download_name = download_name or path.name
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(path.stat().st_size))
        self.end_headers()
        with path.open("rb") as source:
            shutil.copyfileobj(source, self.wfile, length=1024 * 1024)

    def body(self):
        try:
            declared = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            declared = 0
        if declared < 0 or declared > 65536:
            raise ValueError("request body exceeds 64 KiB")
        raw = self.rfile.read(declared)
        return json.loads(raw or b"{}")

    def session_id(self):
        jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        item = jar.get("palworld_ops_session")
        return item.value if item else None

    def identity(self):
        legacy = role_for_token(self.headers.get("X-Palworld-Ops-Token", ""))
        if legacy:
            return {"role": legacy, "legacy": True, "csrf": None}
        session = get_session(self.session_id())
        if session:
            session["legacy"] = False
        return session

    def require_integration(self):
        header = self.headers.get("Authorization", "")
        token = header[7:] if header.startswith("Bearer ") and len(header) <= 519 else ""
        if not INTEGRATION_TOKEN or not secure_equal(token, INTEGRATION_TOKEN):
            self.send_json({"error": "valid read-only integration token required"}, 401,
                           {"WWW-Authenticate": 'Bearer realm="palworld-integration"'})
            return False
        return True

    def session_response(self, role):
        session_id, session = new_session(role)
        secure = "; Secure" if SECURE_COOKIE else ""
        cookie = f"palworld_ops_session={session_id}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL}{secure}"
        return self.send_json(
            {"authenticated": True, "role": role, "csrf": session["csrf"], "expires_at": session["expires_at"]},
            extra_headers={"Set-Cookie": cookie},
        )

    def require(self, minimum="viewer", *, csrf=False):
        identity = self.identity()
        if not identity:
            self.send_json({"error": "authentication required"}, 401)
            return None
        if ROLE_LEVEL[identity["role"]] < ROLE_LEVEL[minimum]:
            self.send_json({"error": f"{minimum} role required"}, 403)
            return None
        if csrf and not identity["legacy"] and not secure_equal(self.headers.get("X-CSRF-Token", ""), identity["csrf"]):
            self.send_json({"error": "valid CSRF token required"}, 403)
            return None
        return identity

    def origin_allowed(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return urlparse(origin).netloc == self.headers.get("Host", "")

    def route(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/v1/"):
            return path[7:], parse_qs(parsed.query)
        if path == "/api/public-status":
            return "/public-status", parse_qs(parsed.query)
        if path.startswith("/api/"):
            return path[4:], parse_qs(parsed.query)
        return path, parse_qs(parsed.query)

    def do_GET(self):
        path, query = self.route()
        if path == "/public-status":
            return self.send_json(public_status())
        if path == "/openapi.json":
            return self.send_json(integration_openapi())
        if path in {"/integration/status", "/integration/events", "/integration/game-data"}:
            if not self.require_integration():
                return
            if path == "/integration/status":
                return self.send_json(public_status())
            if path == "/integration/game-data":
                return self.send_json(integration_game_data())
            try:
                limit = bounded_integer(query, "limit", 50, 1, 100)
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, 400)
            return self.send_json({"events": audit_tail(limit)})
        if path == "/auth/session":
            identity = self.identity()
            if not identity:
                return self.send_json({"authenticated": False}, 401)
            return self.send_json({"authenticated": True, "role": identity["role"], "csrf": identity["csrf"]})

        identity = self.require("viewer") if path.startswith("/") and path in {
            "/status", "/audit", "/settings/schema", "/settings/raw", "/server-config/schema", "/server-config/raw", "/mod-config", "/mod-lifecycle", "/logs", "/metrics/history", "/players/history", "/events", "/backups",
            "/diagnostics/bundle", "/diagnostics/exposure", "/diagnostics/config", "/updates/status", "/builds", "/save/intelligence", "/save/repair", "/game-data", "/map/locations", "/files", "/rcon", "/whitelist", "/player-actions",
        } else None
        if path in {
            "/status", "/audit", "/settings/schema", "/settings/raw", "/server-config/schema", "/server-config/raw", "/mod-config", "/mod-lifecycle", "/logs", "/metrics/history", "/players/history", "/events", "/backups",
            "/diagnostics/bundle", "/diagnostics/exposure", "/diagnostics/config", "/updates/status", "/builds", "/save/intelligence", "/save/repair", "/game-data", "/map/locations", "/files", "/rcon", "/whitelist", "/player-actions",
        } and not identity:
            return
        if path == "/status":
            return self.send_json(status(identity["role"]))
        if path in {"/audit", "/events"}:
            try:
                limit = bounded_integer(query, "limit", 100, 1, 500)
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, 400)
            return self.send_json(audit_tail(limit))
        if path == "/settings/schema":
            result = run(str(LIB / "settings-manager.py"), "schema")
            return self.send_json(json.loads(result["output"]) if result["ok"] else result, 200 if result["ok"] else 500)
        if path == "/settings/raw":
            if ROLE_LEVEL[identity["role"]] < ROLE_LEVEL["admin"]:
                return self.send_json({"error": "admin role required"}, 403)
            result = run(str(LIB / "settings-manager.py"), "raw-show")
            return self.send_json(json.loads(result["output"]) if result["ok"] else result, 200 if result["ok"] else 500)
        if path == "/server-config/schema":
            if ROLE_LEVEL[identity["role"]] < ROLE_LEVEL["admin"]:
                return self.send_json({"error": "admin role required"}, 403)
            result = run(str(LIB / "server-config-manager.py"), "schema")
            return self.send_json(json.loads(result["output"]) if result["ok"] else result, 200 if result["ok"] else 500)
        if path == "/server-config/raw":
            if ROLE_LEVEL[identity["role"]] < ROLE_LEVEL["admin"]:
                return self.send_json({"error": "admin role required"}, 403)
            result = run(str(LIB / "server-config-manager.py"), "raw-show")
            return self.send_json(json.loads(result["output"]) if result["ok"] else result, 200 if result["ok"] else 500)
        if path == "/mod-config":
            if ROLE_LEVEL[identity["role"]] < ROLE_LEVEL["admin"]:
                return self.send_json({"error": "admin role required"}, 403)
            result = run(str(LIB / "mod-config-manager.py"), "status")
            try: payload = json.loads(result["output"])
            except json.JSONDecodeError: payload = result
            return self.send_json(payload, 200 if result["ok"] else 400)
        if path == "/mod-lifecycle":
            if ROLE_LEVEL[identity["role"]] < ROLE_LEVEL["admin"]:
                return self.send_json({"error": "admin role required"}, 403)
            result = run(str(LIB / "mod-lifecycle.py"), "status")
            try: payload = json.loads(result["output"])
            except json.JSONDecodeError: payload = result
            return self.send_json(payload, 200 if result["ok"] else 400)
        if path == "/player-actions":
            if ROLE_LEVEL[identity["role"]] < ROLE_LEVEL["admin"]:
                return self.send_json({"error": "admin role required"}, 403)
            result = run(str(LIB / "player-actions.py"), "capabilities")
            try: payload = json.loads(result["output"])
            except json.JSONDecodeError: payload = result
            return self.send_json(payload, 200 if result["ok"] else 400)
        if path == "/logs":
            unit = query.get("unit", ["palworld.service"])[0]
            if unit not in LOG_UNITS:
                return self.send_json({"error": "unknown log unit"}, 400)
            try:
                lines = bounded_integer(query, "lines", 200, 10, 1000)
                since_minutes = bounded_integer(query, "since_minutes", 60, 1, 10080)
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, 400)
            needle = query.get("filter", [""])[0].strip()
            if len(needle) > 80 or any(character in needle for character in "\r\n\0"):
                return self.send_json({"error": "filter must be a single line of at most 80 characters"}, 400)
            fetch_lines = min(5000, max(lines, lines * 5 if needle else lines))
            result = run(
                "journalctl", "-u", unit, "-n", str(fetch_lines),
                "--since", f"-{since_minutes} minutes", "--no-pager", "--output=short-iso", timeout=15,
            )
            result["output"] = redact_text(result["output"])
            selected = result["output"].splitlines()
            if needle:
                selected = [line for line in selected if needle.casefold() in line.casefold()]
            result["output"] = "\n".join(selected[-lines:])
            return self.send_json({
                "unit": unit, "lines": lines, "since_minutes": since_minutes,
                "filter": needle, "matched": len(selected), **result,
            }, 200 if result["ok"] else 503)
        if path == "/metrics/history":
            try:
                hours = bounded_integer(query, "hours", 24, 1, 2160)
                limit = bounded_integer(query, "limit", 720, 10, 2000)
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, 400)
            return self.send_json({"hours": hours, "samples": history_samples(hours, limit)})
        if path == "/players/history":
            try: limit = bounded_integer(query, "limit", 200, 1, 500)
            except ValueError as exc: return self.send_json({"error": str(exc)}, 400)
            search = query.get("search", [""])[0].strip()
            if len(search) > 80 or any(character in search for character in "\r\n\0"):
                return self.send_json({"error": "search must be a single line of at most 80 characters"}, 400)
            return self.send_json({"players": player_history(identity["role"], limit, search)})
        if path == "/save/intelligence":
            return self.send_json(save_intelligence(identity["role"]))
        if path == "/save/repair":
            if ROLE_LEVEL[identity["role"]] < ROLE_LEVEL["admin"]:
                return self.send_json({"error": "admin role required"}, 403)
            return self.send_json({
                "status": "available",
                "actions": {
                    "rename_player": {"confirmation": "RENAME PLAYER", "requires": ["uid", "new_name"]},
                    "migrate_player": {"confirmation": "MIGRATE PLAYER", "requires": ["old_uid", "new_uid"]},
                    "replace_player": {"confirmation": "REPLACE PLAYER IDENTITY", "requires": ["old_uid", "target_uid"]},
                    "cleanup_duplicates": {"confirmation": "CLEAN DUPLICATE PLAYERS", "requires": []},
                    "cleanup_graph": {"confirmation": "CLEAN SAVE GRAPH", "requires": []},
                    "delete_inactive_players": {"confirmation": "DELETE INACTIVE PLAYERS", "requires": ["inactive_days"]},
                    "transfer_player": {"confirmation": "TRANSFER PLAYER", "requires": ["source_world_id", "source_uid", "target_uid"]},
                    "edit_inventory_slot": {"confirmation": "EDIT INVENTORY SLOT", "requires": ["uid", "container", "slot_index", "item_id", "stack_count"]},
                    "edit_player_progression": {"confirmation": "EDIT PLAYER PROGRESSION", "requires": ["uid"]},
                    "edit_owned_pal": {"confirmation": "EDIT OWNED PAL", "requires": ["uid", "pal_instance_id"]},
                },
                "unsupported": [],
                "last": ops.read_json(ops.STATE / "save-repair-last.json", None),
            })
        if path == "/game-data":
            return self.send_json(game_data())
        if path == "/map/locations":
            return self.send_json({"locations": map_locations()})
        if path == "/files":
            if ROLE_LEVEL[identity["role"]] < ROLE_LEVEL["admin"]:
                return self.send_json({"error": "admin role required"}, 403)
            root = query.get("root", ["config"])[0]
            relative = query.get("path", ["."])[0]
            action = "read" if query.get("read", ["false"])[0].lower() == "true" else "list"
            result = run("python3", str(LIB / "file-manager.py"), action, root, relative)
            try: payload = json.loads(result["output"])
            except json.JSONDecodeError: payload = result
            return self.send_json(payload, 200 if result["ok"] else 400)
        if path in {"/rcon", "/whitelist"}:
            if ROLE_LEVEL[identity["role"]] < ROLE_LEVEL["admin"]:
                return self.send_json({"error": "admin role required"}, 403)
            script = "rcon-console.py" if path == "/rcon" else "whitelist-manager.py"
            action = "overview" if path == "/rcon" else "status"
            result = run(str(LIB / script), action)
            try:
                payload = json.loads(result["output"])
            except json.JSONDecodeError:
                return self.send_json(result, 500)
            return self.send_json(payload, 200 if result["ok"] else 503)
        if path == "/backups":
            return self.send_json({"backups": list_backups()})
        if path == "/diagnostics/exposure":
            return self.send_json(redact(ops.read_json(ops.STATE / "exposure.json", {})))
        if path == "/diagnostics/config":
            if ROLE_LEVEL[identity["role"]] < ROLE_LEVEL["admin"]:
                return self.send_json({"error": "admin role required"}, 403)
            result = run(str(LIB / "config-recovery.py"), "health")
            return self.send_json(json.loads(result["output"]) if result["ok"] else result, 200 if result["ok"] else 500)
        if path == "/updates/status":
            return self.send_json(ops.read_json(ops.STATE / "update.json", {}))
        if path == "/builds":
            result = run(str(LIB / "steam-build-manager.py"), "list", timeout=60)
            return self.send_json(json.loads(result["output"]) if result["ok"] else result, 200 if result["ok"] else 500)
        if path == "/diagnostics/bundle":
            if ROLE_LEVEL[identity["role"]] < ROLE_LEVEL["admin"]:
                return self.send_json({"error": "admin role required"}, 403)
            with tempfile.TemporaryDirectory(prefix="palworld-support-") as temporary:
                result = run(str(LIB / "diagnose.py"), timeout=120, cwd=temporary)
                if not result["ok"]:
                    ops.audit("diagnostics.download", "failed", role=identity["role"])
                    return self.send_json(result, 500)
                output_lines = result["output"].splitlines()
                if not output_lines:
                    return self.send_json({"error": "diagnostic generator returned no path"}, 500)
                candidate = pathlib.Path(output_lines[-1]).resolve()
                try:
                    candidate.relative_to(pathlib.Path(temporary).resolve())
                except ValueError:
                    return self.send_json({"error": "diagnostic generator returned an unsafe path"}, 500)
                if not candidate.is_file() or not candidate.name.endswith(".tar.gz"):
                    return self.send_json({"error": "diagnostic bundle was not created"}, 500)
                ops.audit("diagnostics.download", "ok", role=identity["role"])
                return self.send_download(candidate, "application/gzip", candidate.name)

        match = re.fullmatch(r"/backups/([^/]+)(?:/(download))?", path)
        if match:
            identity = self.require("admin" if match.group(2) else "viewer")
            if not identity:
                return
            backup = backup_path(match.group(1))
            if not backup:
                return self.send_json({"error": "backup not found"}, 404)
            if match.group(2):
                ops.audit("backup.download", "ok", backup=backup.name, role=identity["role"])
                return self.send_download(backup)
            return self.send_json(backup_record(backup))
        return super().do_GET()

    def do_PUT(self):
        if not self.origin_allowed():
            return self.send_json({"error": "origin does not match host"}, 403)
        path, query = self.route()
        if path != "/files/upload":
            return self.send_json({"error": "unknown upload route"}, 404)
        identity = self.require("admin", csrf=True)
        if not identity:
            return
        try:
            declared = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            declared = -1
        if declared < 0 or declared > 64 * 1024 * 1024:
            return self.send_json({"error": "upload must declare at most 64 MiB"}, 413)
        root = query.get("root", [""])[0]
        relative = query.get("path", [""])[0]
        expected = self.headers.get("X-Content-SHA256", "").lower()
        confirmation = self.headers.get("X-File-Confirmation", "")
        ops.STATE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="file-upload-", dir=ops.STATE) as temporary:
            source = pathlib.Path(temporary) / "upload"
            remaining = declared
            with source.open("wb") as output:
                while remaining:
                    block = self.rfile.read(min(1024 * 1024, remaining))
                    if not block:
                        return self.send_json({"error": "upload body ended before declared length"}, 400)
                    output.write(block); remaining -= len(block)
                output.flush(); os.fsync(output.fileno())
            result = run("python3", str(LIB / "file-manager.py"), "upload", root, relative, str(source),
                         "--sha256", expected, "--confirm", confirmation, timeout=120)
        ops.audit("file.upload", "ok" if result["ok"] else "failed", root=root, path=relative, role=identity["role"])
        try: payload = json.loads(result["output"])
        except json.JSONDecodeError: payload = result
        return self.send_json(payload, 200 if result["ok"] else 400)

    def do_POST(self):
        if not self.origin_allowed():
            return self.send_json({"error": "origin does not match host"}, 403)
        path, _ = self.route()
        try:
            data = self.body()
        except (ValueError, json.JSONDecodeError) as exc:
            return self.send_json({"error": str(exc)}, 400)

        if path == "/auth/login":
            address = self.client_address[0]
            if not login_allowed(address):
                return self.send_json({"error": "too many login attempts; retry later"}, 429)
            role = role_for_token(str(data.get("token", "")))
            if not role:
                ops.audit("auth.login", "failed", address=redact_text(address))
                return self.send_json({"error": "invalid credential"}, 401)
            ops.audit("auth.login", "ok", role=role)
            return self.session_response(role)
        if path == "/auth/pair":
            address = self.client_address[0]
            if not login_allowed(address):
                return self.send_json({"error": "too many pairing attempts; retry later"}, 429)
            role = pairing.consume(str(data.get("code", "")))
            if not role:
                ops.audit("auth.pair", "failed", address=redact_text(address))
                return self.send_json({"error": "invalid or expired pairing code"}, 401)
            ops.audit("auth.pair", "ok", role=role)
            return self.session_response(role)
        if path == "/auth/logout":
            identity = self.require("viewer", csrf=True)
            if not identity:
                return
            revoke_session(self.session_id())
            return self.send_json(
                {"ok": True},
                extra_headers={"Set-Cookie": "palworld_ops_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"},
            )

        if path == "/files/quarantine":
            identity = self.require("admin", csrf=True)
            if not identity:
                return
            result = run("python3", str(LIB / "file-manager.py"), "quarantine",
                         str(data.get("root", "")), str(data.get("path", "")),
                         "--confirm", str(data.get("confirm", "")))
            ops.audit("file.quarantine", "ok" if result["ok"] else "failed",
                      root=str(data.get("root", "")), path=str(data.get("path", "")), role=identity["role"])
            try: payload = json.loads(result["output"])
            except json.JSONDecodeError: payload = result
            return self.send_json(payload, 200 if result["ok"] else 400)

        required = "moderator" if path in {"/announce", "/kick", "/ban", "/unban"} else "admin"
        identity = self.require(required, csrf=True)
        if not identity:
            return
        simple = {
            "save": [str(LIB / "rest-client.py"), "save"],
            "restart": ["sudo", "-n", str(LIB / "graceful-restart.sh")],
            "backup": ["sudo", "-n", str(LIB / "backup.sh"), "daily"],
            "start": ["sudo", "-n", str(LIB / "ops-lifecycle.sh"), "start"],
        }
        if path.startswith("/action/"):
            action = path.rsplit("/", 1)[-1]
            if action == "stop":
                if str(data.get("confirm", "")) != "STOP SERVER":
                    return self.send_json({"error": "confirmation required"}, 400)
                result = run("sudo", "-n", str(LIB / "ops-lifecycle.sh"), "stop", "STOP SERVER", timeout=300)
                ops.audit("operator.stop", "ok" if result["ok"] else "failed", role=identity["role"])
                return self.send_json(result, 200 if result["ok"] else 500)
            if action in simple:
                result = run(*simple[action], timeout=3600)
                ops.audit(f"operator.{action}", "ok" if result["ok"] else "failed", role=identity["role"])
                return self.send_json(result, 200 if result["ok"] else 500)
        if path == "/announce":
            result = run(str(LIB / "rest-client.py"), "announce", "--message", str(data.get("message", "")))
            ops.audit("operator.announce", "ok" if result["ok"] else "failed", role=identity["role"])
            return self.send_json(result, 200 if result["ok"] else 500)
        if path in {"/kick", "/ban", "/unban"}:
            action = path[1:]
            if os.environ.get("PALWORLD_MODERATION_ENABLED", "false").lower() != "true":
                return self.send_json({"error": "moderation disabled"}, 403)
            if action == "ban" and data.get("confirm") != "BAN PLAYER":
                return self.send_json({"error": "confirmation required"}, 400)
            result = run(
                str(LIB / "rest-client.py"), action,
                "--userid", str(data.get("userid", "")),
                "--message", str(data.get("message", "")),
            )
            ops.audit(
                f"moderation.{action}", "ok" if result["ok"] else "failed",
                userid=data.get("userid"), reason=data.get("message", ""), role=identity["role"],
            )
            return self.send_json(result, 200 if result["ok"] else 500)
        if path == "/rcon/execute":
            if data.get("confirm") != "EXECUTE RCON":
                return self.send_json({"error": "confirmation required"}, 400)
            payload = {
                "command": data.get("command", ""), "actor": identity["role"],
                "source": "browser", "confirm": data.get("confirm", ""),
            }
            result = run(str(LIB / "rcon-console.py"), "execute", json.dumps(payload))
            try: response = json.loads(result["output"])
            except json.JSONDecodeError: response = result
            return self.send_json(response, 200 if result["ok"] else 400)
        if path in {"/player-actions/plan", "/player-actions/execute"}:
            action = path.rsplit("/", 1)[-1]
            command = [str(LIB / "player-actions.py"), action, json.dumps(data.get("payload", {}))]
            if action == "execute":
                command += [
                    "--expected-sha256", str(data.get("expected_sha256", "")),
                    "--confirm", str(data.get("confirm", "")), "--actor", identity["role"],
                ]
            result = run(*command)
            try: response = json.loads(result["output"])
            except json.JSONDecodeError: response = result
            return self.send_json(response, 200 if result["ok"] else 400)
        if path in {"/save/repair/diagnose", "/save/repair/plan", "/save/repair/apply"}:
            action = path.rsplit("/", 1)[-1]
            command = ["sudo", "-n", str(LIB / "ops-save-repair.sh"), action]
            payload = data.get("payload", {})
            if action != "diagnose":
                if not isinstance(payload, dict):
                    return self.send_json({"error": "payload must be a JSON object"}, 400)
                command.append(json.dumps(payload, separators=(",", ":")))
            if action == "apply":
                command += [str(data.get("reviewed_sha256", "")), str(data.get("confirm", ""))]
            result = run(*command, timeout=3600)
            try: response = json.loads(result["output"])
            except json.JSONDecodeError: response = result
            ops.audit(f"save-repair.browser-{action}", "ok" if result["ok"] else "failed",
                      operation=str(payload.get("action", "diagnose")), role=identity["role"])
            return self.send_json(response, 200 if result["ok"] else 400)
        if path == "/rcon/saved":
            if data.get("confirm") != "SAVE RCON COMMAND":
                return self.send_json({"error": "confirmation required"}, 400)
            result = run(str(LIB / "rcon-console.py"), "saved-add", json.dumps({
                "name": data.get("name", ""), "command": data.get("command", ""),
            }))
            try: response = json.loads(result["output"])
            except json.JSONDecodeError: response = result
            return self.send_json(response, 201 if result["ok"] else 400)
        saved_match = re.fullmatch(r"/rcon/saved/(\d+)/(execute|delete)", path)
        if saved_match:
            identifier, action = saved_match.groups()
            expected = "EXECUTE RCON" if action == "execute" else "DELETE SAVED COMMAND"
            if data.get("confirm") != expected:
                return self.send_json({"error": "confirmation required"}, 400)
            if action == "execute":
                payload = json.dumps({"actor": identity["role"], "confirm": expected})
                result = run(str(LIB / "rcon-console.py"), "saved-execute", identifier, payload)
            else:
                result = run(str(LIB / "rcon-console.py"), "saved-delete", identifier)
            try: response = json.loads(result["output"])
            except json.JSONDecodeError: response = result
            return self.send_json(response, 200 if result["ok"] else 400)
        if path == "/rcon/toggle":
            if not isinstance(data.get("enabled"), bool):
                return self.send_json({"error": "enabled must be a boolean"}, 400)
            enabled = data["enabled"]
            expected = "ENABLE PRIVATE RCON" if enabled else "DISABLE PRIVATE RCON"
            if data.get("confirm") != expected:
                return self.send_json({"error": "confirmation required"}, 400)
            result = run(
                "sudo", "-n", str(LIB / "ops-rcon-toggle.sh"),
                "enable" if enabled else "disable", expected, timeout=3600,
            )
            ops.audit("rcon.toggle-browser", "ok" if result["ok"] else "failed", enabled=enabled, role=identity["role"])
            try: response = json.loads(result["output"])
            except json.JSONDecodeError: response = result
            return self.send_json(response, 200 if result["ok"] else 500)
        if path in {"/whitelist/plan", "/whitelist/replace"}:
            action = path.rsplit("/", 1)[-1]
            if action == "replace" and data.get("confirm") != "REPLACE WHITELIST":
                return self.send_json({"error": "confirmation required"}, 400)
            command = [str(LIB / "whitelist-manager.py"), action, json.dumps(data.get("entries", []))]
            if action == "replace": command += ["--confirm", "REPLACE WHITELIST"]
            result = run(*command)
            try: response = json.loads(result["output"])
            except json.JSONDecodeError: response = result
            return self.send_json(response, 200 if result["ok"] else 400)
        if path in {"/whitelist/enable", "/whitelist/disable"}:
            action = path.rsplit("/", 1)[-1]
            expected = f"{action.upper()} WHITELIST"
            if data.get("confirm") != expected:
                return self.send_json({"error": "confirmation required"}, 400)
            result = run(str(LIB / "whitelist-manager.py"), action, "--confirm", expected)
            try: response = json.loads(result["output"])
            except json.JSONDecodeError: response = result
            return self.send_json(response, 200 if result["ok"] else 400)
        if path in {"/settings/plan", "/settings/apply"}:
            action = path.rsplit("/", 1)[-1]
            if action == "apply" and data.get("confirm") != "APPLY SETTINGS":
                return self.send_json({"error": "confirmation required"}, 400)
            command = [str(LIB / "settings-manager.py"), action, json.dumps(data.get("updates", {}))]
            if action == "apply":
                command += ["--confirm", str(data.get("confirm", ""))]
            result = run(*command)
            return self.send_json(json.loads(result["output"]) if result["ok"] else result, 200 if result["ok"] else 400)
        if path == "/settings/rollback":
            if data.get("confirm") != "ROLLBACK SETTINGS":
                return self.send_json({"error": "confirmation required"}, 400)
            result = run(
                str(LIB / "settings-manager.py"), "rollback",
                "--confirm", str(data.get("confirm", "")),
            )
            return self.send_json(json.loads(result["output"]) if result["ok"] else result, 200 if result["ok"] else 400)
        if path in {"/server-config/plan", "/server-config/apply"}:
            action = path.rsplit("/", 1)[-1]
            if action == "apply" and data.get("confirm") != "APPLY SERVER CONFIG":
                return self.send_json({"error": "confirmation required"}, 400)
            command = [str(LIB / "server-config-manager.py"), action, json.dumps({
                "engine": data.get("engine", {}), "launch": data.get("launch", {}),
            })]
            if action == "apply":
                command += ["--confirm", str(data.get("confirm", ""))]
            result = run(*command)
            return self.send_json(json.loads(result["output"]) if result["ok"] else result, 200 if result["ok"] else 400)
        if path == "/server-config/rollback":
            if data.get("confirm") != "ROLLBACK SERVER CONFIG":
                return self.send_json({"error": "confirmation required"}, 400)
            result = run(str(LIB / "server-config-manager.py"), "rollback", "--confirm", str(data.get("confirm", "")))
            return self.send_json(json.loads(result["output"]) if result["ok"] else result, 200 if result["ok"] else 400)
        if path in {"/server-config/raw/plan", "/server-config/raw/apply"}:
            action = "raw-" + path.rsplit("/", 1)[-1]
            if action == "raw-apply" and data.get("confirm") != "APPLY ENGINE INI":
                return self.send_json({"error": "confirmation required"}, 400)
            command = [str(LIB / "server-config-manager.py"), action, str(data.get("content", ""))]
            if action == "raw-apply":
                command += ["--confirm", str(data.get("confirm", ""))]
            result = run(*command)
            return self.send_json(json.loads(result["output"]) if result["ok"] else result, 200 if result["ok"] else 400)
        if path == "/server-config/raw/rollback":
            if data.get("confirm") != "ROLLBACK ENGINE INI":
                return self.send_json({"error": "confirmation required"}, 400)
            result = run(str(LIB / "server-config-manager.py"), "raw-rollback", "--confirm", str(data.get("confirm", "")))
            return self.send_json(json.loads(result["output"]) if result["ok"] else result, 200 if result["ok"] else 400)
        if path in {"/mod-config/plan", "/mod-config/apply"}:
            action = path.rsplit("/", 1)[-1]
            payload = {"updates": data.get("updates", {})}
            if "motd" in data: payload["motd"] = data["motd"]
            command = [str(LIB / "mod-config-manager.py"), action, json.dumps(payload)]
            if action == "apply":
                if data.get("confirm") != "APPLY MOD CONFIG":
                    return self.send_json({"error": "confirmation required"}, 400)
                command += ["--expected-sha256", str(data.get("expected_sha256", "")), "--confirm", "APPLY MOD CONFIG"]
            result = run(*command)
            try: response = json.loads(result["output"])
            except json.JSONDecodeError: response = result
            return self.send_json(response, 200 if result["ok"] else 400)
        if path == "/mod-config/rollback":
            if data.get("confirm") != "ROLLBACK MOD CONFIG":
                return self.send_json({"error": "confirmation required"}, 400)
            result = run(
                str(LIB / "mod-config-manager.py"), "rollback",
                "--expected-sha256", str(data.get("expected_sha256", "")),
                "--confirm", "ROLLBACK MOD CONFIG",
            )
            try: response = json.loads(result["output"])
            except json.JSONDecodeError: response = result
            return self.send_json(response, 200 if result["ok"] else 400)
        if path == "/mod-config/lua-state":
            if data.get("confirm") != "SET LUA MOD STATE" or not isinstance(data.get("enabled"), bool):
                return self.send_json({"error": "confirmation and boolean enabled state required"}, 400)
            result = run(
                str(LIB / "mod-config-manager.py"), "lua-set",
                str(data.get("name", "")), str(data["enabled"]).lower(),
                "--confirm", "SET LUA MOD STATE",
            )
            try: response = json.loads(result["output"])
            except json.JSONDecodeError: response = result
            return self.send_json(response, 200 if result["ok"] else 400)
        if path == "/mod-lifecycle/plan":
            component = str(data.get("component", ""))
            action = str(data.get("action", ""))
            if component not in {"paldefender", "ue4ss"} or action not in {"install", "remove", "rollback"}:
                return self.send_json({"error": "invalid mod lifecycle component or action"}, 400)
            result = run(str(LIB / "mod-lifecycle.py"), "plan", component, action)
            try: response = json.loads(result["output"])
            except json.JSONDecodeError: response = result
            return self.send_json(response, 200 if result["ok"] else 400)
        if path == "/mod-lifecycle/execute":
            component = str(data.get("component", ""))
            action = str(data.get("action", ""))
            if component not in {"paldefender", "ue4ss"} or action not in {"install", "remove", "rollback"}:
                return self.send_json({"error": "invalid mod lifecycle component or action"}, 400)
            expected_confirmation = f"{action.upper()} {component.upper()}"
            if data.get("confirm") != expected_confirmation:
                return self.send_json({"error": "confirmation required"}, 400)
            result = run(
                str(LIB / "mod-lifecycle.py"), action, component,
                "--expected-plan-hash", str(data.get("expected_plan_hash", "")),
                "--confirm", expected_confirmation,
                timeout=120,
            )
            try: response = json.loads(result["output"])
            except json.JSONDecodeError: response = result
            return self.send_json(response, 200 if result["ok"] else 400)
        if path == "/diagnostics/exposure/check":
            result = run(str(LIB / "exposure-check.py"), timeout=30)
            if not result["output"]:
                return self.send_json(result, 500)
            try:
                payload = redact(json.loads(result["output"])); payload["command_ok"] = result["ok"]
            except json.JSONDecodeError:
                return self.send_json(result, 500)
            return self.send_json(payload, 200 if result["ok"] else 409)
        if path == "/diagnostics/config/recover":
            if data.get("confirm") != "RECOVER CONFIG":
                return self.send_json({"error": "confirmation required"}, 400)
            kind = str(data.get("kind", "")); target = str(data.get("target", ""))
            if kind not in {"world", "engine", "world-options"}:
                return self.send_json({"error": "invalid recovery kind"}, 400)
            result = run(
                "sudo", "-n", str(LIB / "ops-config-recovery.sh"), kind, target, "RECOVER CONFIG", timeout=300,
            )
            ops.audit("config.recover.browser", "ok" if result["ok"] else "failed", kind=kind, role=identity["role"])
            if not result["ok"]: return self.send_json(result, 400)
            try: return self.send_json(json.loads(result["output"]))
            except json.JSONDecodeError: return self.send_json(result)
        if path in {"/updates/check", "/updates/plan"}:
            action = path.rsplit("/", 1)[-1]
            result = run(str(LIB / "update-control.py"), action, timeout=240)
            if not result["ok"]:
                return self.send_json(result, 500)
            try:
                return self.send_json(json.loads(result["output"]))
            except json.JSONDecodeError:
                return self.send_json({"error": "update control returned invalid output"}, 500)
        if path == "/updates/install":
            if data.get("confirm") != "INSTALL UPDATE":
                return self.send_json({"error": "confirmation required"}, 400)
            target = str(data.get("target_build", ""))
            if not re.fullmatch(r"[0-9]+", target):
                return self.send_json({"error": "numeric target_build required"}, 400)
            result = run(
                "sudo", "-n", str(LIB / "ops-update.sh"), target,
                "--execute", "--confirm", "INSTALL UPDATE", timeout=14400,
            )
            ops.audit("update.browser-install", "ok" if result["ok"] else "failed", target_build=target, role=identity["role"])
            return self.send_json(result, 200 if result["ok"] else 500)
        if path == "/builds/plan":
            target = str(data.get("target_build", ""))
            if not re.fullmatch(r"[0-9]+", target):
                return self.send_json({"error": "numeric target_build required"}, 400)
            result = run(str(LIB / "steam-build-manager.py"), "plan", target, timeout=60)
            return self.send_json(json.loads(result["output"]) if result["ok"] else result, 200 if result["ok"] else 400)
        if path == "/builds/pin":
            if data.get("confirm") != "PIN BUILD":
                return self.send_json({"error": "confirmation required"}, 400)
            target = str(data.get("target_build", ""))
            if not re.fullmatch(r"[0-9]+", target):
                return self.send_json({"error": "numeric target_build required"}, 400)
            result = run(str(LIB / "steam-build-manager.py"), "pin", target)
            return self.send_json(json.loads(result["output"]) if result["ok"] else result, 200 if result["ok"] else 400)
        if path == "/builds/unpin":
            if data.get("confirm") != "UNPIN BUILD":
                return self.send_json({"error": "confirmation required"}, 400)
            result = run(str(LIB / "steam-build-manager.py"), "unpin", "--confirm", "UNPIN BUILD")
            return self.send_json(json.loads(result["output"]) if result["ok"] else result, 200 if result["ok"] else 400)
        if path == "/builds/rollback":
            if data.get("confirm") != "ROLLBACK BUILD":
                return self.send_json({"error": "confirmation required"}, 400)
            target = str(data.get("target_build", ""))
            if not re.fullmatch(r"[0-9]+", target):
                return self.send_json({"error": "numeric target_build required"}, 400)
            result = run(
                "sudo", "-n", str(LIB / "ops-build-rollback.sh"), target,
                "--execute", "--confirm", "ROLLBACK BUILD", timeout=14400,
            )
            ops.audit("build.rollback-browser", "ok" if result["ok"] else "failed", target_build=target, role=identity["role"])
            return self.send_json(result, 200 if result["ok"] else 500)
        if path == "/save/intelligence/scan":
            result = run(str(LIB / "save-intelligence.py"), "scan", timeout=3600)
            ops.audit("save-intelligence.scan", "ok" if result["ok"] else "failed", role=identity["role"])
            if not result["ok"]:
                return self.send_json(result, 500)
            try:
                return self.send_json(json.loads(result["output"]))
            except json.JSONDecodeError:
                return self.send_json({"error": "save intelligence returned invalid output"}, 500)
        if path in {"/settings/raw/plan", "/settings/raw/apply"}:
            action = path.rsplit("/", 1)[-1]
            if action == "apply" and data.get("confirm") != "APPLY RAW SETTINGS":
                return self.send_json({"error": "confirmation required"}, 400)
            command = [str(LIB / "settings-manager.py"), f"raw-{action}", str(data.get("content", ""))]
            if action == "apply":
                command += ["--confirm", str(data.get("confirm", ""))]
            result = run(*command)
            return self.send_json(json.loads(result["output"]) if result["ok"] else result, 200 if result["ok"] else 400)
        if path == "/settings/raw/rollback":
            if data.get("confirm") != "ROLLBACK RAW SETTINGS":
                return self.send_json({"error": "confirmation required"}, 400)
            result = run(
                str(LIB / "settings-manager.py"), "raw-rollback",
                "--confirm", str(data.get("confirm", "")),
            )
            return self.send_json(json.loads(result["output"]) if result["ok"] else result, 200 if result["ok"] else 400)
        if path == "/jobs":
            kind = str(data.get("type", ""))
            if kind not in {"announcement", "restart", "settings_event", "rcon"}:
                return self.send_json({"error": "unknown scheduled-work type"}, 400)
            try:
                due_at = int(data.get("due_at"))
            except (TypeError, ValueError):
                return self.send_json({"error": "due_at must be a Unix timestamp"}, 400)
            if due_at < int(time.time()) - 60 or due_at > int(time.time()) + 366 * 86400:
                return self.send_json({"error": "due_at must be within the next year"}, 400)
            message = str(data.get("message", ""))
            if len(message) > 500 or any(character in message for character in "\r\n\0"):
                return self.send_json({"error": "message must be a single line of at most 500 characters"}, 400)
            job = {"id": uuid.uuid4().hex, "enabled": True, "type": kind, "due_at": due_at, "message": message}
            if kind == "settings_event":
                if data.get("confirm") != "SCHEDULE TIMED EVENT":
                    return self.send_json({"error": "confirmation required"}, 400)
                if data.get("preset") not in EVENT_PRESETS:
                    return self.send_json({"error": "unknown timed-event preset"}, 400)
                try:
                    duration_seconds = int(data.get("duration_seconds"))
                except (TypeError, ValueError):
                    return self.send_json({"error": "duration_seconds must be an integer"}, 400)
                if not 300 <= duration_seconds <= 7 * 86400:
                    return self.send_json({"error": "timed events must last between 5 minutes and 7 days"}, 400)
                job.update({"preset": data["preset"], "duration_seconds": duration_seconds, "phase": "pending"})
            if kind == "rcon":
                if data.get("confirm") != "SCHEDULE RCON COMMAND":
                    return self.send_json({"error": "confirmation required"}, 400)
                command = str(data.get("command", "")).strip()
                saved_id = data.get("saved_id")
                if bool(command) == (saved_id is not None and str(saved_id).strip() != ""):
                    return self.send_json({"error": "provide exactly one command or saved_id"}, 400)
                if command:
                    validation = run(str(LIB / "rcon-console.py"), "validate", command)
                    if not validation["ok"]:
                        return self.send_json({"error": validation["output"]}, 400)
                    job["command"] = command
                else:
                    try: saved_id = int(saved_id)
                    except (TypeError, ValueError): return self.send_json({"error": "saved_id must be an integer"}, 400)
                    listing = run(str(LIB / "rcon-console.py"), "saved-list")
                    try: items = json.loads(listing["output"])
                    except json.JSONDecodeError: return self.send_json({"error": "saved-command catalog unavailable"}, 503)
                    if not any(int(item.get("id", -1)) == saved_id for item in items):
                        return self.send_json({"error": "saved command not found"}, 400)
                    job["saved_id"] = saved_id
                if data.get("interval_seconds") not in {None, "", 0, "0"}:
                    try: interval = int(data["interval_seconds"])
                    except (TypeError, ValueError): return self.send_json({"error": "interval_seconds must be an integer"}, 400)
                    if not 60 <= interval <= 366 * 86400:
                        return self.send_json({"error": "interval must be between 60 seconds and 366 days"}, 400)
                    job["interval_seconds"] = interval
            jobs = ops.read_json(ops.STATE / "jobs.json", [])
            jobs.append(job)
            ops.atomic_json(ops.STATE / "jobs.json", jobs)
            ops.audit("scheduler.create", "ok", job_id=job["id"], role=identity["role"])
            return self.send_json(job, 201)
        if path.startswith("/jobs/") and path.endswith("/cancel"):
            job_id = path.split("/")[2]
            jobs = ops.read_json(ops.STATE / "jobs.json", [])
            found = False
            for job in jobs:
                if job.get("id") == job_id:
                    if job.get("type") == "settings_event" and job.get("phase") == "active":
                        job["cancel_requested"] = True
                        job["next_run"] = int(time.time())
                    else:
                        job["enabled"] = False
                    found = True
            ops.atomic_json(ops.STATE / "jobs.json", jobs)
            ops.audit("scheduler.cancel", "ok" if found else "missing", job_id=job_id, role=identity["role"])
            return self.send_json({"ok": found}, 200 if found else 404)
        if path == "/backups/verify":
            backup = backup_path(str(data.get("name", "")))
            if not backup:
                return self.send_json({"error": "backup not found"}, 404)
            result = run(str(LIB / "verify-backup.sh"), str(backup), timeout=300)
            ops.audit("backup.verify", "ok" if result["ok"] else "failed", backup=backup.name, role=identity["role"])
            return self.send_json(result, 200 if result["ok"] else 400)
        if path == "/backups/restore/plan":
            backup = backup_path(str(data.get("name", "")))
            if not backup:
                return self.send_json({"error": "backup not found"}, 404)
            result = run(str(LIB / "restore.py"), str(backup), timeout=300)
            ops.audit("restore.plan", "ok" if result["ok"] else "failed", backup=backup.name, role=identity["role"])
            if not result["ok"]:
                return self.send_json(result, 400)
            try:
                plan = json.loads(result["output"])
            except json.JSONDecodeError:
                return self.send_json({"error": "restore planner returned invalid output"}, 500)
            return self.send_json({"ok": True, "plan": plan})
        if path == "/backups/restore":
            if data.get("confirm") != "RESTORE WORLD":
                return self.send_json({"error": "confirmation required"}, 400)
            backup = backup_path(str(data.get("name", "")))
            if not backup:
                return self.send_json({"error": "backup not found"}, 404)
            result = run(
                "sudo", "-n", str(LIB / "ops-restore.sh"), backup.name,
                "--execute", "--confirm", "RESTORE WORLD", timeout=3600,
            )
            ops.audit("restore.browser", "ok" if result["ok"] else "failed", backup=backup.name, role=identity["role"])
            return self.send_json(result, 200 if result["ok"] else 500)
        if path == "/backups/quarantine":
            if data.get("confirm") != "QUARANTINE BACKUP":
                return self.send_json({"error": "confirmation required"}, 400)
            backup = backup_path(str(data.get("name", "")))
            if not backup:
                return self.send_json({"error": "backup not found"}, 404)
            quarantine = BACKUPS / ".quarantine" / backup.name
            try:
                with ops.operation_lock(blocking=False):
                    quarantine.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    for source in (backup, pathlib.Path(str(backup) + ".sha256"), pathlib.Path(str(backup) + ".manifest.json")):
                        if source.exists():
                            shutil.move(str(source), quarantine.parent / source.name)
            except BlockingIOError:
                return self.send_json({"error": "another world mutation is in progress"}, 409)
            ops.audit("backup.quarantine", "ok", backup=backup.name, role=identity["role"])
            return self.send_json({"ok": True, "name": backup.name})
        return self.send_json({"error": "unknown action"}, 404)


if __name__ == "__main__":
    if not ADMIN_TOKEN:
        raise SystemExit("PALWORLD_OPS_TOKEN must be configured")
    ThreadingHTTPServer(
        (os.environ.get("PALWORLD_OPS_BIND", "127.0.0.1"), int(os.environ.get("PALWORLD_OPS_PORT", "8213"))),
        Handler,
    ).serve_forever()
