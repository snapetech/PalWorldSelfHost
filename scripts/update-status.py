#!/usr/bin/env python3
import json, os, re, subprocess
install = os.environ["PALWORLD_INSTALL_DIR"]
manifest = os.path.join(os.path.dirname(install), "steamapps", "appmanifest_2394010.acf")
if not os.path.exists(manifest):
    manifest = os.path.join(install, "steamapps", "appmanifest_2394010.acf")
local = None
if os.path.exists(manifest):
    match = re.search(r'"buildid"\s+"(\d+)"', open(manifest).read())
    local = match.group(1) if match else None
cmd = [os.environ.get("PALWORLD_STEAMCMD", "steamcmd"), "+login", "anonymous", "+app_info_update", "1", "+app_info_print", "2394010", "+quit"]
output = subprocess.run(cmd, text=True, capture_output=True, timeout=120).stdout
matches = re.findall(r'"buildid"\s+"(\d+)"', output)
remote = matches[-1] if matches else None
print(json.dumps({"local_build": local, "remote_build": remote, "update_available": bool(local and remote and local != remote)}))
