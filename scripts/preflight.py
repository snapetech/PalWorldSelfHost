#!/usr/bin/env python3
import argparse, json, os, pathlib, shutil, socket, subprocess

parser = argparse.ArgumentParser(); parser.add_argument("--json", action="store_true"); args = parser.parse_args()
checks = []
def add(name, ok, detail): checks.append({"name": name, "ok": bool(ok), "detail": str(detail)})
for binary in ("systemctl", "steamcmd", "rclone", "zstd", "tar", "flock", "curl"):
    add(f"binary:{binary}", shutil.which(binary), shutil.which(binary) or "missing")
memory = int(pathlib.Path("/proc/meminfo").read_text().split("MemTotal:", 1)[1].split()[0]) * 1024
add("memory", memory >= 8 * 1024**3, f"{memory / 1024**3:.1f} GiB (16+ recommended)")
port = int(os.environ.get("PALWORLD_PORT", "8211")); sockets = subprocess.run(["ss", "-lun"], text=True, capture_output=True).stdout
add("game-port", f":{port} " not in sockets or subprocess.run(["systemctl", "is-active", "--quiet", "palworld.service"]).returncode == 0, f"UDP {port}")
root = pathlib.Path(os.environ.get("PALWORLD_BACKUP_LOCAL_ROOT", "/var/backups/palworld")); add("backup-parent", root.parent.exists(), root.parent)
result = {"ok": all(item["ok"] for item in checks), "host": socket.gethostname(), "checks": checks}
print(json.dumps(result, indent=2) if args.json else "\n".join(f"{'OK' if c['ok'] else 'FAIL'} {c['name']}: {c['detail']}" for c in checks))
raise SystemExit(not result["ok"])
