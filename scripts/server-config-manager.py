#!/usr/bin/env python3
"""Transactional Engine.ini and dedicated-server launch configuration."""

import argparse
import configparser
import difflib
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import time


HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("ops_lib", HERE / "ops-lib.py")
ops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops)
INSTALL = pathlib.Path(os.environ["PALWORLD_INSTALL_DIR"])
CONFIG_PLATFORM = "WindowsServer" if os.environ.get("PALWORLD_RUNTIME") == "wine-windows" else "LinuxServer"
ACTIVE = INSTALL / "Pal/Saved/Config" / CONFIG_PLATFORM / "Engine.ini"
ENGINE_STATE = ops.STATE / "engine-settings.json"
LAUNCH_STATE = ops.STATE / "launch-options.json"
RAW_STATE = ops.STATE / "engine-raw.ini"
PENDING = ops.STATE / "server-config-restart-required.json"
MAX_RAW = 60 * 1024

NET = "/Script/OnlineSubsystemUtils.IpNetDriver"
ENGINE = "/Script/Engine.Engine"
GC = "/Script/Engine.GarbageCollectionSettings"
ENGINE_OPTIONS = {
    "NetServerMaxTickRate": {"section": NET, "type": "integer", "default": 30, "min": 20, "max": 120, "category": "Network", "label": "Server tick rate", "description": "World updates sent per second; higher values increase CPU and bandwidth demand."},
    "MaxClientRate": {"section": NET, "type": "integer", "default": 15000, "min": 10000, "max": 125000000, "step": 10000, "category": "Network", "label": "Per-player bandwidth", "description": "Maximum bytes per second for each client."},
    "MaxInternetClientRate": {"section": NET, "type": "integer", "default": 10000, "min": 10000, "max": 125000000, "step": 10000, "category": "Network", "label": "Internet-player bandwidth", "description": "Maximum bytes per second for non-LAN clients."},
    "ConnectionTimeout": {"section": NET, "type": "number", "default": 60, "min": 10, "max": 300, "step": 5, "category": "Network", "label": "Connection timeout", "description": "Seconds before an established unresponsive connection is dropped."},
    "InitialConnectTimeout": {"section": NET, "type": "number", "default": 60, "min": 10, "max": 300, "step": 5, "category": "Network", "label": "Initial connection timeout", "description": "Seconds allowed for a player to finish initial world loading."},
    "bUseFixedFrameRate": {"section": ENGINE, "type": "boolean", "default": False, "category": "Frame rate", "label": "Use fixed frame rate", "description": "Runs simulation with a fixed frame step; verify server FPS after enabling."},
    "FixedFrameRate": {"section": ENGINE, "type": "number", "default": 30, "min": 20, "max": 120, "step": 1, "category": "Frame rate", "label": "Fixed frame rate", "description": "Fixed simulation frame rate when fixed-frame mode is enabled."},
    "bSmoothFrameRate": {"section": ENGINE, "type": "boolean", "default": False, "category": "Frame rate", "label": "Smooth frame rate", "description": "Smooths frame-time variation to reduce simulation spikes."},
    "gc.TimeBetweenPurgingPendingKillObjects": {"section": GC, "type": "integer", "default": 60, "min": 10, "max": 600, "category": "Memory", "label": "Garbage collection interval", "description": "Seconds between pending-object purges; shorter intervals trade memory for more frequent pauses."},
}
LAUNCH_OPTIONS = {
    "useperfthreads": {"arg": "useperfthreads", "type": "boolean", "default": True, "category": "Performance", "label": "Performance threads", "description": "Legacy toolkit default; benchmark disabled on Palworld 1.0 or newer."},
    "NoAsyncLoadingThread": {"arg": "NoAsyncLoadingThread", "type": "boolean", "default": True, "category": "Performance", "label": "Disable async loading thread", "description": "Legacy performance flag; benchmark disabled on current builds."},
    "UseMultithreadForDS": {"arg": "UseMultithreadForDS", "type": "boolean", "default": True, "category": "Performance", "label": "Dedicated-server multithreading", "description": "Legacy performance flag; benchmark against the unconfigured behavior."},
    "NumberOfWorkerThreadsServer": {"arg": "NumberOfWorkerThreadsServer", "type": "integer", "default": 0, "min": 0, "max": 128, "category": "Performance", "label": "Worker threads", "description": "Zero leaves worker selection to the game/runtime."},
    "publiclobby": {"arg": "publiclobby", "type": "boolean", "default": True, "category": "General", "label": "Community-server listing", "description": "Advertise the world in the community server list."},
    "logformat": {"arg": "logformat", "type": "choice", "default": "text", "choices": ["text", "json"], "category": "General", "label": "Log format", "description": "Select text or structured JSON game logs."},
}
PRESETS = {
    "Engine defaults": {key: value["default"] for key, value in ENGINE_OPTIONS.items()},
    "Balanced": {"NetServerMaxTickRate": 60, "MaxClientRate": 100000, "MaxInternetClientRate": 100000, "ConnectionTimeout": 60, "InitialConnectTimeout": 60, "bUseFixedFrameRate": False, "FixedFrameRate": 60, "bSmoothFrameRate": True, "gc.TimeBetweenPurgingPendingKillObjects": 60},
    "Performance": {"NetServerMaxTickRate": 90, "MaxClientRate": 150000, "MaxInternetClientRate": 150000, "ConnectionTimeout": 60, "InitialConnectTimeout": 60, "bUseFixedFrameRate": True, "FixedFrameRate": 90, "bSmoothFrameRate": True, "gc.TimeBetweenPurgingPendingKillObjects": 30},
}


def serialized(value):
    if isinstance(value, bool): return "True" if value else "False"
    return str(value)


def validate_value(name, value, metadata):
    kind = metadata["type"]
    if kind == "boolean" and not isinstance(value, bool): raise ValueError(f"{name} requires boolean")
    if kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)): raise ValueError(f"{name} requires integer")
    if kind == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)): raise ValueError(f"{name} requires number")
    if kind == "choice" and value not in metadata["choices"]: raise ValueError(f"{name} has an invalid choice")
    if isinstance(value, (int, float)) and "min" in metadata and not metadata["min"] <= value <= metadata["max"]:
        raise ValueError(f"{name} must be between {metadata['min']} and {metadata['max']}")


def merge_ini(text, values):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for key, value in values.items():
        meta = ENGINE_OPTIONS[key]
        section = meta["section"]
        section_pattern = re.compile(rf"(?m)^\[{re.escape(section)}\]\s*$")
        match = section_pattern.search(text)
        if not match:
            if text and not text.endswith("\n"): text += "\n"
            text += f"\n[{section}]\n{key}={serialized(value)}\n"
            continue
        next_section = re.search(r"(?m)^\[.+\]\s*$", text[match.end():])
        end = match.end() + (next_section.start() if next_section else len(text[match.end():]))
        block = text[match.end():end]
        pattern = re.compile(rf"(?m)^(\s*{re.escape(key)}\s*=).*$")
        if pattern.search(block):
            block = pattern.sub(lambda item: item.group(1) + serialized(value), block, count=1)
        else:
            block = block.rstrip("\n") + f"\n{key}={serialized(value)}\n"
        text = text[:match.end()] + block + text[end:]
    return text.lstrip("\n")


def validate_raw(text):
    if not isinstance(text, str): raise ValueError("Engine.ini must be text")
    if len(text.encode()) > MAX_RAW: raise ValueError(f"Engine.ini exceeds {MAX_RAW} bytes")
    if "\0" in text: raise ValueError("Engine.ini contains a NUL byte")
    parser = configparser.RawConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    try: parser.read_string(text or "[Empty]\n")
    except configparser.Error as exc: raise ValueError(f"Engine.ini is malformed: {exc}") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n")


def source_text():
    if RAW_STATE.is_file(): return RAW_STATE.read_text()
    if ACTIVE.is_file(): return ACTIVE.read_text()
    return ""


def render():
    raw = validate_raw(source_text())
    values = ops.read_json(ENGINE_STATE, {})
    for key, value in values.items(): validate_value(key, value, ENGINE_OPTIONS[key])
    rendered = merge_ini(raw, values)
    ACTIVE.parent.mkdir(parents=True, exist_ok=True)
    ops.atomic_text(ACTIVE, rendered, 0o640)
    return rendered


def launch_values():
    saved = ops.read_json(LAUNCH_STATE, {})
    return {key: saved.get(key, meta["default"]) for key, meta in LAUNCH_OPTIONS.items()}


def launch_args():
    result = []
    for key, value in launch_values().items():
        meta = LAUNCH_OPTIONS[key]; validate_value(key, value, meta)
        if meta["type"] == "boolean" and value: result.append(f"-{meta['arg']}")
        elif meta["type"] == "integer" and value > 0: result.append(f"-{meta['arg']}={value}")
        elif meta["type"] == "choice" and value != meta["default"]: result.append(f"-{meta['arg']}={value}")
    return result


def plan(payload):
    engine = {**ops.read_json(ENGINE_STATE, {}), **payload.get("engine", {})}
    launch = {**ops.read_json(LAUNCH_STATE, {}), **payload.get("launch", {})}
    for key, value in engine.items():
        if key not in ENGINE_OPTIONS: raise ValueError(f"unknown Engine.ini option {key}")
        validate_value(key, value, ENGINE_OPTIONS[key])
    for key, value in launch.items():
        if key not in LAUNCH_OPTIONS: raise ValueError(f"unknown launch option {key}")
        validate_value(key, value, LAUNCH_OPTIONS[key])
    preview_ini = merge_ini(validate_raw(source_text()), engine)
    return {"engine": engine, "launch": launch, "preview_ini": preview_ini, "restart_required": True}


def apply(payload):
    preview = plan(payload)
    backup = ops.STATE / f"server-config-{time.time_ns()}.json"
    ops.atomic_json(backup, {"engine": ops.read_json(ENGINE_STATE, {}), "launch": ops.read_json(LAUNCH_STATE, {})})
    old_engine = ENGINE_STATE.read_text() if ENGINE_STATE.exists() else None
    old_launch = LAUNCH_STATE.read_text() if LAUNCH_STATE.exists() else None
    old_active = ACTIVE.read_text() if ACTIVE.exists() else None
    old_raw = RAW_STATE.read_text() if RAW_STATE.exists() else None
    try:
        if old_raw is None and old_active is not None:
            ops.atomic_text(RAW_STATE, validate_raw(old_active))
        ops.atomic_json(ENGINE_STATE, preview["engine"]); ops.atomic_json(LAUNCH_STATE, preview["launch"])
        render()
    except Exception:
        for path, prior in ((ENGINE_STATE, old_engine), (LAUNCH_STATE, old_launch), (ACTIVE, old_active), (RAW_STATE, old_raw)):
            if prior is None: path.unlink(missing_ok=True)
            else: ops.atomic_text(path, prior, 0o640 if path == ACTIVE else 0o660)
        raise
    ops.atomic_json(PENDING, {"applied_at": int(time.time()), "source": "engine-launch"})
    ops.audit("server-config.apply", "ok", engine=sorted(payload.get("engine", {})), launch=sorted(payload.get("launch", {})), backup=str(backup))
    return {**preview, "backup": str(backup), "launch_args": launch_args()}


def newest(pattern):
    matches = sorted(ops.STATE.glob(pattern), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    if not matches: raise ValueError("no configuration snapshot is available")
    return matches[0]


def rollback():
    backup = newest("server-config-[0-9]*.json")
    snapshot = ops.read_json(backup, None)
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("engine"), dict) or not isinstance(snapshot.get("launch"), dict):
        raise ValueError(f"invalid configuration snapshot {backup.name}")
    current = ops.STATE / f"server-config-{time.time_ns()}.json"
    ops.atomic_json(current, {"engine": ops.read_json(ENGINE_STATE, {}), "launch": ops.read_json(LAUNCH_STATE, {})})
    old_engine = ENGINE_STATE.read_text() if ENGINE_STATE.exists() else None
    old_launch = LAUNCH_STATE.read_text() if LAUNCH_STATE.exists() else None
    old_active = ACTIVE.read_text() if ACTIVE.exists() else None
    try:
        for key, value in snapshot["engine"].items():
            if key not in ENGINE_OPTIONS: raise ValueError(f"unknown Engine.ini option {key}")
            validate_value(key, value, ENGINE_OPTIONS[key])
        for key, value in snapshot["launch"].items():
            if key not in LAUNCH_OPTIONS: raise ValueError(f"unknown launch option {key}")
            validate_value(key, value, LAUNCH_OPTIONS[key])
        restored = {
            "engine": snapshot["engine"], "launch": snapshot["launch"],
            "preview_ini": merge_ini(validate_raw(source_text()), snapshot["engine"]),
            "restart_required": True,
        }
        ops.atomic_json(ENGINE_STATE, restored["engine"])
        ops.atomic_json(LAUNCH_STATE, restored["launch"])
        render()
    except Exception:
        for path, prior in ((ENGINE_STATE, old_engine), (LAUNCH_STATE, old_launch), (ACTIVE, old_active)):
            if prior is None: path.unlink(missing_ok=True)
            else: ops.atomic_text(path, prior, 0o640 if path == ACTIVE else 0o660)
        current.unlink(missing_ok=True)
        raise
    ops.atomic_json(PENDING, {"applied_at": int(time.time()), "source": "engine-launch-rollback"})
    ops.audit("server-config.rollback", "ok", restored=str(backup), backup=str(current))
    return {**restored, "restored": str(backup), "backup": str(current), "launch_args": launch_args()}


def raw_plan(content):
    proposed = validate_raw(content)
    diff = "\n".join(difflib.unified_diff(source_text().splitlines(), proposed.splitlines(), "current-Engine.ini", "proposed-Engine.ini", lineterm=""))
    return {"changed": proposed != source_text(), "diff": diff, "bytes": len(proposed.encode()), "restart_required": proposed != source_text()}, proposed


def raw_apply(content):
    result, proposed = raw_plan(content)
    backup = ops.STATE / f"engine-raw-{time.time_ns()}.ini"
    ops.atomic_text(backup, source_text())
    prior = RAW_STATE.read_text() if RAW_STATE.exists() else None
    try:
        ops.atomic_text(RAW_STATE, proposed)
        render()
    except Exception:
        if prior is None: RAW_STATE.unlink(missing_ok=True)
        else: ops.atomic_text(RAW_STATE, prior)
        render()
        backup.unlink(missing_ok=True)
        raise
    ops.atomic_json(PENDING, {"applied_at": int(time.time()), "source": "engine-raw"})
    ops.audit("engine.raw.apply", "ok", backup=str(backup))
    return {**result, "backup": str(backup)}


def raw_rollback():
    backup = newest("engine-raw-*.ini")
    proposed = validate_raw(backup.read_text())
    current = ops.STATE / f"engine-raw-{time.time_ns()}.ini"
    ops.atomic_text(current, source_text())
    prior = RAW_STATE.read_text() if RAW_STATE.exists() else None
    try:
        ops.atomic_text(RAW_STATE, proposed)
        render()
    except Exception:
        if prior is None: RAW_STATE.unlink(missing_ok=True)
        else: ops.atomic_text(RAW_STATE, prior)
        render()
        current.unlink(missing_ok=True)
        raise
    ops.atomic_json(PENDING, {"applied_at": int(time.time()), "source": "engine-raw-rollback"})
    ops.audit("engine.raw.rollback", "ok", restored=str(backup), backup=str(current))
    return {"restored": str(backup), "backup": str(current), "restart_required": True}


def schema_payload():
    return {"engine": ENGINE_OPTIONS, "launch": LAUNCH_OPTIONS, "engine_values": ops.read_json(ENGINE_STATE, {}), "launch_values": launch_values(), "presets": PRESETS, "launch_args": launch_args(), "pending_restart": ops.read_json(PENDING, {})}


def main():
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("schema"); sub.add_parser("render"); sub.add_parser("launch-args"); sub.add_parser("raw-show")
    for name in ("plan", "apply"):
        item = sub.add_parser(name); item.add_argument("payload"); item.add_argument("--confirm", default="")
    rollback_parser = sub.add_parser("rollback"); rollback_parser.add_argument("--confirm", default="")
    raw_plan_parser = sub.add_parser("raw-plan"); raw_plan_parser.add_argument("content")
    raw_apply_parser = sub.add_parser("raw-apply"); raw_apply_parser.add_argument("content"); raw_apply_parser.add_argument("--confirm", default="")
    raw_rollback_parser = sub.add_parser("raw-rollback"); raw_rollback_parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    try:
        if args.command == "schema": result = schema_payload()
        elif args.command == "render": result = {"path": str(ACTIVE), "bytes": len(render().encode())}
        elif args.command == "launch-args": print("\n".join(launch_args())); return
        elif args.command == "raw-show": result = {"content": source_text(), "max_bytes": MAX_RAW}
        elif args.command == "plan": result = plan(json.loads(args.payload))
        elif args.command == "apply":
            if args.confirm != "APPLY SERVER CONFIG": raise ValueError("confirmation required")
            with ops.operation_lock(): result = apply(json.loads(args.payload))
        elif args.command == "rollback":
            if args.confirm != "ROLLBACK SERVER CONFIG": raise ValueError("confirmation required")
            with ops.operation_lock(): result = rollback()
        elif args.command == "raw-plan": result, _ = raw_plan(args.content)
        elif args.command == "raw-apply":
            if args.confirm != "APPLY ENGINE INI": raise ValueError("confirmation required")
            with ops.operation_lock(): result = raw_apply(args.content)
        elif args.command == "raw-rollback":
            if args.confirm != "ROLLBACK ENGINE INI": raise ValueError("confirmation required")
            with ops.operation_lock(): result = raw_rollback()
        print(json.dumps(result, indent=2))
    except (ValueError, KeyError, json.JSONDecodeError, OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__": main()
