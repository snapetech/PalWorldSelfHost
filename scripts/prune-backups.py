#!/usr/bin/env python3
import os, pathlib, time

root = pathlib.Path(os.environ["PALWORLD_BACKUP_LOCAL_ROOT"]); now = time.time()
retention = {"hourly": int(os.environ.get("PALWORLD_BACKUP_HOURLY_RETENTION_HOURS", "48")) / 24,
             "daily": int(os.environ.get("PALWORLD_BACKUP_DAILY_RETENTION_DAYS", "14")),
             "weekly": int(os.environ.get("PALWORLD_BACKUP_WEEKLY_RETENTION_DAYS", "90"))}
archives = sorted(root.glob("palworld-*.tar.zst"), key=lambda p: p.stat().st_mtime, reverse=True)
valid = [p for p in archives if p.with_suffix(p.suffix + ".sha256").exists()]
for archive in archives:
    tier = next((key for key in retention if f"-{key}-" in archive.name), "daily")
    if tier == "protected" or archive == (valid[0] if valid else None): continue
    if (now - archive.stat().st_mtime) / 86400 <= retention[tier]: continue
    for path in (archive, pathlib.Path(str(archive) + ".sha256"), pathlib.Path(str(archive) + ".manifest.json")):
        path.unlink(missing_ok=True)
