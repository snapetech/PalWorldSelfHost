#!/usr/bin/env python3
"""Diagnose and safely recover Palworld configuration files and conflicts."""

import argparse
import configparser
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import time


HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("ops_lib", HERE / "ops-lib.py")
ops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops)
INSTALL = pathlib.Path(os.environ["PALWORLD_INSTALL_DIR"])
CONFIG_PLATFORM = "WindowsServer" if os.environ.get("PALWORLD_RUNTIME") == "wine-windows" else "LinuxServer"
CONFIG = INSTALL / "Pal/Saved/Config" / CONFIG_PLATFORM
WORLD = CONFIG / "PalWorldSettings.ini"
ENGINE = CONFIG / "Engine.ini"
TEMPLATE = INSTALL / "DefaultPalWorldSettings.ini"
WORLD_RAW = ops.STATE / "settings-raw.ini"
ENGINE_RAW = ops.STATE / "engine-raw.ini"
ENV_FILE = pathlib.Path(os.environ.get("PALWORLD_ENV_FILE", "/etc/palworld-server.env"))
REQUIRED_ENV = {"PALWORLD_INSTALL_DIR", "PALWORLD_STATE_DIR", "PALWORLD_BACKUP_LOCAL_ROOT", "PALWORLD_PORT", "PALWORLD_ADMIN_PASSWORD", "PALWORLD_REST_PORT"}


def structure_issue(text):
    saw_section = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith((";", "#")): continue
        if re.fullmatch(r"\[.+\]", line): saw_section = True; continue
        if line.startswith("["): return "section header is not closed"
        if "=" not in line: return f"unparseable line: {line[:40]}"
    return None if saw_section else "no section header exists"


def world_issue(text):
    issue = structure_issue(text)
    if issue: return issue
    if "[/Script/Pal.PalGameWorldSettings]" not in text: return "PalGameWorldSettings section is missing"
    if text.count("OptionSettings=(") != 1: return "OptionSettings block is missing or duplicated"
    body = text.split("OptionSettings=(", 1)[1]
    if ")" not in body: return "OptionSettings parentheses are incomplete"
    return None


def engine_issue(text):
    issue = structure_issue(text)
    if issue: return issue
    parser = configparser.RawConfigParser(strict=False, interpolation=None)
    try: parser.read_string(text)
    except configparser.Error as exc: return str(exc)
    return None


def file_health(path, validator):
    if not path.is_file(): return {"path": str(path), "exists": False, "corrupted": False, "reason": "missing"}
    try: issue = validator(path.read_text())
    except (OSError, UnicodeError) as exc: issue = str(exc)
    return {"path": str(path), "exists": True, "corrupted": issue is not None, **({"reason": issue} if issue else {})}


def env_health():
    issues = []
    keys = []
    try: lines = ENV_FILE.read_text().splitlines()
    except OSError as exc: return {"path": str(ENV_FILE), "valid": False, "issues": [str(exc)]}
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"): continue
        match = re.match(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=", line)
        if not match: issues.append(f"line {number} is not KEY=value"); continue
        key = match.group(1)
        if key in keys: issues.append(f"{key} is defined more than once")
        keys.append(key)
    for key in sorted(REQUIRED_ENV - set(keys)): issues.append(f"required {key} is missing")
    ports = {}
    for key in ("PALWORLD_PORT", "PALWORLD_REST_PORT", "PALWORLD_OPS_PORT", "PALWORLD_RCON_PORT"):
        if key not in os.environ: continue
        try: ports[key] = int(os.environ[key])
        except ValueError: issues.append(f"{key} is not an integer")
        else:
            if not 1 <= ports[key] <= 65535: issues.append(f"{key} is outside 1-65535")
    used = {}
    for key, value in ports.items(): used.setdefault(value, []).append(key)
    for port, names in used.items():
        if len(names) > 1: issues.append(f"port {port} conflicts across {', '.join(names)}")
    return {"path": str(ENV_FILE), "valid": not issues, "issues": issues, "known_good_snapshot": str(ops.STATE / "env-known-good") if (ops.STATE / "env-known-good").is_file() else None}


def world_options():
    root = INSTALL / "Pal/Saved/SaveGames/0"
    found = []
    if root.is_dir():
        for name in ("WorldOptions.sav", "WorldOption.sav"):
            for path in root.glob(f"*/{name}"):
                if path.is_file(): found.append(str(path))
    return sorted(found)


def health():
    world = file_health(WORLD, world_issue)
    engine = file_health(ENGINE, engine_issue)
    conflicts = world_options()
    environment = env_health()
    return {
        "ok": not world["corrupted"] and not engine["corrupted"] and not conflicts and environment["valid"],
        "world": world, "engine": engine, "environment": environment,
        "world_options_conflicts": conflicts,
        "recoverable": {
            "world": TEMPLATE.is_file(), "engine": True,
            "world_options": bool(conflicts), "environment": bool(environment["known_good_snapshot"]),
        },
    }


def require_stopped():
    if os.environ.get("PALWORLD_ASSUME_SERVICE_STOPPED") == "true": return
    active = subprocess.run(["systemctl", "is-active", "--quiet", "palworld.service"], check=False).returncode == 0
    if active: raise ValueError("stop palworld.service before configuration recovery")


def preserve(path, suffix):
    if not path.exists(): return None
    destination = path.with_name(f"{path.name}.{suffix}-{time.time_ns()}.bak")
    path.rename(destination)
    return destination


def repair(kind, target=None):
    require_stopped()
    if kind == "environment":
        if os.geteuid() != 0: raise ValueError("environment recovery must run as root")
        known = ops.STATE / "env-known-good"
        if not known.is_file(): raise ValueError("no known-good environment snapshot exists")
        preserved = preserve(ENV_FILE, "invalid")
        try:
            shutil.copyfile(known, ENV_FILE)
            os.chmod(ENV_FILE, 0o640)
            stat = known.stat(); os.chown(ENV_FILE, stat.st_uid, stat.st_gid)
        except Exception:
            if preserved and preserved.exists(): preserved.rename(ENV_FILE)
            raise
        ops.audit("config.recover", "ok", kind=kind, preserved=str(preserved))
        return {"kind": kind, "path": str(ENV_FILE), "preserved": str(preserved), "restart_required": True}
    if kind == "world-options":
        candidates = {str(path.resolve()): path for path in map(pathlib.Path, world_options())}
        candidate = pathlib.Path(str(target or "")).resolve()
        if str(candidate) not in candidates: raise ValueError("WorldOptions target is not a detected conflict")
        destination = candidate.with_name(f"{candidate.name}.disabled-{time.time_ns()}")
        candidate.rename(destination)
        ops.audit("config.recover", "ok", kind=kind, preserved=str(destination))
        return {"kind": kind, "preserved": str(destination), "restart_required": True}
    if kind not in {"world", "engine"}: raise ValueError("unknown recovery kind")
    active, raw, validator = (WORLD, WORLD_RAW, world_issue) if kind == "world" else (ENGINE, ENGINE_RAW, engine_issue)
    preserved = []
    created_raw = False
    if active.exists():
        backup = preserve(active, "corrupt"); preserved.append((active, backup))
    if raw.exists() and validator(raw.read_text()) is not None:
        backup = preserve(raw, "corrupt-source"); preserved.append((raw, backup))
    if kind == "engine" and not raw.exists():
        ops.atomic_text(raw, "; regenerated by PalWorldSelfHost\n[/Script/Engine.Engine]\n")
        created_raw = True
    command = [str(HERE / "start.sh"), "--render-only"] if kind == "world" else [str(HERE / "server-config-manager.py"), "render"]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    issue = validator(active.read_text()) if result.returncode == 0 and active.is_file() else "regenerated file is missing"
    if result.returncode != 0 or issue:
        active.unlink(missing_ok=True)
        if created_raw: raw.unlink(missing_ok=True)
        for original, backup in reversed(preserved):
            if backup and backup.exists(): backup.rename(original)
        detail = (result.stdout + result.stderr).strip() if result.returncode != 0 else issue
        raise ValueError(detail or f"failed to regenerate {kind} configuration")
    paths = [str(backup) for _, backup in preserved]
    ops.audit("config.recover", "ok", kind=kind, preserved=paths)
    return {"kind": kind, "path": str(active), "preserved": paths, "restart_required": True}


def main():
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("health")
    repair_parser = sub.add_parser("repair"); repair_parser.add_argument("kind", choices=["world", "engine", "world-options", "environment"]); repair_parser.add_argument("--target"); repair_parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    try:
        if args.command == "health": result = health()
        else:
            if args.confirm != "RECOVER CONFIG": raise ValueError("confirmation required")
            with ops.operation_lock(): result = repair(args.kind, args.target)
        print(json.dumps(result, indent=2))
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__": main()
