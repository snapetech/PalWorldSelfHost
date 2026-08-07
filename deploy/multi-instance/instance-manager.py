#!/usr/bin/env python3
"""Create and independently operate isolated official-image Palworld instances."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import pathlib
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import time


IMAGE = "ghcr.io/pocketpairjp/palserver:v1.0.1.100619@sha256:0d293cafd503a91a6d11d71f7bf770ee0c3c5ecf37db988349b2c1758f4e9358"
NAME_RE = re.compile(r"[a-z][a-z0-9-]{0,31}")
CONFIRM_DELETE = "DELETE INSTANCE"
PORT_FIELDS = ("game_port", "query_port", "rest_port", "rcon_port")


def atomic_json(path: pathlib.Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def validate_name(value: str) -> str:
    value = str(value or "")
    if not NAME_RE.fullmatch(value):
        raise ValueError("instance name must match [a-z][a-z0-9-]{0,31}")
    return value


def validate_port(value, field: str) -> int:
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value or "")):
        raise ValueError(f"{field} must be an integer from 1024 through 65535")
    port = int(value)
    if not 1024 <= port <= 65535:
        raise ValueError(f"{field} must be an integer from 1024 through 65535")
    return port


def validate_world_id(value: str | None) -> str:
    value = value or secrets.token_hex(16).upper()
    if not re.fullmatch(r"[0-9A-Fa-f]{32}", value):
        raise ValueError("world_id must be exactly 32 hexadecimal characters")
    return value.upper()


def option_line(settings: pathlib.Path) -> str:
    if not settings.is_file() or settings.is_symlink():
        raise ValueError("settings must be a regular, non-symlinked file")
    if settings.stat().st_size > 1024 * 1024:
        raise ValueError("settings exceeds 1 MiB")
    try:
        lines = settings.read_text().splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError("settings is not readable UTF-8 text") from exc
    matches = [line for line in lines if re.fullmatch(r"OptionSettings=\(.*\)", line)]
    if len(matches) != 1:
        raise ValueError("settings must contain exactly one single-line OptionSettings tuple")
    line = matches[0]
    if not re.search(r'AdminPassword="(?!CHANGE-ME")[^"\r\n]{8,128}"', line):
        raise ValueError("settings requires a non-placeholder 8-128 character AdminPassword")
    for key, expected in (("PublicPort", 8211), ("RESTAPIPort", 8212)):
        match = re.search(rf"(?:\(|,){key}=([0-9]+)(?:,|\))", line)
        if not match or int(match.group(1)) != expected:
            raise ValueError(f"settings {key} must use container-internal port {expected}")
    rcon = re.search(r"(?:\(|,)RCONPort=([0-9]+)(?:,|\))", line)
    if rcon and int(rcon.group(1)) != 25575:
        raise ValueError("settings RCONPort must use container-internal port 25575")
    return line


def load_registry(root: pathlib.Path) -> dict:
    path = root / "instances.json"
    if not path.exists():
        return {"version": 1, "instances": {}}
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("instance registry is unreadable or invalid JSON") from exc
    if value.get("version") != 1 or not isinstance(value.get("instances"), dict):
        raise ValueError("instance registry has an unsupported schema")
    return value


def compose_text(config: dict) -> str:
    return f'''services:
  palworld:
    image: {IMAGE}
    user: "0:0"
    restart: unless-stopped
    stop_grace_period: 120s
    entrypoint: /palworldselfhost/entrypoint.sh
    environment:
      PALWORLD_WORLD_ID: "{config['world_id']}"
    command:
      - -port=8211
      - -queryport=27015
      - -useperfthreads
      - -NoAsyncLoadingThread
      - -UseMultithreadForDS
    ports:
      - "{config['game_port']}:8211/udp"
      - "{config['query_port']}:27015/udp"
      - "127.0.0.1:{config['rest_port']}:8212/tcp"
      - "127.0.0.1:{config['rcon_port']}:25575/tcp"
    configs:
      - source: entrypoint
        target: /palworldselfhost/entrypoint.sh
        mode: 0555
      - source: settings
        target: /run/palworldselfhost/PalWorldSettings.ini
        mode: 0400
    volumes:
      - ./saved:/pal/Package/Pal/Saved
    healthcheck:
      test: ["CMD-SHELL", "test -s /tmp/palworldselfhost.pid && kill -0 $(cat /tmp/palworldselfhost.pid)"]
      start_period: 5m
      interval: 20s
      timeout: 5s
      retries: 3

configs:
  entrypoint:
    file: ./entrypoint.sh
  settings:
    file: ./PalWorldSettings.ini
'''


def project_name(name: str) -> str:
    return f"palworldselfhost-{name}"


def compose_command(root: pathlib.Path, name: str, *arguments: str) -> list[str]:
    directory = root / "instances" / name
    return ["docker", "compose", "-p", project_name(name), "-f", str(directory / "compose.yaml"),
            "--project-directory", str(directory), *arguments]


def run_checked(argv: list[str], runner=subprocess.run) -> subprocess.CompletedProcess:
    result = runner(argv, text=True, capture_output=True, timeout=600)
    if result.returncode:
        detail = (result.stdout + result.stderr).strip()[-2000:]
        raise ValueError(f"command failed: {' '.join(argv)}: {detail}")
    return result


def port_available(port: int, udp: bool) -> bool:
    family = socket.SOCK_DGRAM if udp else socket.SOCK_STREAM
    address = "0.0.0.0" if udp else "127.0.0.1"
    with socket.socket(socket.AF_INET, family) as probe:
        try:
            probe.bind((address, port))
        except OSError:
            return False
    return True


def validate_ports(registry: dict, requested: dict) -> None:
    values = [requested[field] for field in PORT_FIELDS]
    if len(values) != len(set(values)):
        raise ValueError("all four host ports must be distinct")
    used = {config[field] for config in registry["instances"].values() for field in PORT_FIELDS}
    overlap = sorted(set(values) & used)
    if overlap:
        raise ValueError(f"host port already belongs to another managed instance: {overlap[0]}")
    for field in PORT_FIELDS:
        if not port_available(requested[field], field in {"game_port", "query_port"}):
            raise ValueError(f"{field} {requested[field]} is already bound on the host")


def create(root: pathlib.Path, args, registry: dict, runner=subprocess.run) -> dict:
    name = validate_name(args.name)
    if name in registry["instances"]:
        raise ValueError("instance name already exists")
    settings = pathlib.Path(args.settings).resolve()
    option_line(settings)
    config = {field: validate_port(getattr(args, field), field) for field in PORT_FIELDS}
    config.update({"name": name, "world_id": validate_world_id(args.world_id),
                   "created_at": int(time.time()), "image": IMAGE})
    validate_ports(registry, config)
    directory = root / "instances" / name
    if directory.exists():
        raise ValueError("instance directory already exists outside the registry")
    entrypoint = pathlib.Path(__file__).resolve().parents[1] / "container" / "entrypoint.sh"
    if not entrypoint.is_file():
        raise ValueError("official container entrypoint adapter is missing")
    directory.mkdir(parents=True, mode=0o700)
    registered = False
    start_attempted = False
    try:
        (directory / "saved").mkdir(mode=0o700)
        shutil.copy2(entrypoint, directory / "entrypoint.sh", follow_symlinks=False)
        os.chmod(directory / "entrypoint.sh", 0o555)
        shutil.copy2(settings, directory / "PalWorldSettings.ini", follow_symlinks=False)
        os.chmod(directory / "PalWorldSettings.ini", 0o600)
        (directory / "compose.yaml").write_text(compose_text(config))
        os.chmod(directory / "compose.yaml", 0o600)
        atomic_json(directory / "instance.json", config)
        run_checked(compose_command(root, name, "config", "--quiet"), runner)
        registry["instances"][name] = config
        atomic_json(root / "instances.json", registry)
        registered = True
        if args.start:
            start_attempted = True
            run_checked(compose_command(root, name, "up", "-d"), runner)
    except Exception:
        if start_attempted:
            runner(compose_command(root, name, "down", "--remove-orphans"), text=True,
                   capture_output=True, timeout=600)
        if registered:
            registry["instances"].pop(name, None)
            atomic_json(root / "instances.json", registry)
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return {"status": "created", "instance": config, "started": bool(args.start)}


def require_instance(root: pathlib.Path, registry: dict, name: str) -> tuple[str, dict]:
    name = validate_name(name)
    config = registry["instances"].get(name)
    if not isinstance(config, dict):
        raise ValueError("managed instance does not exist")
    directory = root / "instances" / name
    if not directory.is_dir() or directory.is_symlink() or not (directory / "compose.yaml").is_file():
        raise ValueError("managed instance files are missing or unsafe")
    return name, config


def refresh_adapter(root: pathlib.Path, name: str, config: dict,
                    runner=subprocess.run) -> None:
    directory = root / "instances" / name
    entrypoint = pathlib.Path(__file__).resolve().parents[1] / "container" / "entrypoint.sh"
    if not entrypoint.is_file():
        raise ValueError("official container entrypoint adapter is missing")
    target = directory / "entrypoint.sh"
    temporary = directory / ".entrypoint.sh.tmp"
    shutil.copy2(entrypoint, temporary, follow_symlinks=False)
    os.chmod(temporary, 0o555)
    os.replace(temporary, target)
    (directory / "compose.yaml").write_text(compose_text(config))
    os.chmod(directory / "compose.yaml", 0o600)
    run_checked(compose_command(root, name, "config", "--quiet"), runner)


def lifecycle(root: pathlib.Path, registry: dict, name: str, action: str,
              runner=subprocess.run) -> dict:
    name, config = require_instance(root, registry, name)
    if action == "start":
        refresh_adapter(root, name, config, runner)
        command = ("up", "-d")
    elif action == "stop":
        command = ("stop", "-t", "120")
    elif action == "restart":
        run_checked(compose_command(root, name, "stop", "-t", "120"), runner)
        refresh_adapter(root, name, config, runner)
        command = ("up", "-d")
    else:
        raise ValueError("unsupported instance lifecycle action")
    run_checked(compose_command(root, name, *command), runner)
    return {"status": action + "ed" if action != "stop" else "stopped", "instance": config}


def status(root: pathlib.Path, registry: dict, name: str, runner=subprocess.run) -> dict:
    name, config = require_instance(root, registry, name)
    result = run_checked(compose_command(root, name, "ps", "--format", "json"), runner)
    records = []
    text = result.stdout.strip()
    if text:
        try:
            value = json.loads(text)
            records = value if isinstance(value, list) else [value]
        except json.JSONDecodeError:
            records = [json.loads(line) for line in text.splitlines()]
    return {"status": "observed", "instance": config, "containers": records}


def delete(root: pathlib.Path, registry: dict, name: str, confirm: str,
           runner=subprocess.run) -> dict:
    name, config = require_instance(root, registry, name)
    if confirm != CONFIRM_DELETE:
        raise ValueError(f"exact confirmation {CONFIRM_DELETE} is required")
    run_checked(compose_command(root, name, "down", "--remove-orphans"), runner)
    source = root / "instances" / name
    retired_root = root / "retired"
    retired_root.mkdir(mode=0o700, exist_ok=True)
    destination = retired_root / f"{name}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    if destination.exists():
        raise ValueError("retired instance destination already exists")
    os.replace(source, destination)
    try:
        del registry["instances"][name]
        atomic_json(root / "instances.json", registry)
    except Exception:
        os.replace(destination, source)
        raise
    return {"status": "deleted", "instance": config, "preserved": str(destination),
            "saved": str(destination / "saved")}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=pathlib.Path,
                        default=os.environ.get("PALWORLD_MULTI_ROOT", "/srv/palworldselfhost-multi"))
    sub = parser.add_subparsers(dest="command", required=True)
    create_parser = sub.add_parser("create")
    create_parser.add_argument("name")
    create_parser.add_argument("--settings", type=pathlib.Path, required=True)
    create_parser.add_argument("--world-id")
    for field, default in (("game_port", 8211), ("query_port", 27015),
                           ("rest_port", 8212), ("rcon_port", 25575)):
        create_parser.add_argument("--" + field.replace("_", "-"), default=default)
    create_parser.add_argument("--start", action="store_true")
    sub.add_parser("list")
    for command in ("status", "start", "stop", "restart"):
        item = sub.add_parser(command); item.add_argument("name")
    delete_parser = sub.add_parser("delete"); delete_parser.add_argument("name")
    delete_parser.add_argument("--confirm", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args(); root = args.root.resolve(strict=False)
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    try:
        with (root / ".lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            registry = load_registry(root)
            if args.command == "create":
                result = create(root, args, registry)
            elif args.command == "list":
                result = {"status": "listed", "instances": registry["instances"]}
            elif args.command == "status":
                result = status(root, registry, args.name)
            elif args.command in {"start", "stop", "restart"}:
                result = lifecycle(root, registry, args.name, args.command)
            else:
                result = delete(root, registry, args.name, args.confirm)
        print(json.dumps(result, indent=2)); return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)})); return 2


if __name__ == "__main__":
    raise SystemExit(main())
