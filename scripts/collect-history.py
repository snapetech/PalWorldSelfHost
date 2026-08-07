#!/usr/bin/env python3
"""Collect host/world telemetry and derive private player presence events."""

import importlib.util
import json
import os
import pathlib
import sqlite3
import subprocess
import time


HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("ops_lib", HERE / "ops-lib.py")
ops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops)


def rest(endpoint):
    process = subprocess.run(
        [str(HERE / "rest-client.py"), endpoint], text=True, capture_output=True,
    )
    if process.returncode:
        return None
    try:
        return json.loads(process.stdout)
    except json.JSONDecodeError:
        return None


def player_identity(player):
    identifier = player.get("userId") or player.get("userid") or player.get("accountName")
    name = str(player.get("name") or "Unknown player")[:128]
    return str(identifier or f"name:{name}")[:256], name


def prepare_presence(database):
    database.execute(
        "create table if not exists player_presence("
        "user_key text primary key,name text not null,first_seen integer not null,last_seen integer not null,"
        "sessions integer not null,total_seconds integer not null,online integer not null,joined_at integer)"
    )
    database.execute(
        "create table if not exists player_events("
        "ts integer not null,kind text not null,user_key text not null,name text not null)"
    )


def track_players(database, players, now, emit=True):
    prepare_presence(database)
    previous = {
        row[0]: {"name": row[1], "joined_at": row[2]}
        for row in database.execute(
            "select user_key,name,joined_at from player_presence where online=1"
        )
    }
    current = dict(player_identity(player) for player in players)
    events = []
    for user_key, name in current.items():
        existing = database.execute(
            "select online from player_presence where user_key=?", (user_key,),
        ).fetchone()
        if not existing:
            database.execute(
                "insert into player_presence values(?,?,?,?,?,?,?,?)",
                (user_key, name, now, now, 1, 0, 1, now),
            )
            if emit:
                events.append(("join", user_key, name, None))
        elif not existing[0]:
            database.execute(
                "update player_presence set name=?,last_seen=?,sessions=sessions+1,online=1,joined_at=? where user_key=?",
                (name, now, now, user_key),
            )
            events.append(("join", user_key, name, None))
        else:
            database.execute(
                "update player_presence set name=?,last_seen=? where user_key=?", (name, now, user_key),
            )
    for user_key, prior in previous.items():
        if user_key in current:
            continue
        joined_at = prior["joined_at"] or now
        session_seconds = max(0, now - joined_at)
        database.execute(
            "update player_presence set last_seen=?,total_seconds=total_seconds+?,online=0,joined_at=null where user_key=?",
            (now, session_seconds, user_key),
        )
        events.append(("leave", user_key, prior["name"], session_seconds))
    for kind, user_key, name, session_seconds in events:
        database.execute(
            "insert into player_events values(?,?,?,?)", (now, kind, user_key, name),
        )
        details = {"player": name, "userid": user_key}
        if session_seconds is not None:
            details["session_seconds"] = session_seconds
        ops.audit(f"player.{kind}", "ok", **details)
        if os.environ.get("PALWORLD_NOTIFY_PLAYER_EVENTS", "false").lower() == "true":
            verb = "joined" if kind == "join" else "left"
            ops.notify(f"player-{kind}", f"{name} {verb} the Palworld server.")
    database.execute("delete from player_events where ts < ?", (now - 90 * 86400,))
    return events


def process_resources(pid):
    if not pid:
        return 0, 0
    try:
        fields = pathlib.Path(f"/proc/{pid}/stat").read_text().split()
        return int(fields[23]) * os.sysconf("SC_PAGE_SIZE"), int(fields[13]) + int(fields[14])
    except (FileNotFoundError, IndexError, ValueError):
        return 0, 0


def main():
    metrics = rest("metrics") or {}
    player_payload = rest("players")
    now = int(time.time())
    service = dict(
        line.split("=", 1) for line in subprocess.run(
            ["systemctl", "show", "palworld.service", "-p", "MainPID", "-p", "NRestarts"],
            text=True, capture_output=True,
        ).stdout.splitlines() if "=" in line
    )
    pid = int(service.get("MainPID", "0"))
    restarts = int(service.get("NRestarts", "0"))
    rss, cpu = process_resources(pid)
    database = sqlite3.connect(ops.STATE / "history.sqlite3")
    database.execute(
        "create table if not exists samples(ts integer primary key,players integer,fps real,frame_ms real,"
        "uptime integer,day integer,rss_bytes integer,cpu_ticks integer,restarts integer)"
    )
    prepare_presence(database)
    initialized = (ops.STATE / "player-presence-initialized.json").is_file()
    if player_payload is not None:
        players = player_payload.get("players", [])
        track_players(database, players, now, emit=initialized)
        if not initialized:
            ops.atomic_json(ops.STATE / "player-presence-initialized.json", {"initialized_at": now})
        player_count = len(players)
    else:
        player_count = database.execute(
            "select count(*) from player_presence where online=1"
        ).fetchone()[0]
    database.execute(
        "insert or replace into samples values(?,?,?,?,?,?,?,?,?)",
        (now, player_count, metrics.get("serverfps"), metrics.get("serverframetime"),
         metrics.get("uptime"), metrics.get("days"), rss, cpu, restarts),
    )
    database.execute("delete from samples where ts < ?", (now - 90 * 86400,))
    database.commit()
    rows = database.execute(
        "select ts,players from samples where ts >= ? order by ts", (now - 7 * 86400,),
    ).fetchall()
    database.close()
    ops.atomic_json(
        ops.STATE / "public-history.json",
        [{"timestamp": row[0], "players": row[1]} for row in rows],
    )


if __name__ == "__main__":
    main()
