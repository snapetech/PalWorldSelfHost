#!/usr/bin/env python3
"""Pinned, transactional PalDefender and UE4SS lifecycle for the Wine runtime."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile


HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("ops_lib", HERE / "ops-lib.py")
ops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops)

INSTALL = pathlib.Path(os.environ.get("PALWORLD_INSTALL_DIR", "/srv/palworld/server")).resolve()
STATE = pathlib.Path(os.environ.get("PALWORLD_STATE_DIR", "/var/lib/palworld")).resolve()
RUNTIME = os.environ.get("PALWORLD_RUNTIME", "native-linux")
WIN64 = INSTALL / "Pal" / "Binaries" / "Win64"
REGISTRY = STATE / "mod-lifecycle.json"
BACKUPS = STATE / "mod-lifecycle-backups"
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_FILES = 2048
COMPONENT = re.compile(r"(?:paldefender|ue4ss)")


# Release assets are immutable-by-contract here even when an upstream release
# tag is movable: execution verifies these exact reviewed bytes before writing.
ARTIFACTS = {
    "paldefender": {
        "version": "1.8.3",
        "source": "Ultimeit/PalDefender@v1.8.3",
        "url": "https://github.com/Ultimeit/PalDefender/releases/download/v1.8.3/PalDefender.zip",
        "sha256": "2ec395237018b18b91b6e25ed1338f9e203814a6a3fd5fec8408eba10bfb35c8",
        "managed": ("PalDefender.dll", "d3d9.dll"),
        "required": ("PalDefender.dll", "d3d9.dll"),
        "seed": (),
    },
    "ue4ss": {
        "version": "experimental-palworld-20260719",
        "source": "Okaetsu/RE-UE4SS@experimental-palworld (asset updated 2026-07-19)",
        "url": "https://github.com/Okaetsu/RE-UE4SS/releases/download/experimental-palworld/UE4SS-Palworld.zip",
        "sha256": "768a45718fbb9e429ac5cc3ce4a139a1b7b468bff31b4a136ae483d725aca1ca",
        "managed": (
            "dwmapi.dll", "ue4ss/UE4SS.dll", "ue4ss/MemberVariableLayout.ini", "ue4ss/LICENSE",
        ),
        "required": (
            "dwmapi.dll", "ue4ss/UE4SS.dll", "ue4ss/MemberVariableLayout.ini",
            "ue4ss/UE4SS-settings.ini", "ue4ss/Mods/mods.txt",
        ),
        # Seed defaults only when absent. Updates never overwrite operator
        # settings, Lua enable state, or bundled/user-added Lua mod trees.
        "seed": ("ue4ss/UE4SS-settings.ini", "ue4ss/Mods/"),
    },
}

def bundled_file(relative: str) -> pathlib.Path:
    candidates = (
        HERE.parent / relative,
        pathlib.Path("/usr/local/share/palworldselfhost") / relative,
        pathlib.Path("/usr/share/palworldselfhost") / relative,
    )
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])


BUNDLED = {
    "paldefender": {},
    "ue4ss": {
        "ue4ss/Mods/PalWorldSelfHostRelay/Scripts/main.lua": (
            bundled_file("deploy/wine-modded/PalWorldSelfHostRelay/Scripts/main.lua")
        ),
        "ue4ss/Mods/PalWorldSelfHostRelay/enabled.txt": (
            bundled_file("deploy/wine-modded/PalWorldSelfHostRelay/enabled.txt")
        ),
    },
}


def managed_paths(name: str) -> tuple[str, ...]:
    return tuple(ARTIFACTS[name]["managed"]) + tuple(BUNDLED[name])


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_registry() -> dict:
    value = ops.read_json(REGISTRY, {"schema": 1, "components": {}})
    if not isinstance(value, dict) or value.get("schema") != 1 or not isinstance(value.get("components"), dict):
        raise ValueError("mod lifecycle registry is malformed")
    return value


def write_registry(value: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    ops.atomic_json(REGISTRY, value)
    os.chmod(REGISTRY, 0o640)


def runtime_supported() -> tuple[bool, str | None]:
    if RUNTIME != "wine-windows":
        return False, "PalDefender and Windows UE4SS require PALWORLD_RUNTIME=wine-windows"
    if not (INSTALL / "PalServer.exe").is_file():
        return False, "the Windows Palworld depot is not installed (PalServer.exe is absent)"
    if not (WIN64 / "PalServer-Win64-Shipping-Cmd.exe").is_file():
        return False, "the Windows Palworld shipping executable is absent"
    return True, None


def server_active() -> bool:
    service = os.environ.get("PALWORLD_SERVICE", "palworld.service")
    try:
        if subprocess.run(
            ["systemctl", "is-active", "--quiet", service],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
        ).returncode == 0:
            return True
    except (OSError, subprocess.TimeoutExpired):
        pass
    # Conservative fallback also catches manually launched Wine validation.
    for entry in pathlib.Path("/proc").glob("[0-9]*/cmdline"):
        try:
            command = entry.read_bytes().replace(b"\0", b" ").decode(errors="ignore").casefold()
        except OSError:
            continue
        if "palserver.exe" in command:
            return True
    return False


def component_status(name: str, registry: dict) -> dict:
    artifact = ARTIFACTS[name]
    record = registry["components"].get(name)
    files = {}
    drift = []
    for relative in managed_paths(name):
        path = WIN64 / relative
        observed = sha256_file(path) if path.is_file() and not path.is_symlink() else None
        files[relative] = observed
        expected = record.get("files", {}).get(relative) if isinstance(record, dict) else None
        if expected and observed != expected:
            drift.append(relative)
    installed = all(files[relative] for relative in managed_paths(name))
    generated = None
    if name == "paldefender":
        generated = (WIN64 / "PalDefender/Config.json").is_file()
    elif name == "ue4ss":
        generated = (WIN64 / "ue4ss/UE4SS.log").is_file()
    return {
        "installed": installed,
        "managed": bool(record),
        "version": record.get("version") if isinstance(record, dict) else None,
        "current_version": artifact["version"],
        "source": artifact["source"],
        "artifact_sha256": artifact["sha256"],
        "drift": drift,
        "generated_runtime_state": generated,
    }


def status() -> dict:
    registry = read_registry()
    supported, reason = runtime_supported()
    return {
        "runtime": RUNTIME,
        "supported": supported,
        "reason": reason,
        "server_active": server_active(),
        "components": {name: component_status(name, registry) for name in ARTIFACTS},
    }


def canonical_hash(value: dict) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return sha256_bytes(encoded)


def plan(name: str, action: str) -> dict:
    if not COMPONENT.fullmatch(name):
        raise ValueError("component must be paldefender or ue4ss")
    if action not in {"install", "remove", "rollback"}:
        raise ValueError("action must be install, remove, or rollback")
    state = status()
    if not state["supported"]:
        raise ValueError(state["reason"])
    current = state["components"][name]
    if action == "remove" and not current["managed"]:
        raise ValueError(f"{name} is not managed by PalWorldSelfHost")
    if current["drift"]:
        raise ValueError(f"{name} managed files have drifted: {', '.join(current['drift'])}")
    if action == "rollback" and not latest_backup(name):
        raise ValueError(f"no preserved {name} lifecycle version is available")
    artifact = ARTIFACTS[name]
    result = {
        "component": name,
        "action": action,
        "runtime": RUNTIME,
        "server_must_be_stopped": True,
        "server_active": state["server_active"],
        "installed": current["installed"],
        "installed_version": current["version"],
        "target_version": artifact["version"] if action == "install" else None,
        "source": artifact["source"] if action == "install" else None,
        "artifact_sha256": artifact["sha256"] if action == "install" else None,
        "managed_files": list(managed_paths(name)),
        "preserves": ["Pal/Saved", "PalDefender generated configuration", "UE4SS settings and Lua mods"],
        "confirmation": f"{action.upper()} {name.upper()}",
    }
    result["plan_hash"] = canonical_hash(result)
    return result


def download(url: str) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        raise ValueError("artifact source must be an allowlisted GitHub HTTPS URL")
    request = urllib.request.Request(
        url, headers={"Accept": "application/octet-stream", "User-Agent": "PalWorldSelfHost-mod-lifecycle"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        final = urllib.parse.urlparse(response.geturl())
        if final.scheme != "https" or final.hostname not in {
            "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com",
        }:
            raise ValueError("artifact redirect left the allowlisted GitHub asset hosts")
        length = response.headers.get("Content-Length")
        if length and int(length) > MAX_ARCHIVE_BYTES:
            raise ValueError("artifact exceeds 64 MiB")
        value = response.read(MAX_ARCHIVE_BYTES + 1)
    if len(value) > MAX_ARCHIVE_BYTES:
        raise ValueError("artifact exceeds 64 MiB")
    return value


def safe_archive(value: bytes, artifact: dict) -> tuple[zipfile.ZipFile, dict[str, zipfile.ZipInfo]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(value))
    except zipfile.BadZipFile as exc:
        raise ValueError("artifact is not a valid ZIP archive") from exc
    if len(archive.infolist()) > MAX_ARCHIVE_FILES:
        archive.close()
        raise ValueError("artifact contains too many files")
    files = {}
    expanded = 0
    for info in archive.infolist():
        relative = pathlib.PurePosixPath(info.filename.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            archive.close()
            raise ValueError("artifact contains an unsafe path")
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            archive.close()
            raise ValueError("artifact contains a symbolic link")
        if info.is_dir():
            continue
        name = relative.as_posix()
        if name in files:
            archive.close()
            raise ValueError("artifact contains a duplicate path")
        expanded += info.file_size
        if expanded > MAX_EXPANDED_BYTES:
            archive.close()
            raise ValueError("artifact expands beyond 256 MiB")
        files[name] = info
    missing = sorted(set(artifact["required"]) - set(files))
    if missing:
        archive.close()
        raise ValueError(f"artifact is missing required files: {', '.join(missing)}")
    return archive, files


def atomic_binary(path: pathlib.Path, value: bytes, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def backup_component(name: str, registry: dict) -> pathlib.Path:
    stamp = f"{time.time_ns()}-{name}"
    directory = BACKUPS / stamp
    files = directory / "files"
    files.mkdir(parents=True, mode=0o750)
    existing = []
    absent = []
    for relative in managed_paths(name):
        source = WIN64 / relative
        if source.is_symlink():
            raise ValueError(f"refusing symlinked managed file {relative}")
        if source.is_file():
            target = files / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            existing.append(relative)
        else:
            absent.append(relative)
    metadata = {
        "schema": 1, "component": name, "created_at": int(time.time()),
        "existing": existing, "absent": absent,
        "registry_prior": registry["components"].get(name),
    }
    ops.atomic_json(directory / "backup.json", metadata)
    os.chmod(directory / "backup.json", 0o640)
    return directory


def restore_backup(directory: pathlib.Path, name: str, registry: dict) -> None:
    metadata = ops.read_json(directory / "backup.json", {})
    if metadata.get("schema") != 1 or metadata.get("component") != name:
        raise ValueError("mod lifecycle backup metadata is malformed")
    for relative in managed_paths(name):
        target = WIN64 / relative
        source = directory / "files" / relative
        if relative in metadata.get("existing", []):
            atomic_binary(target, source.read_bytes(), source.stat().st_mode & 0o777)
        elif relative in metadata.get("absent", []):
            target.unlink(missing_ok=True)
    prior = metadata.get("registry_prior")
    if prior is None:
        registry["components"].pop(name, None)
    else:
        registry["components"][name] = prior
    write_registry(registry)


def latest_backup(name: str) -> pathlib.Path | None:
    if not BACKUPS.is_dir():
        return None
    candidates = []
    for metadata in BACKUPS.glob(f"*-{name}/backup.json"):
        value = ops.read_json(metadata, {})
        if value.get("schema") == 1 and value.get("component") == name:
            candidates.append(metadata.parent)
    return max(candidates, key=lambda item: item.stat().st_mtime_ns, default=None)


def verify_review(name: str, action: str, expected_plan_hash: str, confirmation: str) -> dict:
    reviewed = plan(name, action)
    if not re.fullmatch(r"[0-9a-f]{64}", str(expected_plan_hash or "")):
        raise ValueError("reviewed plan hash is required")
    if reviewed["plan_hash"] != expected_plan_hash:
        raise ValueError("mod lifecycle plan changed after review")
    if confirmation != reviewed["confirmation"]:
        raise ValueError(f"exact confirmation {reviewed['confirmation']} is required")
    if server_active():
        raise ValueError("Palworld must be stopped before changing loaded mod files")
    return reviewed


def install_component(name: str, expected_plan_hash: str, confirmation: str) -> dict:
    reviewed = verify_review(name, "install", expected_plan_hash, confirmation)
    artifact = ARTIFACTS[name]
    value = download(artifact["url"])
    if sha256_bytes(value) != artifact["sha256"]:
        raise ValueError("downloaded artifact SHA-256 does not match the reviewed release")
    archive, files = safe_archive(value, artifact)
    with ops.operation_lock():
        if server_active():
            archive.close()
            raise ValueError("Palworld became active after review")
        registry = read_registry()
        backup = backup_component(name, registry)
        installed = {}
        seeded = []
        try:
            for relative in artifact["managed"]:
                content = archive.read(files[relative])
                atomic_binary(WIN64 / relative, content)
                installed[relative] = sha256_bytes(content)
            for relative, source in BUNDLED[name].items():
                if not source.is_file() or source.is_symlink():
                    raise ValueError(f"bundled adapter file is absent or unsafe: {relative}")
                content = source.read_bytes()
                atomic_binary(WIN64 / relative, content)
                installed[relative] = sha256_bytes(content)
            for seed in artifact["seed"]:
                if seed.endswith("/"):
                    for relative, info in files.items():
                        if relative.startswith(seed) and not (WIN64 / relative).exists():
                            atomic_binary(WIN64 / relative, archive.read(info))
                            seeded.append(relative)
                elif not (WIN64 / seed).exists():
                    atomic_binary(WIN64 / seed, archive.read(files[seed]))
                    seeded.append(seed)
            registry["components"][name] = {
                "version": artifact["version"], "source": artifact["source"],
                "artifact_sha256": artifact["sha256"], "installed_at": int(time.time()),
                "files": installed, "seeded": seeded, "backup": backup.name,
            }
            write_registry(registry)
            observed = component_status(name, registry)
            if not observed["installed"] or observed["drift"]:
                raise ValueError("post-install managed-file verification failed")
        except Exception:
            restore_backup(backup, name, registry)
            raise
        finally:
            archive.close()
    ops.audit("mod-lifecycle.install", "ok", component=name, version=artifact["version"], backup=backup.name)
    return {**reviewed, "backup": backup.name, "installed_files": installed, "seeded_files": seeded}


def remove_component(name: str, expected_plan_hash: str, confirmation: str) -> dict:
    reviewed = verify_review(name, "remove", expected_plan_hash, confirmation)
    with ops.operation_lock():
        if server_active():
            raise ValueError("Palworld became active after review")
        registry = read_registry()
        observed = component_status(name, registry)
        if observed["drift"]:
            raise ValueError(f"{name} managed files changed after review")
        backup = backup_component(name, registry)
        try:
            for relative in managed_paths(name):
                (WIN64 / relative).unlink(missing_ok=True)
            registry["components"].pop(name, None)
            write_registry(registry)
        except Exception:
            restore_backup(backup, name, registry)
            raise
    ops.audit("mod-lifecycle.remove", "ok", component=name, backup=backup.name)
    return {**reviewed, "backup": backup.name, "preserved_generated_data": True}


def rollback_component(name: str, expected_plan_hash: str, confirmation: str) -> dict:
    reviewed = verify_review(name, "rollback", expected_plan_hash, confirmation)
    directory = latest_backup(name)
    if directory is None:
        raise ValueError(f"no preserved {name} lifecycle version is available")
    with ops.operation_lock():
        if server_active():
            raise ValueError("Palworld became active after review")
        registry = read_registry()
        displaced = backup_component(name, registry)
        try:
            restore_backup(directory, name, registry)
        except Exception:
            restore_backup(displaced, name, registry)
            raise
    ops.audit("mod-lifecycle.rollback", "ok", component=name, restored=directory.name, backup=displaced.name)
    return {**reviewed, "restored": directory.name, "backup": displaced.name}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    item = sub.add_parser("plan")
    item.add_argument("component", choices=tuple(ARTIFACTS))
    item.add_argument("action", choices=("install", "remove", "rollback"))
    for command in ("install", "remove", "rollback"):
        item = sub.add_parser(command)
        item.add_argument("component", choices=tuple(ARTIFACTS))
        item.add_argument("--expected-plan-hash", required=True)
        item.add_argument("--confirm", default="")
    args = parser.parse_args()
    try:
        if args.command == "status":
            result = status()
        elif args.command == "plan":
            result = plan(args.component, args.action)
        elif args.command == "install":
            result = install_component(args.component, args.expected_plan_hash, args.confirm)
        elif args.command == "remove":
            result = remove_component(args.component, args.expected_plan_hash, args.confirm)
        else:
            result = rollback_component(args.component, args.expected_plan_hash, args.confirm)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (ValueError, OSError, UnicodeError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)[:1000]}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
