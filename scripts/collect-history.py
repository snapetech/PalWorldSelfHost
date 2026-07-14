#!/usr/bin/env python3
import importlib.util, json, os, pathlib, resource, sqlite3, subprocess, time

here = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ops_lib", here / "ops-lib.py")
ops = importlib.util.module_from_spec(spec); spec.loader.exec_module(ops)
def rest(endpoint):
    p = subprocess.run([str(here / "rest-client.py"), endpoint], text=True, capture_output=True)
    return json.loads(p.stdout) if p.returncode == 0 else {}
metrics = rest("metrics"); players = rest("players").get("players", []); now = int(time.time())
service = dict(line.split("=", 1) for line in subprocess.run(["systemctl", "show", "palworld.service", "-p", "MainPID", "-p", "NRestarts"], text=True, capture_output=True).stdout.splitlines() if "=" in line)
pid = int(service.get("MainPID", "0")); restarts = int(service.get("NRestarts", "0"))
rss = cpu = 0
if pid:
    try:
        fields = pathlib.Path(f"/proc/{pid}/stat").read_text().split(); cpu = int(fields[13]) + int(fields[14])
        rss = int(fields[23]) * os.sysconf("SC_PAGE_SIZE")
    except (FileNotFoundError, IndexError): pass
db = sqlite3.connect(ops.STATE / "history.sqlite3")
db.execute("create table if not exists samples(ts integer primary key, players integer, fps real, frame_ms real, uptime integer, day integer, rss_bytes integer, cpu_ticks integer, restarts integer)")
db.execute("insert or replace into samples values(?,?,?,?,?,?,?,?,?)", (now, len(players), metrics.get("serverfps"), metrics.get("serverframetime"), metrics.get("uptime"), metrics.get("days"), rss, cpu, restarts))
db.execute("delete from samples where ts < ?", (now - 90 * 86400,)); db.commit()
rows = db.execute("select ts,players from samples where ts >= ? order by ts", (now - 7 * 86400,)).fetchall(); db.close()
ops.atomic_json(ops.STATE / "public-history.json", [{"timestamp": row[0], "players": row[1]} for row in rows])
