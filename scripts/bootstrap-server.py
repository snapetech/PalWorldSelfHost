#!/usr/bin/env python3
"""One-shot, loopback-only browser bootstrap for PalWorldSelfHost."""

import argparse
import hashlib
import hmac
import importlib.util
import ipaddress
import json
import os
import pathlib
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse


ROOT = pathlib.Path(__file__).resolve().parents[1]
STATIC = ROOT / "bootstrap" / "static"
if not (STATIC / "index.html").is_file():
    STATIC = pathlib.Path("/usr/local/share/palworld-bootstrap/static")
MAX_BODY = 64 * 1024
MAX_LOG = 100_000
PLAN_TTL = 15 * 60
SECRET_MIN = 24
SECRET_MAX = 256
EXACT_CONFIRMATIONS = {
    "fresh": "INSTALL PALWORLD",
    "adopt": "ADOPT EXISTING INSTALL",
}
REQUIRED_COMMANDS = (
    "bash", "curl", "findmnt", "nft", "python3", "rclone", "sudo",
    "systemctl", "zstd",
)


class BootstrapError(ValueError):
    """A bounded operator-facing bootstrap refusal."""


def load_adoption_module(repo=ROOT):
    source = pathlib.Path(repo) / "scripts" / "adopt-existing.py"
    spec = importlib.util.spec_from_file_location("palworld_adopt_existing", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def clean_text(value, label, *, minimum=0, maximum=300):
    value = str(value or "").strip()
    if len(value) < minimum or len(value) > maximum:
        raise BootstrapError(f"{label} must contain {minimum}-{maximum} characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise BootstrapError(f"{label} contains a control character")
    return value


def absolute_path(value, label):
    value = clean_text(value, label, minimum=1, maximum=500)
    path = pathlib.Path(value)
    if not path.is_absolute() or path == pathlib.Path("/"):
        raise BootstrapError(f"{label} must be an absolute non-root path")
    if path.is_symlink():
        raise BootstrapError(f"{label} may not be a symlink")
    return path.resolve(strict=False)


def is_within(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def port(value, label):
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise BootstrapError(f"{label} must be an integer") from exc
    if not 1024 <= result <= 65535:
        raise BootstrapError(f"{label} must be between 1024 and 65535")
    return result


def optional_ipv4(value, label):
    value = clean_text(value, label, maximum=45)
    if not value:
        return ""
    try:
        return str(ipaddress.IPv4Address(value))
    except ipaddress.AddressValueError as exc:
        raise BootstrapError(f"{label} must be an IPv4 address") from exc


def normalize_config(payload, *, hostname=None):
    if not isinstance(payload, dict):
        raise BootstrapError("request body must be a JSON object")
    mode = payload.get("mode", "fresh")
    if mode not in EXACT_CONFIRMATIONS:
        raise BootstrapError("mode must be fresh or adopt")

    install = absolute_path(payload.get("install_dir", "/srv/palworld/server"), "install directory")
    state = absolute_path(payload.get("state_dir", "/var/lib/palworld"), "state directory")
    backup = absolute_path(payload.get("backup_root", "/var/backups/palworld"), "backup directory")
    public = absolute_path(payload.get("public_dir", "/srv/static/palworld"), "public directory")
    rclone_config = absolute_path(
        payload.get("rclone_config", "/var/lib/palworld/.config/rclone/rclone.conf"),
        "rclone configuration",
    )
    steamcmd = absolute_path(payload.get("steamcmd", "/usr/local/bin/steamcmd"), "SteamCMD path")

    managed = {"install": install, "state": state, "backup": backup, "public": public}
    for left_name, left in managed.items():
        for right_name, right in managed.items():
            if left_name >= right_name:
                continue
            if is_within(left, right) or is_within(right, left):
                raise BootstrapError(f"{left_name} and {right_name} directories may not overlap")

    game_port = port(payload.get("game_port", 8211), "game port")
    rest_port = port(payload.get("rest_port", 8212), "REST port")
    ops_port = port(payload.get("ops_port", 8213), "operations port")
    rcon_port = port(payload.get("rcon_port", 25575), "RCON port")
    if len({game_port, rest_port, ops_port, rcon_port}) != 4:
        raise BootstrapError("game, REST, operations and RCON ports must be distinct")

    rclone_dest = clean_text(payload.get("rclone_dest"), "rclone destination", minimum=3, maximum=500)
    if ":" not in rclone_dest:
        raise BootstrapError("rclone destination must name a configured remote, for example remote:path")
    server_name = clean_text(payload.get("server_name"), "server name", minimum=1, maximum=80)
    description = clean_text(payload.get("server_description", "Palworld community server"), "server description", maximum=200)
    try:
        exp_rate = float(payload.get("player_exp_rate", 1.0))
    except (TypeError, ValueError) as exc:
        raise BootstrapError("player XP rate must be numeric") from exc
    if not 0.1 <= exp_rate <= 20:
        raise BootstrapError("player XP rate must be between 0.1 and 20")

    return {
        "mode": mode,
        "install_dir": str(install),
        "state_dir": str(state),
        "backup_root": str(backup),
        "public_dir": str(public),
        "steamcmd": str(steamcmd),
        "rclone_dest": rclone_dest,
        "rclone_config": str(rclone_config),
        "server_name": server_name,
        "server_description": description,
        "player_exp_rate": exp_rate,
        "game_port": game_port,
        "rest_port": rest_port,
        "ops_port": ops_port,
        "rcon_port": rcon_port,
        "public_ip": optional_ipv4(payload.get("public_ip"), "public IP"),
        "bind_ip": optional_ipv4(payload.get("bind_ip"), "bind IP"),
        "expected_hostname": hostname or socket.gethostname(),
    }


def validate_secret(value, label):
    value = clean_text(value, label, minimum=SECRET_MIN, maximum=SECRET_MAX)
    if value.lower() in {"generate-a-long-random-secret", "password", "palworld"}:
        raise BootstrapError(f"{label} is a placeholder or weak value")
    return value


def environment_values(config, admin_password, ops_token):
    return {
        "PALWORLD_INSTALL_DIR": config["install_dir"],
        "PALWORLD_STEAMCMD": config["steamcmd"],
        "PALWORLD_USER": "palworld",
        "PALWORLD_GROUP": "palworld",
        "PALWORLD_STATE_DIR": config["state_dir"],
        "PALWORLD_BACKUP_LOCAL_ROOT": config["backup_root"],
        "PALWORLD_RCLONE_DEST": config["rclone_dest"],
        "RCLONE_CONFIG": config["rclone_config"],
        "PALWORLD_REQUIRE_LVM_BACKUP": "false",
        "PALWORLD_PUBLIC_DIR": config["public_dir"],
        "PALWORLD_EXPECTED_HOSTNAME": config["expected_hostname"],
        "PALWORLD_PORT": str(config["game_port"]),
        "PALWORLD_PUBLIC_IP": config["public_ip"],
        "PALWORLD_BIND_IP": config["bind_ip"],
        "PALWORLD_HAIRPIN_NAT_ENABLED": "false",
        "PALWORLD_SERVER_NAME": config["server_name"],
        "PALWORLD_SERVER_DESCRIPTION": config["server_description"],
        "PALWORLD_PLAYER_EXP_RATE": str(config["player_exp_rate"]),
        "PALWORLD_ADMIN_PASSWORD": admin_password,
        "PALWORLD_REST_PORT": str(config["rest_port"]),
        "PALWORLD_OPS_BIND": "127.0.0.1",
        "PALWORLD_OPS_PORT": str(config["ops_port"]),
        "PALWORLD_OPS_TOKEN": ops_token,
        "PALWORLD_OPS_SECURE_COOKIE": "false",
        "PALWORLD_RCON_PORT": str(config["rcon_port"]),
        "PALWORLD_RCON_ENABLED": "false",
        "PALWORLD_MODERATION_ENABLED": "false",
        "PALWORLD_AUTO_PAUSE_ENABLED": "false",
        "PALWORLD_GAME_DATA_ENABLED": "false",
    }


def atomic_environment(path, values):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write("# Created by the one-shot PalWorldSelfHost browser bootstrap.\n")
            for key, value in values.items():
                if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
                    raise BootstrapError("environment contains an invalid key")
                stream.write(f"{key}={shlex.quote(str(value))}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def atomic_json(path, payload, mode=0o640):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(payload, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def prerequisites(config):
    missing = [name for name in REQUIRED_COMMANDS if shutil.which(name) is None]
    steamcmd = pathlib.Path(config["steamcmd"])
    if not steamcmd.is_file() or steamcmd.is_symlink() or not os.access(steamcmd, os.X_OK):
        missing.append(config["steamcmd"])
    return sorted(set(missing))


def default_runner(command, append, environment):
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    for line in process.stdout:
        append(line.rstrip())
    return process.wait()


class BootstrapManager:
    def __init__(self, *, repo=ROOT, env_path="/etc/palworld-server.env", now=time.time,
                 runner=default_runner, prerequisite_checker=prerequisites,
                 adoption_planner=None):
        self.repo = pathlib.Path(repo).resolve()
        self.env_path = pathlib.Path(env_path)
        self.now = now
        self.runner = runner
        self.prerequisite_checker = prerequisite_checker
        self.adoption_planner = adoption_planner or load_adoption_module(self.repo).adoption_plan
        self.code = secrets.token_urlsafe(32)
        self.plan = None
        self.phase = "locked"
        self.output = []
        self.error = None
        self.return_code = None
        self.lock = threading.Lock()
        self.owns_environment = False
        self.secret_values = []
        self.secret_hashes = None
        self.started_at = None

    def authenticate(self, candidate):
        return hmac.compare_digest(self.code, str(candidate or ""))

    def create_plan(self, payload):
        with self.lock:
            if self.phase == "running":
                raise BootstrapError("installation is already running")
            if self.owns_environment:
                raise BootstrapError("bootstrap already wrote the environment; retry the same bound plan instead of replanning")
        config = normalize_config(payload)
        install = pathlib.Path(config["install_dir"])
        if config["mode"] == "fresh" and install.exists() and any(install.iterdir()):
            raise BootstrapError("fresh install directory is not empty; use adoption for an existing Palworld tree")

        adoption = None
        if config["mode"] == "adopt":
            adoption = self.adoption_planner(config["install_dir"], config["state_dir"])
            if adoption.get("status") != "ready":
                raise BootstrapError("the existing server is still running; stop it before adoption")

        missing = self.prerequisite_checker(config)
        created = int(self.now())
        internal = {
            "id": secrets.token_urlsafe(24),
            "created_at": created,
            "expires_at": created + PLAN_TTL,
            "config": config,
            "adoption": adoption,
            "missing_prerequisites": missing,
        }
        fingerprint_body = {
            "config": config,
            "adoption_fingerprint": adoption.get("fingerprint") if adoption else None,
        }
        internal["fingerprint"] = hashlib.sha256(
            json.dumps(fingerprint_body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with self.lock:
            self.plan = internal
            self.phase = "planned"
            self.output = []
            self.error = None
            self.return_code = None
        return self.public_plan(internal)

    @staticmethod
    def public_plan(plan):
        config = dict(plan["config"])
        mode = config.pop("mode")
        result = {
            "plan_id": plan["id"],
            "mode": mode,
            "created_at": plan["created_at"],
            "expires_at": plan["expires_at"],
            "confirmation": EXACT_CONFIRMATIONS[mode],
            "fingerprint": plan["fingerprint"],
            "configuration": config,
            "missing_prerequisites": plan["missing_prerequisites"],
            "changes": [
                "write a protected Palworld environment file atomically",
                "create the locked palworld service account and managed directories",
                "install the private console, services, timers and firewall boundary",
            ],
        }
        if mode == "fresh":
            result["changes"].insert(1, "install the official Palworld dedicated server through SteamCMD")
        else:
            result["changes"].insert(1, "adopt the exact reviewed server tree without reinstalling game files")
            result["adoption"] = {
                "status": plan["adoption"]["status"],
                "build_id": plan["adoption"]["build_id"],
                "worlds": plan["adoption"]["worlds"],
                "fingerprint": plan["adoption"]["fingerprint"],
                "game_files_reinstalled": False,
            }
        return result

    def _append(self, line):
        line = str(line)
        for secret_value in self.secret_values:
            if secret_value:
                line = line.replace(secret_value, "<redacted>")
        with self.lock:
            self.output.append(line[:4000])
            while sum(len(item) + 1 for item in self.output) > MAX_LOG:
                self.output.pop(0)

    def execute(self, payload, *, background=True):
        if not isinstance(payload, dict):
            raise BootstrapError("request body must be a JSON object")
        with self.lock:
            plan = self.plan
            if not plan or payload.get("plan_id") != plan["id"]:
                raise BootstrapError("plan identifier is missing or no longer current")
            retry = self.phase == "failed" and self.owns_environment
            if self.now() > plan["expires_at"] and not retry:
                raise BootstrapError("the reviewed plan expired; create and review a new plan")
            if self.phase == "running":
                raise BootstrapError("installation is already running")
            if self.phase == "complete":
                raise BootstrapError("bootstrap has already completed")
            if plan["missing_prerequisites"]:
                raise BootstrapError("install required prerequisites before execution")
            expected = EXACT_CONFIRMATIONS[plan["config"]["mode"]]
            if payload.get("confirmation") != expected:
                raise BootstrapError(f"type {expected!r} exactly to execute")

        admin_password = validate_secret(payload.get("admin_password"), "game administrator password")
        ops_token = validate_secret(payload.get("ops_token"), "operations token")
        if hmac.compare_digest(admin_password, ops_token):
            raise BootstrapError("game administrator password and operations token must be different")
        if self.env_path.exists() and not self.owns_environment:
            raise BootstrapError(f"{self.env_path} already exists; bootstrap will not overwrite it")

        presented_hashes = (
            hashlib.sha256(admin_password.encode()).hexdigest(),
            hashlib.sha256(ops_token.encode()).hexdigest(),
        )
        if retry:
            if self.secret_hashes != presented_hashes:
                raise BootstrapError("retry requires the same saved credentials used by the first execution")
        else:
            values = environment_values(plan["config"], admin_password, ops_token)
            atomic_environment(self.env_path, values)
            self.owns_environment = True
            self.secret_hashes = presented_hashes
        self.secret_values = [admin_password, ops_token]
        command = [str(self.repo / "scripts" / "install.sh")]
        if plan["config"]["mode"] == "adopt":
            command.extend([
                "--adopt-existing",
                "--adoption-fingerprint", plan["adoption"]["fingerprint"],
                "--confirm", EXACT_CONFIRMATIONS["adopt"],
            ])
        environment = {**os.environ, "PALWORLD_ENV_FILE": str(self.env_path)}
        with self.lock:
            self.phase = "running"
            self.output = []
            self.error = None
            self.return_code = None
            if self.started_at is None:
                self.started_at = int(self.now())

        worker = threading.Thread(target=self._run, args=(command, environment), daemon=True)
        if background:
            worker.start()
        else:
            worker.run()
        return {"status": "running", "plan_id": plan["id"]}

    def _run(self, command, environment):
        try:
            code = self.runner(command, self._append, environment)
            if code == 0:
                receipt = {
                    "status": "complete",
                    "plan_fingerprint": self.plan["fingerprint"],
                    "mode": self.plan["config"]["mode"],
                    "configuration": {key: value for key, value in self.plan["config"].items() if key != "mode"},
                    "adoption_fingerprint": self.plan["adoption"].get("fingerprint") if self.plan["adoption"] else None,
                    "started_at": self.started_at,
                    "completed_at": int(self.now()),
                    "installer_status": 0,
                }
                atomic_json(pathlib.Path(self.plan["config"]["state_dir"]) / "bootstrap-receipt.json", receipt)
            with self.lock:
                self.return_code = int(code)
                if code == 0:
                    self.phase = "complete"
                else:
                    self.phase = "failed"
                    self.error = f"installer exited with status {code}"
        except Exception as exc:  # execution boundary: retain a bounded failure for the UI
            with self.lock:
                self.phase = "failed"
                self.error = f"installer could not run: {exc}"
        finally:
            self.secret_values = []

    def status(self):
        with self.lock:
            result = {
                "phase": self.phase,
                "output": "\n".join(self.output),
                "error": self.error,
                "return_code": self.return_code,
            }
            if self.plan:
                result["plan_id"] = self.plan["id"]
            return result


class BootstrapHandler(SimpleHTTPRequestHandler):
    server_version = "PalWorldBootstrap/1"

    def __init__(self, *args, manager=None, **kwargs):
        self.manager = manager
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def log_message(self, format_string, *args):
        # Keep only method/path/status metadata; request bodies and launch codes are never logged.
        super().log_message(format_string, *args)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def json_response(self, status, payload):
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        return self.manager.authenticate(self.headers.get("X-Bootstrap-Code"))

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise BootstrapError("invalid Content-Length") from exc
        if length <= 0 or length > MAX_BODY:
            raise BootstrapError(f"JSON body must contain 1-{MAX_BODY} bytes")
        try:
            return json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BootstrapError("request body is not valid JSON") from exc

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/v1/bootstrap/status":
            if not self.authorized():
                self.json_response(401, {"error": "valid launch code required"})
                return
            self.json_response(200, self.manager.status())
            return
        if path.startswith("/api/"):
            self.json_response(404, {"error": "not found"})
            return
        if path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self):
        if not self.authorized():
            self.json_response(401, {"error": "valid launch code required"})
            return
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
            if path == "/api/v1/bootstrap/plan":
                result = self.manager.create_plan(payload)
                self.json_response(200, result)
            elif path == "/api/v1/bootstrap/execute":
                result = self.manager.execute(payload)
                self.json_response(202, result)
            else:
                self.json_response(404, {"error": "not found"})
        except (BootstrapError, OSError) as exc:
            self.json_response(400, {"error": str(exc)})


def loopback_address(value):
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        try:
            return all(ipaddress.ip_address(item[4][0]).is_loopback for item in socket.getaddrinfo(value, None))
        except socket.gaierror:
            return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8214)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("run as root; this one-shot process writes system configuration and invokes the installer")
    if not loopback_address(args.bind):
        raise SystemExit("bootstrap refuses non-loopback bind addresses; use an SSH tunnel for remote setup")
    if not 1024 <= args.port <= 65535:
        raise SystemExit("port must be between 1024 and 65535")
    env_file = pathlib.Path("/etc/palworld-server.env")
    if env_file.exists():
        raise SystemExit(f"{env_file} already exists; use the installed operations console or remove it deliberately")
    if not (STATIC / "index.html").is_file():
        raise SystemExit(f"bootstrap assets are missing from {STATIC}")

    manager = BootstrapManager(env_path=env_file)
    handler = lambda *handler_args, **kwargs: BootstrapHandler(*handler_args, manager=manager, **kwargs)
    server = ThreadingHTTPServer((args.bind, args.port), handler)
    print(f"PalWorldSelfHost bootstrap: http://{args.bind}:{args.port}/", flush=True)
    print(f"Ephemeral launch code: {manager.code}", flush=True)
    print("The listener is loopback-only. Press Ctrl+C after the UI reports completion.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
