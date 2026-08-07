#!/usr/bin/env python3
"""Preserve, pin, inspect, and restore Palworld executable builds without touching saves."""

import argparse
import hashlib
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
INSTALL = pathlib.Path(os.environ["PALWORLD_INSTALL_DIR"]).resolve()
ROOT = pathlib.Path(os.environ.get("PALWORLD_BUILD_SNAPSHOT_ROOT", pathlib.Path(os.environ["PALWORLD_BACKUP_LOCAL_ROOT"]) / ".builds")).resolve()
PIN = ops.STATE / "steam-build-pin.json"
APP_ID = "2394010"
if os.environ.get("PALWORLD_RUNTIME") == "wine-windows":
    CRITICAL = ("PalServer.exe", "Pal/Binaries/Win64/PalServer-Win64-Shipping-Cmd.exe")
    REQUIRED_LAUNCHER = "PalServer.exe"
else:
    CRITICAL = ("PalServer.sh", "Pal/Binaries/Linux/PalServer-Linux-Shipping")
    REQUIRED_LAUNCHER = "PalServer.sh"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def appmanifest():
    candidates = (
        INSTALL.parent / "steamapps" / f"appmanifest_{APP_ID}.acf",
        INSTALL / "steamapps" / f"appmanifest_{APP_ID}.acf",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ValueError("Steam appmanifest_2394010.acf is missing")


def build_id(manifest=None):
    manifest = manifest or appmanifest()
    match = re.search(r'"buildid"\s+"(\d+)"', manifest.read_text())
    if not match:
        raise ValueError("Steam appmanifest has no numeric buildid")
    return match.group(1)


def tree_inventory(root):
    files = 0
    size = 0
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            files += 1
            size += path.stat().st_size
    critical = {
        name: sha256(root / name) for name in CRITICAL if (root / name).is_file()
    }
    if REQUIRED_LAUNCHER not in critical:
        raise ValueError(f"snapshot has no {REQUIRED_LAUNCHER}")
    return {"files": files, "bytes": size, "critical_sha256": critical}


def snapshot_records():
    records = []
    if not ROOT.is_dir():
        return records
    for metadata in ROOT.glob("build-*/snapshot.json"):
        value = ops.read_json(metadata, {})
        if value.get("build_id") and (metadata.parent / "tree").is_dir():
            records.append({**value, "name": metadata.parent.name, "path": str(metadata.parent)})
    return sorted(records, key=lambda item: item.get("created_at", 0), reverse=True)


def effective_pin():
    configured = os.environ.get("PALWORLD_STEAM_BUILD_PIN", "").strip()
    if configured:
        if not configured.isdigit():
            raise ValueError("PALWORLD_STEAM_BUILD_PIN must be numeric")
        return {"build_id": configured, "source": "environment"}
    value = ops.read_json(PIN, {})
    return value if str(value.get("build_id", "")).isdigit() else {}


def create_snapshot():
    if subprocess.run(["systemctl", "is-active", "--quiet", "palworld.service"]).returncode == 0:
        raise ValueError("refusing executable snapshot while palworld.service is active")
    manifest = appmanifest()
    current = build_id(manifest)
    ROOT.mkdir(parents=True, exist_ok=True)
    stamp = f"{int(time.time())}-{time.time_ns() % 1_000_000_000:09d}"
    final = ROOT / f"build-{current}-{stamp}"
    staging = ROOT / f".staging-{os.getpid()}-{stamp}"
    staging.mkdir(mode=0o750)
    tree = staging / "tree"
    previous = snapshot_records()
    command = [
        "rsync", "-a", "--delete", "--exclude=/Pal/Saved/",
        "--exclude=/Pal/Saved/***",
    ]
    if previous:
        command.append(f"--link-dest={pathlib.Path(previous[0]['path']) / 'tree'}")
    command += [f"{INSTALL}/", f"{tree}/"]
    try:
        subprocess.run(command, check=True, timeout=7200)
        shutil.copy2(manifest, staging / "appmanifest.acf")
        inventory = tree_inventory(tree)
        metadata = {
            "build_id": current, "created_at": int(time.time()), "app_id": APP_ID,
            "source_manifest": str(manifest), "manifest_sha256": sha256(staging / "appmanifest.acf"),
            **inventory,
        }
        ops.atomic_json(staging / "snapshot.json", metadata)
        staging.rename(final)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    prune_snapshots()
    ops.audit("steam.snapshot", "ok", build_id=current, snapshot=final.name, files=inventory["files"], bytes=inventory["bytes"])
    return {**metadata, "name": final.name, "path": str(final)}


def verify_snapshot(record):
    root = pathlib.Path(record["path"])
    manifest = root / "appmanifest.acf"
    if not manifest.is_file() or sha256(manifest) != record.get("manifest_sha256"):
        raise ValueError("snapshot appmanifest checksum mismatch")
    for name, expected in record.get("critical_sha256", {}).items():
        candidate = root / "tree" / name
        if not candidate.is_file() or sha256(candidate) != expected:
            raise ValueError(f"snapshot critical-file mismatch: {name}")
    if build_id(manifest) != str(record["build_id"]):
        raise ValueError("snapshot build ID does not match its metadata")
    return True


def find_snapshot(target):
    if not re.fullmatch(r"\d+", str(target)):
        raise ValueError("target build must be numeric")
    match = next((item for item in snapshot_records() if item["build_id"] == str(target)), None)
    if not match:
        raise ValueError(f"no local executable snapshot exists for build {target}")
    verify_snapshot(match)
    return match


def restore_snapshot(target):
    if subprocess.run(["systemctl", "is-active", "--quiet", "palworld.service"]).returncode == 0:
        raise ValueError("refusing executable rollback while palworld.service is active")
    record = find_snapshot(target)
    subprocess.run([
        "rsync", "-a", "--checksum", "--delete", "--exclude=/Pal/Saved/", "--exclude=/Pal/Saved/***",
        f"{pathlib.Path(record['path']) / 'tree'}/", f"{INSTALL}/",
    ], check=True, timeout=7200)
    manifest = appmanifest()
    shutil.copy2(pathlib.Path(record["path"]) / "appmanifest.acf", manifest)
    if build_id(manifest) != str(target):
        raise ValueError("restored appmanifest build does not match target")
    verify_snapshot(record)
    ops.audit("steam.rollback-tree", "ok", build_id=str(target), snapshot=record["name"])
    return record


def prune_snapshots():
    keep = max(1, min(int(os.environ.get("PALWORLD_BUILD_SNAPSHOT_RETENTION", "2")), 10))
    pin = effective_pin().get("build_id")
    records = snapshot_records()
    retained = 0
    for record in records:
        if retained < keep or record["build_id"] == pin:
            retained += 1
            continue
        shutil.rmtree(record["path"])


def set_pin(target):
    available = {item["build_id"] for item in snapshot_records()}
    try:
        available.add(build_id())
    except ValueError:
        pass
    if str(target) not in available:
        raise ValueError("a build can only be pinned when installed or locally snapshotted")
    value = {"build_id": str(target), "source": "operator", "pinned_at": int(time.time())}
    ops.atomic_json(PIN, value)
    ops.audit("steam.pin", "ok", build_id=str(target))
    return value


def plan(target):
    record = find_snapshot(target)
    return {
        "target_build": str(target), "current_build": build_id(), "snapshot": record,
        "save_tree_excluded": True,
        "steps": [
            "refuse online players", "create and verify a protected save backup",
            "stop the world", "snapshot the current executable build",
            f"restore executable build {target} without touching Pal/Saved",
            "render managed settings", "start and verify REST/build health",
            "automatically restore the prior executable snapshot on failure",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list"); sub.add_parser("snapshot")
    plan_parser = sub.add_parser("plan"); plan_parser.add_argument("build")
    restore_parser = sub.add_parser("restore"); restore_parser.add_argument("build"); restore_parser.add_argument("--confirm", default="")
    pin_parser = sub.add_parser("pin"); pin_parser.add_argument("build")
    unpin_parser = sub.add_parser("unpin"); unpin_parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    try:
        if args.command == "list":
            result = {"current_build": build_id(), "pin": effective_pin(), "snapshots": snapshot_records()}
        elif args.command == "snapshot": result = create_snapshot()
        elif args.command == "plan": result = plan(args.build)
        elif args.command == "restore":
            if args.confirm != "ROLLBACK BUILD": raise ValueError("confirmation required")
            result = restore_snapshot(args.build)
        elif args.command == "pin": result = set_pin(args.build)
        elif args.command == "unpin":
            if args.confirm != "UNPIN BUILD": raise ValueError("confirmation required")
            PIN.unlink(missing_ok=True); ops.audit("steam.unpin", "ok"); result = {"ok": True}
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__":
    main()
