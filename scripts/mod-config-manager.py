#!/usr/bin/env python3
"""Transactional, schema-guided configuration for installed Palworld mods."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import pathlib
import re
import time


HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("ops_lib", HERE / "ops-lib.py")
ops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops)

INSTALL = pathlib.Path(os.environ.get("PALWORLD_INSTALL_DIR", "/srv/palworld/server")).resolve()
STATE = pathlib.Path(os.environ.get("PALWORLD_STATE_DIR", "/var/lib/palworld")).resolve()
MAX_CONFIG_BYTES = 256 * 1024
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
MOD_NAME = re.compile(r"[A-Za-z0-9_.-]{1,80}")


def option(kind, category, label, default, *, minimum=None, maximum=None, step=None, max_length=None, description="", warning=""):
    result = {
        "type": kind, "category": category, "label": label,
        "default": default, "description": description,
    }
    if minimum is not None: result["min"] = minimum
    if maximum is not None: result["max"] = maximum
    if step is not None: result["step"] = step
    if max_length is not None: result["max_length"] = max_length
    if warning: result["warning"] = warning
    return result


# Current scalar Config.json surface documented by PalDefender. Arrays holding
# IPs, bans, filters, templates, or command policy stay out of browser output;
# apply merges only these named keys and preserves every unowned field.
PALDEFENDER_OPTIONS = {
    "shouldWarnCheaters": option("boolean", "Anti-cheat", "Warn detected cheaters", True),
    "shouldWarnCheatersReason": option("boolean", "Anti-cheat", "Include detection reason", True),
    "shouldKickCheaters": option("boolean", "Anti-cheat", "Kick detected cheaters", False),
    "shouldBanCheaters": option("boolean", "Anti-cheat", "Ban detected cheaters", False),
    "shouldIPBanCheaters": option("boolean", "Anti-cheat", "IP-ban detected cheaters", False, warning="Shared public addresses can cause collateral bans."),
    "steamidProtection": option("boolean", "Protection", "Reject duplicate user identities", True),
    "disableIllegalItemProtection": option("boolean", "Protection", "Disable illegal-item protection", False, warning="Allows items the anti-cheat would normally reject."),
    "doActionUponIllegalPalStats": option("boolean", "Protection", "Act on illegal Pal statistics", True),
    "preventDoctorSurgiExploit": option("boolean", "Protection", "Prevent Surgeon exploit", True),
    "doActionUponDoctorSurgiExploit": option("boolean", "Protection", "Act on Surgeon exploit", True),
    "preventUnsupportedWorkbenchRecipes": option("boolean", "Protection", "Block unsupported workbench recipes", True),
    "palStatsMaxRank": option("integer", "Protection", "Maximum Pal enhancement rank", -1, minimum=-1, maximum=20, description="-1 lets PalDefender detect the supported maximum."),
    "pvpMaxToBuildingDamage": option("integer", "Protection", "Maximum PvP building damage", 0, minimum=0, maximum=1000000),
    "pvpMaxToPalDamage": option("integer", "Protection", "Maximum PvP Pal damage", 0, minimum=0, maximum=1000000),
    "pveMaxToPalBanThreshold": option("integer", "Protection", "PvE Pal-damage ban threshold", 0, minimum=0, maximum=1000000),
    "treeLimiter": option("number", "Protection", "Minimum tree destruction interval", 0, minimum=0, maximum=5, step=0.05, description="Seconds per tree; zero disables the limiter."),
    "useWhitelist": option("boolean", "Administration", "Use PalDefender whitelist", False),
    "whitelistMessage": option("string", "Administration", "Whitelist rejection message", "You are not whitelisted.", max_length=500),
    "useAdminWhitelist": option("boolean", "Administration", "Restrict administrators by IP", False, description="Configure adminIPs outside this identifier-redacted form first."),
    "adminAutoLogin": option("boolean", "Administration", "Auto-login allowlisted administrators", False),
    "preventAdminPasswordInChat": option("boolean", "Administration", "Prevent admin-password chat leaks", True),
    "allowAdminCheats": option("boolean", "Administration", "Allow administrator cheat commands", True),
    "allowGodmodeOnehit": option("boolean", "Administration", "Allow godmode one-hit", False),
    "chatBypassWait": option("boolean", "Chat", "Remove chat cooldown", False),
    "chatMessageMaxLen": option("integer", "Chat", "Maximum chat message length", 200, minimum=1, maximum=1000),
    "announceConnections": option("boolean", "Announcements", "Announce player connections", False),
    "dontAnnounceAdminConnections": option("boolean", "Announcements", "Hide administrator connections", False),
    "announcePunishments": option("boolean", "Announcements", "Announce anti-cheat punishments", False),
    "announcePlayerDeaths": option("boolean", "Announcements", "Announce player deaths", False),
    "announceOpenOilrigBoxes": option("boolean", "Announcements", "Announce oilrig box openings", False),
    "announceHelicopterKills": option("boolean", "Announcements", "Announce helicopter kills", False),
    "announcePlayerSummons": option("boolean", "Announcements", "Announce player summons", False),
    "announceAdminSummons": option("boolean", "Announcements", "Announce administrator summons", False),
    "announceAdminSummonsKill": option("boolean", "Announcements", "Announce kills of administrator summons", False),
    "logChat": option("boolean", "Logging", "Log player chat", False, warning="Chat content can contain personal information."),
    "logRCON": option("boolean", "Logging", "Log RCON commands", False),
    "logPlayerUID": option("boolean", "Logging", "Log player UID", False, warning="Adds persistent player identifiers to mod logs."),
    "logPlayerIP": option("boolean", "Logging", "Log player IP addresses", False, warning="Adds personal network identifiers to mod logs."),
    "logNetworking": option("boolean", "Logging", "Log client network data", False, warning="High-volume diagnostic output may contain sensitive data."),
    "logNetworkingToConsole": option("boolean", "Logging", "Log network data to console", False, warning="High-volume diagnostic output may contain sensitive data."),
    "logPlayerLogins": option("boolean", "Logging", "Log player connections", False),
    "logPlayerDeaths": option("boolean", "Logging", "Log player deaths", False),
    "logPlayerBuildings": option("boolean", "Logging", "Log player construction", False),
    "logHelicopterKills": option("boolean", "Logging", "Log helicopter kills", False),
    "logPlayerSummons": option("boolean", "Logging", "Log player summons", False),
    "logPlayerCaptures": option("boolean", "Logging", "Log player captures", False),
    "logCraftings": option("boolean", "Logging", "Log player crafting", False),
    "logTechUnlocks": option("boolean", "Logging", "Log technology unlocks", False),
    "logOpenOilrigBoxes": option("boolean", "Logging", "Log oilrig box openings", False),
    "exitServerOnStartupFailure": option("boolean", "Runtime", "Exit if PalDefender cannot start", False, warning="A service restart policy must distinguish a mod startup abort from a transient crash."),
    "disableButchering": option("boolean", "Runtime", "Disable butchering", False),
    "disableRenaming": option("boolean", "Runtime", "Disable player renaming", False),
    "disablePalRenaming": option("boolean", "Runtime", "Disable Pal renaming", False),
    "OilrigGoalBoxLocktime": option("integer", "Runtime", "Oilrig goal-box lock time", 300, minimum=0, maximum=3600, description="Seconds."),
    "RCONTimeout": option("number", "Runtime", "PalDefender RCON timeout", 5, minimum=1, maximum=60, step=0.5, description="Seconds."),
    "RCONUsePacketIdFix": option("boolean", "Runtime", "Use RCON packet-ID workaround", False),
    "RCONbase64": option("boolean", "Runtime", "Use base64 RCON commands", False),
}


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def reject_constant(value):
    raise ValueError(f"non-finite JSON number {value} is not allowed")


def safe_directory(candidate):
    candidate = pathlib.Path(candidate)
    try:
        relative = candidate.relative_to(INSTALL)
    except ValueError as exc:
        raise ValueError("mod path escapes the install root") from exc
    current = INSTALL
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError("symlinked mod paths are not supported")
    if candidate.exists() and not candidate.is_dir():
        raise ValueError("mod path is not a directory")
    return candidate


def paldefender_directory():
    candidates = []
    for binaries in ("Win64", "Linux"):
        base = INSTALL / "Pal" / "Binaries" / binaries
        candidates.extend(base / name for name in ("PalDefender", "palguard"))
    for candidate in candidates:
        if candidate.exists():
            return safe_directory(candidate)
    return None


def config_file(*, required=False):
    directory = paldefender_directory()
    if directory is None:
        if required: raise ValueError("PalDefender configuration directory is not installed")
        return None
    path = directory / "Config.json"
    if path.is_symlink(): raise ValueError("symlinked PalDefender configuration is not supported")
    if required and not path.is_file(): raise ValueError("PalDefender Config.json has not been generated")
    if path.exists() and not path.is_file(): raise ValueError("PalDefender Config.json is not a regular file")
    return path


def read_config(path=None):
    path = path or config_file(required=True)
    raw = path.read_bytes()
    if len(raw) > MAX_CONFIG_BYTES: raise ValueError("PalDefender Config.json exceeds 256 KiB")
    try:
        data = json.loads(raw, parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"PalDefender Config.json is malformed: {exc}") from exc
    if not isinstance(data, dict): raise ValueError("PalDefender Config.json must contain an object")
    return raw, data


def validate_value(name, value):
    if name not in PALDEFENDER_OPTIONS: raise ValueError(f"unknown PalDefender option {name}")
    metadata = PALDEFENDER_OPTIONS[name]
    kind = metadata["type"]
    if kind == "boolean":
        if not isinstance(value, bool): raise ValueError(f"{name} requires a boolean")
    elif kind == "integer":
        if not isinstance(value, int) or isinstance(value, bool): raise ValueError(f"{name} requires an integer")
    elif kind == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            raise ValueError(f"{name} requires a finite number")
    elif kind == "string":
        if not isinstance(value, str) or len(value) > metadata["max_length"] or any(character in value for character in "\0\r\n"):
            raise ValueError(f"{name} requires a single-line string of at most {metadata['max_length']} characters")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "min" in metadata and not metadata["min"] <= value <= metadata["max"]:
            raise ValueError(f"{name} must be between {metadata['min']} and {metadata['max']}")
    return value


def validate_motd(value):
    if not isinstance(value, list) or len(value) > 30: raise ValueError("MOTD must contain at most 30 lines")
    result = []
    for line in value:
        if not isinstance(line, str) or len(line) > 500 or "\0" in line or "\r" in line or "\n" in line:
            raise ValueError("each MOTD line must be a single line of at most 500 characters")
        result.append(line)
    return result


def managed_values(data):
    result = {}
    for name in PALDEFENDER_OPTIONS:
        if name in data:
            try: result[name] = validate_value(name, data[name])
            except ValueError: continue
    motd = data.get("MOTD", data.get("motd", []))
    return result, [line for line in motd if isinstance(line, str)][:30] if isinstance(motd, list) else []


def lua_mods_directory():
    candidates = []
    for binaries in ("Win64", "Linux"):
        base = INSTALL / "Pal" / "Binaries" / binaries
        candidates.extend((base / "ue4ss" / "Mods", base / "Mods"))
    candidates.append(INSTALL / "ue4ss" / "Mods")
    for candidate in candidates:
        if candidate.is_dir(): return safe_directory(candidate)
    return None


def parse_mods_txt(directory):
    values = {}
    path = directory / "mods.txt"
    if path.is_symlink(): raise ValueError("symlinked UE4SS mods.txt is not supported")
    if not path.is_file(): return values
    if path.stat().st_size > 64 * 1024: raise ValueError("UE4SS mods.txt exceeds 64 KiB")
    for line in path.read_text(errors="strict").splitlines():
        match = re.fullmatch(r"\s*([A-Za-z0-9_.-]{1,80})\s*:\s*([01])\s*", line)
        if match: values[match.group(1)] = match.group(2) == "1"
    return values


def list_lua_mods():
    directory = lua_mods_directory()
    if directory is None: return {"installed": False, "path": None, "mods": []}
    flags = parse_mods_txt(directory)
    result = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
        if entry.name.casefold() == "shared" or not entry.is_dir() or entry.is_symlink() or not MOD_NAME.fullmatch(entry.name):
            continue
        marker = entry / "enabled.txt"
        marker_enabled = False
        if marker.is_file() and not marker.is_symlink() and marker.stat().st_size <= 32:
            marker_enabled = marker.read_text(errors="replace").strip().casefold() not in {"0", "false", "no", "off", "disabled"}
        result.append({"name": entry.name, "enabled": flags.get(entry.name, marker_enabled)})
    return {"installed": True, "path": str(directory.relative_to(INSTALL)), "mods": result}


def status():
    path = config_file()
    payload = {
        "paldefender": {
            "installed": path is not None,
            "config_exists": bool(path and path.is_file()),
            "path": str(path.relative_to(INSTALL)) if path else None,
            "schema": PALDEFENDER_OPTIONS,
            "values": {}, "motd": [], "sha256": None, "unknown_key_count": 0,
        },
        "ue4ss": list_lua_mods(),
    }
    if path and path.is_file():
        raw, data = read_config(path)
        values, motd = managed_values(data)
        payload["paldefender"].update({
            "values": values, "motd": motd, "sha256": sha256_bytes(raw),
            "unknown_key_count": len(set(data) - set(PALDEFENDER_OPTIONS) - {"MOTD", "motd"}),
        })
    return payload


def planned(payload):
    if not isinstance(payload, dict): raise ValueError("mod configuration payload must be an object")
    raw, current = read_config()
    updates = payload.get("updates", {})
    if not isinstance(updates, dict) or len(updates) > len(PALDEFENDER_OPTIONS):
        raise ValueError("updates must be a bounded object")
    proposed = dict(current)
    changes = []
    for name, value in updates.items():
        value = validate_value(name, value)
        if current.get(name) != value: changes.append({"key": name, "before": current.get(name), "after": value})
        proposed[name] = value
    if "motd" in payload:
        motd = validate_motd(payload["motd"])
        key = "motd" if "motd" in current and "MOTD" not in current else "MOTD"
        prior = current.get(key, [])
        if prior != motd: changes.append({"key": key, "before_lines": len(prior) if isinstance(prior, list) else 0, "after_lines": len(motd)})
        proposed[key] = motd
    encoded = (json.dumps(proposed, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    if len(encoded) > MAX_CONFIG_BYTES: raise ValueError("planned PalDefender Config.json exceeds 256 KiB")
    return {
        "component": "paldefender", "current_sha256": sha256_bytes(raw),
        "after_sha256": sha256_bytes(encoded), "changes": changes,
        "changed": encoded != raw, "restart_or_reload_required": encoded != raw,
        "preserved_unknown_keys": len(set(current) - set(PALDEFENDER_OPTIONS) - {"MOTD", "motd"}),
    }, encoded


def snapshot(raw, suffix="json"):
    directory = STATE / "mod-config-backups"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"paldefender-{time.time_ns()}.{suffix}"
    ops.atomic_text(path, raw.decode("utf-8"), 0o600)
    return path


def apply(payload, expected_sha256, confirmation):
    if confirmation != "APPLY MOD CONFIG": raise ValueError("exact confirmation APPLY MOD CONFIG is required")
    if not re.fullmatch(r"[0-9a-f]{64}", str(expected_sha256 or "")): raise ValueError("reviewed current SHA-256 is required")
    with ops.operation_lock():
        plan, encoded = planned(payload)
        if plan["current_sha256"] != expected_sha256: raise ValueError("PalDefender configuration changed after review")
        path = config_file(required=True)
        backup = snapshot(path.read_bytes())
        try:
            ops.atomic_text(path, encoded.decode("utf-8"), 0o640)
            installed = path.read_bytes()
            if sha256_bytes(installed) != plan["after_sha256"]: raise ValueError("post-write mod configuration verification failed")
            read_config(path)
        except Exception:
            ops.atomic_text(path, backup.read_text(), 0o640)
            raise
    ops.audit("mod-config.apply", "ok", component="paldefender", keys=[item["key"] for item in plan["changes"]], backup=backup.name)
    return {**plan, "backup": backup.name}


def rollback(expected_sha256, confirmation):
    if confirmation != "ROLLBACK MOD CONFIG": raise ValueError("exact confirmation ROLLBACK MOD CONFIG is required")
    if not re.fullmatch(r"[0-9a-f]{64}", str(expected_sha256 or "")): raise ValueError("current SHA-256 is required")
    backups = sorted((STATE / "mod-config-backups").glob("paldefender-*.json"), key=lambda item: item.stat().st_mtime_ns, reverse=True)
    if not backups: raise ValueError("no PalDefender configuration snapshot is available")
    with ops.operation_lock():
        path = config_file(required=True)
        raw, _ = read_config(path)
        if sha256_bytes(raw) != expected_sha256: raise ValueError("PalDefender configuration changed after review")
        restored_raw, _ = read_config(backups[0])
        current_backup = snapshot(raw)
        try:
            ops.atomic_text(path, restored_raw.decode("utf-8"), 0o640)
            read_config(path)
        except Exception:
            ops.atomic_text(path, raw.decode("utf-8"), 0o640)
            current_backup.unlink(missing_ok=True)
            raise
    ops.audit("mod-config.rollback", "ok", component="paldefender", restored=backups[0].name, backup=current_backup.name)
    return {"restored": backups[0].name, "backup": current_backup.name, "sha256": sha256_bytes(restored_raw), "restart_or_reload_required": True}


def set_lua_mod(name, enabled, confirmation):
    if confirmation != "SET LUA MOD STATE": raise ValueError("exact confirmation SET LUA MOD STATE is required")
    if not MOD_NAME.fullmatch(str(name or "")) or not isinstance(enabled, bool): raise ValueError("valid Lua mod name and boolean state are required")
    with ops.operation_lock():
        directory = lua_mods_directory()
        if directory is None: raise ValueError("UE4SS Mods directory is not installed")
        mod_dir = directory / name
        if not mod_dir.is_dir() or mod_dir.is_symlink(): raise ValueError("unknown or symlinked Lua mod")
        path = directory / "mods.txt"
        if path.is_symlink(): raise ValueError("symlinked UE4SS mods.txt is not supported")
        marker = mod_dir / "enabled.txt"
        if marker.is_symlink(): raise ValueError("symlinked Lua mod enable marker is not supported")
        marker_prior = marker.read_bytes() if marker.is_file() else None
        prior = path.read_text() if path.is_file() else ""
        lines = prior.splitlines()
        replacement = f"{name} : {1 if enabled else 0}"
        matched = False
        for index, line in enumerate(lines):
            if re.fullmatch(rf"\s*{re.escape(name)}\s*:\s*[01]\s*", line):
                lines[index] = replacement; matched = True
        if not matched: lines.insert(0, replacement)
        proposed = "\n".join(lines).rstrip() + "\n"
        if len(proposed.encode()) > 64 * 1024: raise ValueError("planned UE4SS mods.txt exceeds 64 KiB")
        backup = snapshot(prior.encode(), "mods.txt")
        try:
            ops.atomic_text(path, proposed, 0o640)
            if not enabled and marker.is_file(): marker.unlink()
            if parse_mods_txt(directory).get(name) is not enabled: raise ValueError("post-write Lua mod state verification failed")
        except Exception:
            ops.atomic_text(path, prior, 0o640)
            if marker_prior is None: marker.unlink(missing_ok=True)
            else: ops.atomic_text(marker, marker_prior.decode("utf-8"), 0o640)
            raise
    ops.audit("lua-mod.state", "ok", mod=name, enabled=enabled, backup=backup.name)
    return {"name": name, "enabled": enabled, "backup": backup.name, "restart_or_reload_required": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    item = sub.add_parser("plan"); item.add_argument("payload")
    item = sub.add_parser("apply"); item.add_argument("payload"); item.add_argument("--expected-sha256", required=True); item.add_argument("--confirm", default="")
    item = sub.add_parser("rollback"); item.add_argument("--expected-sha256", required=True); item.add_argument("--confirm", default="")
    item = sub.add_parser("lua-set"); item.add_argument("name"); item.add_argument("enabled", choices=("true", "false")); item.add_argument("--confirm", default="")
    args = parser.parse_args()
    try:
        if args.command == "status": result = status()
        elif args.command == "plan": result, _ = planned(json.loads(args.payload))
        elif args.command == "apply": result = apply(json.loads(args.payload), args.expected_sha256, args.confirm)
        elif args.command == "rollback": result = rollback(args.expected_sha256, args.confirm)
        else: result = set_lua_mod(args.name, args.enabled == "true", args.confirm)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        raise SystemExit(1)


if __name__ == "__main__": main()
