#!/usr/bin/env python3
import importlib.util, os, pathlib, subprocess, tempfile, time

here = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ops_lib", here / "ops-lib.py")
ops = importlib.util.module_from_spec(spec); spec.loader.exec_module(ops)
root = pathlib.Path(os.environ["PALWORLD_BACKUP_LOCAL_ROOT"])
archives = sorted(root.glob("palworld-*.tar.zst"), key=lambda p: p.stat().st_mtime, reverse=True)
if not archives: raise SystemExit("no backup available for restore drill")
archive = archives[0]; started = time.time()
try:
    subprocess.run([str(here / "verify-backup.sh"), str(archive)], check=True)
    with tempfile.TemporaryDirectory(dir=root) as temporary:
        subprocess.run(["tar", "--zstd", "-xf", str(archive), "-C", temporary], check=True)
        level = list(pathlib.Path(temporary).glob("Pal/Saved/SaveGames/0/*/Level.sav"))
        settings = pathlib.Path(temporary) / "Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"
        if not level or not level[0].stat().st_size or not settings.is_file():
            raise RuntimeError("required restored world/config files are missing")
    ops.audit("restore.drill", "ok", archive=archive.name, seconds=round(time.time() - started, 2))
except Exception as exc:
    ops.audit("restore.drill", "failed", archive=archive.name, error=str(exc))
    ops.notify("restore-drill-failed", f"Restore drill failed for {archive.name}: {exc}", "error")
    raise
