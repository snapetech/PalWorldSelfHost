#!/usr/bin/env python3
import hashlib, json, os, pathlib, re, subprocess, sys, time

archive = pathlib.Path(sys.argv[1]); tier = sys.argv[2]
install = pathlib.Path(os.environ["PALWORLD_INSTALL_DIR"])
manifest_path = install / "steamapps/appmanifest_2394010.acf"
text = manifest_path.read_text(errors="replace") if manifest_path.exists() else ""
match = re.search(r'"buildid"\s+"(\d+)"', text)
info = {}
try:
    info = json.loads(subprocess.run(["/usr/local/lib/palworld/rest-client.py", "info"], text=True,
                      capture_output=True, timeout=10).stdout or "{}")
except Exception: pass
digest = hashlib.sha256()
with archive.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""): digest.update(chunk)
print(json.dumps({"schema": 1, "created_at": int(time.time()), "tier": tier,
      "archive": archive.name, "bytes": archive.stat().st_size, "sha256": digest.hexdigest(),
      "steam_build_id": match.group(1) if match else None, "server_version": info.get("version"),
      "world_guid": info.get("worldguid"), "server_name": info.get("servername"),
      "player_count": int(os.environ.get("PALWORLD_BACKUP_PLAYER_COUNT", "0")),
      "selfhost_commit": os.environ.get("PALWORLD_SELFHOST_VERSION", "unknown"),
      "verified": True}, indent=2, sort_keys=True))
