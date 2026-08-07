#!/usr/bin/env python3
"""Plan and execute a protected dedicated/co-op Palworld world import."""

import argparse
import fcntl
import grp
import hashlib
import json
import os
import pathlib
import pwd
import re
import shutil
import subprocess
import tempfile
import time


MAX_FILES = 100_000
MAX_BYTES = 128 * 1024**3
WORLD_RE = re.compile(r"[0-9A-Fa-f]{32}")
ROOT_SAVE_FILES = {"Level.sav", "LevelMeta.sav", "LocalData.sav", "WorldOption.sav", "WorldOptions.sav"}
PLAYER_SAVE_RE = re.compile(r"Players/[0-9A-Fa-f]{32}\.sav")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def within(path, root):
    try: path.relative_to(root); return True
    except ValueError: return False


def inventory(source):
    source = pathlib.Path(source)
    if not source.is_absolute() or not source.is_dir() or source.is_symlink():
        raise ValueError("source must be an absolute, non-symlinked world directory")
    source = source.resolve()
    if not (source / "Level.sav").is_file() or (source / "Level.sav").is_symlink():
        raise ValueError("source world has no regular Level.sav")
    files = []
    total = 0
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError("source world may not contain symlinks")
        if path.is_dir():
            if path.relative_to(source).as_posix() != "Players":
                raise ValueError("source contains an unexpected directory; select the world folder itself")
            continue
        if not path.is_file():
            raise ValueError("source world may contain only directories and regular files")
        relative = path.relative_to(source).as_posix()
        if relative not in ROOT_SAVE_FILES and not PLAYER_SAVE_RE.fullmatch(relative):
            raise ValueError(f"source contains an unexpected world file: {relative}")
        if len(relative) > 500:
            raise ValueError("source contains a path longer than 500 characters")
        total += path.stat().st_size
        files.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256(path)})
        if len(files) > MAX_FILES or total > MAX_BYTES:
            raise ValueError("source exceeds the 100,000-file or 128-GiB import bound")
    return source, files, total


def existing_worlds(install):
    root = install / "Pal/Saved/SaveGames/0"
    return sorted(path.parent.name for path in root.glob("*/Level.sav") if path.is_file() and not path.is_symlink())


def import_plan(source, install, state, target_world=None):
    install = pathlib.Path(install).resolve()
    state = pathlib.Path(state).resolve(strict=False)
    if within(state, install) or within(install, state):
        raise ValueError("managed install and state directories may not overlap")
    source, files, total = inventory(source)
    if within(source, install) or within(source, state):
        raise ValueError("source must be outside the managed install and state trees")
    worlds = existing_worlds(install)
    if target_world:
        if not WORLD_RE.fullmatch(target_world):
            raise ValueError("target world must be a 32-character hexadecimal identifier")
        target = target_world.upper()
    elif len(worlds) == 1:
        target = worlds[0]
    elif not worlds and WORLD_RE.fullmatch(source.name):
        target = source.name.upper()
    else:
        raise ValueError("specify --target-world when the managed tree has zero or multiple selectable worlds")
    binding = {"source": str(source), "target_world": target, "files": files}
    fingerprint = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "status": "ready",
        "source": str(source),
        "source_kind": "co-op" if any(row["path"] == "LocalData.sav" for row in files) else "dedicated-or-exported",
        "target_world": target,
        "target_exists": target in worlds,
        "existing_worlds": worlds,
        "files": len(files),
        "bytes": total,
        "players": sum(1 for row in files if row["path"].startswith("Players/") and row["path"].endswith(".sav")),
        "fingerprint": fingerprint,
        "steps": [
            "revalidate the exact source fingerprint and require zero online players",
            "save and create a verified protected backup of the current managed tree",
            "stop the server and stage a private copy without changing the source",
            "preserve any replaced target and select the imported world in GameUserSettings.ini",
            "start and verify REST health; automatically restore the prior world selection on failure",
        ],
    }


def run_checked(argv, *, runner=subprocess.run, timeout=1800, allow_failure=False, env=None):
    process = runner(argv, text=True, capture_output=True, timeout=timeout, env=env or os.environ.copy())
    if process.returncode and not allow_failure:
        detail = (process.stdout + process.stderr).strip()[-2000:]
        raise ValueError(f"command failed: {' '.join(map(str, argv))}: {detail}")
    return process


def select_world(settings, world):
    prior = settings.read_text() if settings.exists() else "[/Script/Pal.PalGameLocalSettings]\n"
    pattern = re.compile(r"(?m)^DedicatedServerName=.*$")
    if pattern.search(prior):
        updated = pattern.sub(f"DedicatedServerName={world}", prior, count=1)
    else:
        marker = "[/Script/Pal.PalGameLocalSettings]"
        if marker in prior:
            updated = prior.replace(marker, marker + f"\nDedicatedServerName={world}", 1)
        else:
            updated = marker + f"\nDedicatedServerName={world}\n" + prior
    settings.parent.mkdir(parents=True, exist_ok=True)
    temporary = settings.with_suffix(settings.suffix + ".import")
    temporary.write_text(updated)
    os.replace(temporary, settings)


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, path)
    finally:
        try: os.unlink(temporary)
        except FileNotFoundError: pass


def chown_tree(path, user, group):
    uid = pwd.getpwnam(user).pw_uid
    gid = grp.getgrnam(group).gr_gid
    for root, directories, files in os.walk(path):
        os.chown(root, uid, gid)
        for name in directories + files:
            os.chown(pathlib.Path(root) / name, uid, gid, follow_symlinks=False)


def rest_players(here, *, runner):
    process = run_checked([str(here / "rest-client.py"), "players"], runner=runner, timeout=20)
    try: return len(json.loads(process.stdout).get("players", []))
    except json.JSONDecodeError as exc: raise ValueError("player roster was not valid JSON") from exc


def wait_ready(here, *, runner, attempts=24):
    for _ in range(attempts):
        process = run_checked([str(here / "rest-client.py"), "info"], runner=runner, timeout=15, allow_failure=True)
        if process.returncode == 0:
            return True
        time.sleep(5)
    return False


def execute(source, install, state, target_world, fingerprint, confirm, *, runner=subprocess.run, require_root=True):
    if confirm != "IMPORT WORLD":
        raise ValueError("confirmation required")
    if require_root and os.geteuid() != 0:
        raise ValueError("world import must run as root")
    install = pathlib.Path(install).resolve(); state = pathlib.Path(state).resolve(strict=False)
    state.mkdir(parents=True, exist_ok=True)
    here = pathlib.Path(__file__).resolve().parent
    with (state / "mutation.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        plan = import_plan(source, install, state, target_world)
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint or "") or plan["fingerprint"] != fingerprint:
            raise ValueError("source fingerprint or target selection does not match the reviewed plan")
        active = run_checked(["systemctl", "is-active", "--quiet", "palworld.service"], runner=runner, allow_failure=True).returncode == 0
        if active and rest_players(here, runner=runner):
            raise ValueError("online players must be zero before world import")
        env = {**os.environ, "PALWORLD_LOCK_HELD": "true"}
        if existing_worlds(install):
            run_checked([str(here / "backup.sh"), "protected"], runner=runner, env=env)
        if active:
            run_checked([str(here / "rest-client.py"), "save"], runner=runner, timeout=30)
            run_checked(["systemctl", "stop", "palworld.service"], runner=runner, timeout=180)

        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        save_root = install / "Pal/Saved/SaveGames/0"; save_root.mkdir(parents=True, exist_ok=True)
        target = save_root / plan["target_world"]
        staging = save_root / f".import-{plan['target_world']}-{os.getpid()}"
        preserved = save_root / f".palworldselfhost-preserved-{plan['target_world']}-{stamp}-{os.getpid()}"
        platform = "WindowsServer" if os.environ.get("PALWORLD_RUNTIME") == "wine-windows" else "LinuxServer"
        settings = install / "Pal/Saved/Config" / platform / "GameUserSettings.ini"
        prior_settings = settings.read_bytes() if settings.exists() else None
        moved_prior = False
        installed_new = False
        try:
            shutil.copytree(plan["source"], staging, symlinks=False)
            source_again, files_again, _ = inventory(staging)
            staged_binding = {"source": plan["source"], "target_world": plan["target_world"], "files": files_again}
            staged_fingerprint = hashlib.sha256(json.dumps(staged_binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if staged_fingerprint != fingerprint:
                raise ValueError("staged import copy does not match the reviewed source")
            if target.exists():
                os.replace(target, preserved); moved_prior = True
            os.replace(staging, target)
            installed_new = True
            select_world(settings, plan["target_world"])
            chown_tree(target, os.environ.get("PALWORLD_USER", "palworld"), os.environ.get("PALWORLD_GROUP", "palworld"))
            run_checked(["systemctl", "start", "palworld.service"], runner=runner, timeout=180)
            if not wait_ready(here, runner=runner):
                raise ValueError("imported world did not pass REST startup verification")
        except Exception:
            run_checked(["systemctl", "stop", "palworld.service"], runner=runner, timeout=180, allow_failure=True)
            shutil.rmtree(staging, ignore_errors=True)
            if installed_new and target.exists(): shutil.rmtree(target)
            if moved_prior: os.replace(preserved, target)
            if prior_settings is None: settings.unlink(missing_ok=True)
            else: settings.write_bytes(prior_settings)
            if active: run_checked(["systemctl", "start", "palworld.service"], runner=runner, timeout=180, allow_failure=True)
            run_checked([str(here / "ops-event.py"), "audit", "save-import", "failed", "--details", plan["target_world"]], runner=runner, allow_failure=True)
            raise
        preserved_result = None
        if moved_prior:
            final_preserved = state / "import-preserved" / stamp / plan["target_world"]
            final_preserved.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(preserved), str(final_preserved))
                preserved_result = str(final_preserved)
            except OSError:
                preserved_result = str(preserved)
        result = {**plan, "status": "imported", "imported_at": stamp, "preserved_target": preserved_result}
        atomic_json(state / "save-import-last.json", result)
        run_checked([str(here / "ops-event.py"), "audit", "save-import", "ok", "--details", plan["target_world"]], runner=runner, allow_failure=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "import"):
        item = sub.add_parser(name); item.add_argument("source", type=pathlib.Path)
        item.add_argument("--install-dir", default=os.environ.get("PALWORLD_INSTALL_DIR"), required=not os.environ.get("PALWORLD_INSTALL_DIR"))
        item.add_argument("--state-dir", default=os.environ.get("PALWORLD_STATE_DIR", "/var/lib/palworld"))
        item.add_argument("--target-world")
        if name == "import":
            item.add_argument("--fingerprint", required=True); item.add_argument("--confirm", default="")
    args = parser.parse_args()
    try:
        if args.command == "plan": result = import_plan(args.source, args.install_dir, args.state_dir, args.target_world)
        else: result = execute(args.source, args.install_dir, args.state_dir, args.target_world, args.fingerprint, args.confirm)
        print(json.dumps(result, indent=2))
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__": main()
