#!/usr/bin/env python3
"""Plan or execute a guarded single-world restore."""
import argparse, importlib.util, json, os, pathlib, shutil, subprocess, tarfile, tempfile, time

here = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ops_lib", here / "ops-lib.py")
ops = importlib.util.module_from_spec(spec); spec.loader.exec_module(ops)
parser = argparse.ArgumentParser(); parser.add_argument("archive", type=pathlib.Path)
parser.add_argument("--execute", action="store_true"); parser.add_argument("--confirm", default="")
args = parser.parse_args(); archive = args.archive.resolve(); install = pathlib.Path(os.environ["PALWORLD_INSTALL_DIR"])
checksum = pathlib.Path(str(archive) + ".sha256"); manifest_file = pathlib.Path(str(archive) + ".manifest.json")
if not archive.is_file() or not checksum.is_file(): raise SystemExit("archive or checksum sidecar is missing")
subprocess.run([str(here / "verify-backup.sh"), str(archive)], check=True)
manifest = ops.read_json(manifest_file, {})
plan = {"archive": str(archive), "bytes": archive.stat().st_size, "manifest": manifest,
        "server_active": subprocess.run(["systemctl", "is-active", "--quiet", "palworld.service"]).returncode == 0,
        "steps": ["protected pre-restore backup", "stop server", "extract into staging",
                  "replace Pal/Saved", "restore ownership", "start and verify REST/world GUID", "rollback on failure"]}
if not args.execute:
    print(json.dumps(plan, indent=2)); raise SystemExit(0)
if args.confirm != "RESTORE WORLD": raise SystemExit("execution requires --confirm 'RESTORE WORLD'")
with ops.operation_lock():
    players = subprocess.run([str(here / "rest-client.py"), "players"], text=True, capture_output=True)
    if players.returncode == 0 and json.loads(players.stdout).get("players"): raise SystemExit("refusing restore while players are online")
    child_env = dict(os.environ, PALWORLD_LOCK_HELD="true")
    pre = subprocess.run([str(here / "backup.sh"), "protected"], text=True, capture_output=True, env=child_env)
    if pre.returncode: raise SystemExit("pre-restore backup failed: " + pre.stderr[-1000:])
    rollback = pathlib.Path(pre.stdout.strip().splitlines()[-1])
    old = None
    ops.audit("restore", "started", archive=archive.name, rollback=rollback.name)
    subprocess.run(["systemctl", "stop", "palworld.service"], check=True)
    try:
        with tempfile.TemporaryDirectory(dir=os.environ["PALWORLD_BACKUP_LOCAL_ROOT"]) as temporary:
            subprocess.run(["tar", "--zstd", "-xf", str(archive), "-C", temporary], check=True)
            staged = pathlib.Path(temporary) / "Pal/Saved"
            if not any(staged.glob("SaveGames/0/*/Level.sav")): raise RuntimeError("staged backup has no Level.sav")
            destination = install / "Pal/Saved"
            old = install / f"Pal/Saved.pre-restore-{int(time.time())}"
            destination.rename(old); shutil.move(str(staged), destination)
        subprocess.run(["chown", "-R", "palworld:palworld", str(install / "Pal/Saved")], check=True)
        subprocess.run(["systemctl", "start", "palworld.service"], check=True)
        for _ in range(30):
            health = subprocess.run([str(here / "rest-client.py"), "info"], text=True, capture_output=True)
            if health.returncode == 0: break
            time.sleep(2)
        else: raise RuntimeError("REST health did not recover")
        actual = json.loads(health.stdout).get("worldguid")
        expected = manifest.get("world_guid")
        if expected and actual != expected: raise RuntimeError(f"world GUID mismatch: {actual} != {expected}")
        if old and old.exists(): shutil.rmtree(old)
        ops.audit("restore", "ok", archive=archive.name)
    except Exception as exc:
        rollback_error = None
        try:
            subprocess.run(["systemctl", "stop", "palworld.service"], check=False)
            destination = install / "Pal/Saved"
            if old and old.exists():
                if destination.exists(): shutil.rmtree(destination)
                old.rename(destination)
                subprocess.run(["chown", "-R", f"{os.environ.get('PALWORLD_USER', 'palworld')}:{os.environ.get('PALWORLD_GROUP', 'palworld')}", str(destination)], check=True)
                subprocess.run(["systemctl", "start", "palworld.service"], check=True)
            else: rollback_error = "pre-restore tree was unavailable; use protected archive"
        except Exception as rollback_exc: rollback_error = str(rollback_exc)
        ops.audit("restore", "failed", archive=archive.name, error=str(exc), rollback=rollback.name, rollback_error=rollback_error)
        detail = f"automatic rollback failed ({rollback_error}); protected archive is {rollback}" if rollback_error else "automatic rollback completed"
        raise SystemExit(f"restore failed: {exc}; {detail}")
