#!/usr/bin/env python3
"""Plan and bind a non-destructive adoption of an existing Palworld server tree."""

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import tempfile
import time


APP_ID = "2394010"
MAX_WORLDS = 128
MAX_PLAYERS = 100_000


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def regular_file(path, label, *, executable=False):
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"{label} is missing or is not a regular file")
    if executable and not os.access(path, os.X_OK):
        raise ValueError(f"{label} is not executable")
    stat = path.stat()
    return {"path": str(path), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": sha256(path)}


def refuse_symlink_components(path, root):
    current = root
    try:
        parts = path.relative_to(root).parts
    except ValueError as exc:
        raise ValueError("managed path escapes the install directory") from exc
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"managed path contains a symlink: {current}")


def path_is_within(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def running_processes(install):
    found = []
    proc = pathlib.Path("/proc")
    if not proc.is_dir():
        return found
    for item in proc.iterdir():
        if not item.name.isdigit():
            continue
        try:
            executable = (item / "exe").resolve(strict=True)
            executable.relative_to(install)
        except (FileNotFoundError, PermissionError, OSError, ValueError):
            continue
        found.append(int(item.name))
    return sorted(found)


def manifest_path(install):
    candidates = (
        install.parent / "steamapps" / f"appmanifest_{APP_ID}.acf",
        install / "steamapps" / f"appmanifest_{APP_ID}.acf",
    )
    for candidate in candidates:
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    raise ValueError(f"Steam appmanifest_{APP_ID}.acf is missing")


def build_id(manifest):
    match = re.search(r'"buildid"\s+"(\d+)"', manifest.read_text(errors="replace"))
    if not match:
        raise ValueError("Steam appmanifest has no numeric buildid")
    return match.group(1)


def world_summary(install):
    levels = sorted((install / "Pal/Saved/SaveGames/0").glob("*/Level.sav"))
    if len(levels) > MAX_WORLDS:
        raise ValueError(f"more than {MAX_WORLDS} worlds were found; select and reduce the tree before adoption")
    worlds = []
    total_players = 0
    for level in levels:
        refuse_symlink_components(level, install)
        if level.is_symlink() or not level.is_file():
            raise ValueError("world Level.sav may not be a symlink or special file")
        players = list(level.parent.glob("Players/*.sav"))
        for player in players:
            refuse_symlink_components(player, install)
        if any(player.is_symlink() or not player.is_file() for player in players):
            raise ValueError("player saves may not be symlinks or special files")
        total_players += len(players)
        if total_players > MAX_PLAYERS:
            raise ValueError(f"more than {MAX_PLAYERS} player saves were found")
        worlds.append({"world": level.parent.name, "level_bytes": level.stat().st_size, "players": len(players)})
    return worlds


def adoption_plan(install, state):
    install = pathlib.Path(install)
    state = pathlib.Path(state)
    if not install.is_absolute() or install == pathlib.Path("/"):
        raise ValueError("install directory must be an absolute non-root path")
    if not install.is_dir() or install.is_symlink():
        raise ValueError("install directory is missing or is a symlink")
    install = install.resolve()
    state_resolved = state.resolve(strict=False)
    if path_is_within(state_resolved, install):
        raise ValueError("state directory may not be inside the adopted install tree")
    if path_is_within(install, state_resolved):
        raise ValueError("adopted install tree may not be inside the state directory")
    if (state / "adoption-receipt.json").exists():
        raise ValueError("this state directory already contains an adoption receipt")
    if (state / "settings-raw.ini").exists():
        raise ValueError("state already contains managed raw settings; use migration instead of adoption")

    launcher = install / "PalServer.sh"
    server = install / "Pal/Binaries/Linux/PalServer-Linux-Shipping"
    defaults = install / "DefaultPalWorldSettings.ini"
    for path in (launcher, server, defaults):
        refuse_symlink_components(path, install)
    files = {
        "launcher": regular_file(launcher, "PalServer.sh", executable=True),
        "server": regular_file(server, "Linux server executable", executable=True),
        "defaults": regular_file(defaults, "DefaultPalWorldSettings.ini"),
    }
    active = install / "Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"
    if active.exists():
        refuse_symlink_components(active, install)
        files["active_settings"] = regular_file(active, "active PalWorldSettings.ini")
    manifest = manifest_path(install)
    if manifest.parent.is_symlink():
        raise ValueError("Steam manifest directory may not be a symlink")
    files["appmanifest"] = regular_file(manifest, "Steam appmanifest")
    worlds = world_summary(install)
    running = running_processes(install)
    binding = {"install_dir": str(install), "files": files, "worlds": worlds}
    fingerprint = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "status": "ready" if not running else "blocked-running",
        "install_dir": str(install),
        "state_dir": str(state.resolve(strict=False)),
        "build_id": build_id(manifest),
        "worlds": worlds,
        "files": files,
        "running_process_ids": running,
        "fingerprint": fingerprint,
        "changes": [
            "preserve active settings and seed them as the managed raw configuration base",
            "record an adoption receipt bound to this exact fingerprint",
            "transfer the existing tree to the configured locked service account",
            "install and start management services without running SteamCMD",
        ],
        "game_files_reinstalled": False,
    }


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, path)
    finally:
        try: os.unlink(temporary)
        except FileNotFoundError: pass


def adopt(install, state, expected_fingerprint, confirm):
    if confirm != "ADOPT EXISTING INSTALL":
        raise ValueError("confirmation required")
    plan = adoption_plan(install, state)
    if plan["status"] != "ready":
        raise ValueError("an unmanaged Palworld process is running from the selected tree; stop it before adoption")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_fingerprint or "") or plan["fingerprint"] != expected_fingerprint:
        raise ValueError("existing-install fingerprint does not match the reviewed plan")
    state = pathlib.Path(state)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    active = pathlib.Path(plan["install_dir"]) / "Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"
    preserved = None
    if active.is_file():
        preserved = state / "adoption" / stamp / "PalWorldSettings.ini"
        preserved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(active, preserved)
        raw = state / "settings-raw.ini"
        raw.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(active, raw)
        os.chmod(raw, 0o640)
    receipt = {
        **plan,
        "status": "adopted",
        "adopted_at": stamp,
        "preserved_settings": str(preserved) if preserved else None,
    }
    atomic_json(state / "adoption-receipt.json", receipt)
    return receipt


def verify_receipt(install, state, expected_fingerprint):
    install = pathlib.Path(install).resolve(strict=False)
    state = pathlib.Path(state).resolve(strict=False)
    receipt_path = state / "adoption-receipt.json"
    if not receipt_path.is_file() or receipt_path.is_symlink() or receipt_path.stat().st_size > 1024 * 1024:
        raise ValueError("adoption receipt is missing, linked, or oversized")
    try:
        receipt = json.loads(receipt_path.read_text())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("adoption receipt is not valid JSON") from exc
    if receipt.get("status") != "adopted":
        raise ValueError("adoption receipt does not record a completed adoption")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_fingerprint or "") or receipt.get("fingerprint") != expected_fingerprint:
        raise ValueError("adoption receipt fingerprint does not match the reviewed plan")
    if pathlib.Path(receipt.get("install_dir", "")).resolve(strict=False) != install:
        raise ValueError("adoption receipt belongs to a different install directory")

    files = receipt.get("files")
    if not isinstance(files, dict):
        raise ValueError("adoption receipt has no critical-file inventory")
    verified = {}
    for name in ("launcher", "server", "defaults", "appmanifest"):
        recorded = files.get(name)
        if not isinstance(recorded, dict):
            raise ValueError(f"adoption receipt is missing {name}")
        path = pathlib.Path(recorded.get("path", ""))
        if name == "appmanifest":
            if path.resolve(strict=False) != manifest_path(install).resolve(strict=False):
                raise ValueError("adoption receipt appmanifest path changed")
        else:
            try:
                path.resolve(strict=False).relative_to(install)
            except ValueError as exc:
                raise ValueError(f"adoption receipt {name} escapes the install directory") from exc
            refuse_symlink_components(path, install)
        current = regular_file(path, name, executable=name in {"launcher", "server"})
        if current["sha256"] != recorded.get("sha256") or current["bytes"] != recorded.get("bytes"):
            raise ValueError(f"adopted {name} changed after the reviewed handoff")
        verified[name] = current
    return {
        "status": "verified",
        "fingerprint": expected_fingerprint,
        "install_dir": str(install),
        "files": verified,
        "game_files_reinstalled": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "adopt", "verify"):
        item = sub.add_parser(name)
        item.add_argument("--install-dir", required=True)
        item.add_argument("--state-dir", required=True)
        if name in {"adopt", "verify"}:
            item.add_argument("--fingerprint", required=True)
        if name == "adopt":
            item.add_argument("--confirm", default="")
    args = parser.parse_args()
    try:
        if args.command == "plan":
            result = adoption_plan(args.install_dir, args.state_dir)
        elif args.command == "adopt":
            result = adopt(args.install_dir, args.state_dir, args.fingerprint, args.confirm)
        else:
            result = verify_receipt(args.install_dir, args.state_dir, args.fingerprint)
        print(json.dumps(result, indent=2))
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__":
    main()
