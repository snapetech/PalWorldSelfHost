#!/usr/bin/env python3
"""Atomically publish sanitized status JSON into the static site directory."""
import json, os, pathlib, tempfile, urllib.request

target = pathlib.Path(os.environ.get("PALWORLD_PUBLIC_DIR", "/var/www/palworld")) / "status.json"
url = f"http://127.0.0.1:{os.environ.get('PALWORLD_OPS_PORT', '8213')}/api/public-status"
with urllib.request.urlopen(url, timeout=15) as response: data = json.load(response)
target.parent.mkdir(parents=True, exist_ok=True)
fd, temporary = tempfile.mkstemp(prefix=".status-", suffix=".json", dir=target.parent)
try:
    with os.fdopen(fd, "w") as handle: json.dump(data, handle, separators=(",", ":")); handle.write("\n")
    os.chmod(temporary, 0o644); os.replace(temporary, target)
finally:
    try: os.unlink(temporary)
    except FileNotFoundError: pass
