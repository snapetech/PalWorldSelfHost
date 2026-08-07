#!/usr/bin/env python3
"""Persisted, audited Palworld RCON console and saved-command catalog."""

import argparse
import contextlib
import importlib.util
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import time


HERE = pathlib.Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ops = load("ops_lib", "ops-lib.py")
rcon = load("rcon_client", "rcon-client.py")
DATABASE = ops.STATE / "rcon.sqlite3"
MAX_HISTORY = 1000
CATALOG = [
    {"command": "Info", "arguments": "", "description": "Show server information", "risk": "read", "source": "palworld"},
    {"command": "ShowPlayers", "arguments": "", "description": "List connected players", "risk": "read", "source": "palworld"},
    {"command": "Save", "arguments": "", "description": "Flush world data to disk", "risk": "write", "source": "palworld"},
    {"command": "Broadcast", "arguments": "<message>", "description": "Announce to every player", "risk": "write", "source": "palworld"},
    {"command": "KickPlayer", "arguments": "<user ID>", "description": "Disconnect one player", "risk": "moderation", "source": "palworld"},
    {"command": "BanPlayer", "arguments": "<user ID>", "description": "Ban one player", "risk": "critical", "source": "palworld"},
    {"command": "UnBanPlayer", "arguments": "<user ID>", "description": "Remove one player ban", "risk": "moderation", "source": "palworld"},
    {"command": "Shutdown", "arguments": "<seconds> <message>", "description": "Save and stop after a countdown", "risk": "critical", "source": "palworld"},
    {"command": "DoExit", "arguments": "", "description": "Force-stop the game process", "risk": "critical", "source": "palworld"},
    {"command": "TeleportToPlayer", "arguments": "<user ID>", "description": "Teleport the invoking player", "risk": "critical", "source": "palworld"},
    {"command": "TeleportToMe", "arguments": "<user ID>", "description": "Teleport a player to the invoker", "risk": "critical", "source": "palworld"},
]

PALDEFENDER_COMMANDS = {
    "getrconcmds": ("", "Discover commands accepted by this server", "read"),
    "version": ("", "Show game and PalDefender versions", "read"),
    "reloadcfg": ("", "Reload PalDefender configuration and lists", "write"),
    "pgbroadcast": ("<message>", "Broadcast a message with spaces", "write"),
    "alert": ("<message>", "Show an emphasized player alert", "write"),
    "getpos": ("[user ID]", "Show a player position", "read"),
    "getip": ("<user ID>", "Show a player network address", "moderation"),
    "kick": ("<user ID> [reason]", "Disconnect a player", "moderation"),
    "ban": ("<user ID> [reason]", "Ban a player identity", "critical"),
    "ipban": ("<user ID> [reason]", "Ban a player's network address", "critical"),
    "unban": ("<user ID> [reason]", "Remove a player ban", "moderation"),
    "banip": ("<IP>", "Ban a network address", "critical"),
    "unbanip": ("<IP>", "Remove a network-address ban", "moderation"),
    "whitelist_add": ("<user ID>", "Add a player to PalDefender whitelist", "moderation"),
    "whitelist_remove": ("<user ID>", "Remove a player from PalDefender whitelist", "moderation"),
    "whitelist_get": ("", "List PalDefender whitelist", "read"),
    "setadmin": ("<user ID>", "Change player administrator state", "critical"),
    "settime": ("<0-23|day|night>", "Change world time", "write"),
    "tp": ("<user ID> <user ID|X Y [Z]>", "Teleport a named player", "critical"),
    "give_exp": ("<user ID> <amount>", "Give player experience", "critical"),
    "givestats": ("<user ID> [amount]", "Change player status points", "critical"),
    "give": ("<user ID> <item ID> [amount]", "Give a player an item", "critical"),
    "giveitems": ("<user ID> <item:amount>...", "Give multiple player items", "critical"),
    "delitem": ("<user ID> <item ID> [amount]", "Remove a player item", "critical"),
    "delitems": ("<user ID> <item:amount>...", "Remove multiple player items", "critical"),
    "clearinv": ("<user ID> [container]", "Clear player inventory containers", "critical"),
    "give_relic": ("<user ID> <type> [amount]", "Give player relic progress", "critical"),
    "givepal": ("<user ID> <Pal ID> [level]", "Give a player a Pal", "critical"),
    "givepal_j": ("<user ID> <template>", "Give a template-defined Pal", "critical"),
    "spawnpal": ("<Pal ID> <X Y Z> [level]", "Spawn a Pal at coordinates", "critical"),
    "spawnpal_j": ("<template> <X Y Z>", "Spawn a template-defined Pal", "critical"),
    "giveegg": ("<user ID> <egg ID> <Pal ID> [level]", "Give a player a Pal egg", "critical"),
    "exportpals": ("[user ID]", "Export player Pals", "write"),
    "deletepals": ("<user ID> <filter>", "Delete matching player Pals", "critical"),
    "learntech": ("<user ID> <technology ID>", "Unlock player technology", "critical"),
    "unlearntech": ("<user ID> <technology ID>", "Lock player technology", "critical"),
    "givetechpoints": ("<user ID> [amount]", "Give player technology points", "critical"),
    "givebosstechpoints": ("<user ID> [amount]", "Give player ancient technology points", "critical"),
    "gettechids": ("", "List technology identifiers", "read"),
    "getskinids": ("", "List Pal skin identifiers", "read"),
    "getnearestbase": ("<X Y Z>", "Identify nearest base owner", "read"),
    "killnearestbase": ("<X Y Z>", "Destroy the nearest base", "critical"),
    "setguildleader": ("<user ID>", "Change guild leader", "critical"),
    "exportguilds": ("", "Export guild data", "write"),
}


@contextlib.contextmanager
def connection():
    ops.STATE.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(DATABASE, timeout=10)
    database.execute("pragma journal_mode=wal")
    database.execute("pragma busy_timeout=10000")
    database.executescript("""
        create table if not exists console_history(
          id integer primary key autoincrement, ts integer not null, actor text not null,
          source text not null, command text not null, output text not null,
          ok integer not null, duration_ms integer not null
        );
        create table if not exists saved_commands(
          id integer primary key autoincrement, name text not null unique,
          command text not null, created_at integer not null
        );
    """)
    database.commit()
    try:
        os.chmod(DATABASE, 0o640)
    except OSError:
        pass
    try:
        yield database
        database.commit()
    except Exception:
        database.rollback()
        raise
    finally:
        database.close()


def boolean_env(name, default=False):
    return os.environ.get(name, str(default)).lower() == "true"


def command_verb(command):
    return rcon.validate_command(command).split(None, 1)[0]


def execute(command, actor="operator", source="console", confirm=""):
    command = rcon.validate_command(command)
    if confirm != "EXECUTE RCON":
        raise ValueError("exact confirmation EXECUTE RCON is required")
    actor = str(actor).strip()[:64]
    source = str(source).strip()[:32]
    if not actor or not re.fullmatch(r"[A-Za-z0-9_.:@ -]+", actor):
        raise ValueError("actor contains unsupported characters")
    if not source or not re.fullmatch(r"[A-Za-z0-9_.:-]+", source):
        raise ValueError("source contains unsupported characters")
    started = time.monotonic()
    ok, output = True, ""
    try:
        output = rcon.execute(command)
    except Exception as exc:
        ok, output = False, str(exc)[:500]
    duration_ms = int((time.monotonic() - started) * 1000)
    output = output[-32768:]
    with connection() as database:
        database.execute(
            "insert into console_history(ts,actor,source,command,output,ok,duration_ms) values(?,?,?,?,?,?,?)",
            (int(time.time()), actor, source, command, output, int(ok), duration_ms),
        )
        database.execute(
            "delete from console_history where id not in (select id from console_history order by id desc limit ?)",
            (MAX_HISTORY,),
        )
    ops.audit("rcon.execute", "ok" if ok else "failed", verb=command_verb(command), actor=actor, source=source)
    result = {"ok": ok, "command": command, "output": output, "duration_ms": duration_ms}
    if not ok:
        raise RuntimeError(json.dumps(result))
    return result


def history(limit=200):
    limit = max(1, min(int(limit), MAX_HISTORY))
    with connection() as database:
        rows = database.execute(
            "select id,ts,actor,source,command,output,ok,duration_ms from console_history order by id desc limit ?",
            (limit,),
        ).fetchall()
    keys = ("id", "timestamp", "actor", "source", "command", "output", "ok", "duration_ms")
    return [dict(zip(keys, row)) for row in rows]


def saved():
    with connection() as database:
        rows = database.execute("select id,name,command,created_at from saved_commands order by lower(name)").fetchall()
    return [dict(zip(("id", "name", "command", "created_at"), row)) for row in rows]


def save_command(name, command):
    name = str(name).strip()
    command = rcon.validate_command(command)
    if not 1 <= len(name) <= 80 or any(character in name for character in "\r\n\0"):
        raise ValueError("saved-command name must be 1-80 single-line characters")
    with connection() as database:
        cursor = database.execute(
            "insert into saved_commands(name,command,created_at) values(?,?,?)",
            (name, command, int(time.time())),
        )
        result = {"id": cursor.lastrowid, "name": name, "command": command}
    ops.audit("rcon.saved-create", "ok", saved_id=result["id"], name=name)
    return result


def saved_by_id(identifier):
    with connection() as database:
        row = database.execute(
            "select id,name,command,created_at from saved_commands where id=?", (int(identifier),)
        ).fetchone()
    if not row:
        raise ValueError("saved command not found")
    return dict(zip(("id", "name", "command", "created_at"), row))


def delete_saved(identifier):
    identifier = int(identifier)
    jobs = ops.read_json(ops.STATE / "jobs.json", [])
    if any(job.get("enabled", True) and job.get("type") == "rcon" and str(job.get("saved_id")) == str(identifier) for job in jobs):
        raise ValueError("saved command is referenced by enabled scheduled work")
    with connection() as database:
        cursor = database.execute("delete from saved_commands where id=?", (identifier,))
        if cursor.rowcount != 1:
            raise ValueError("saved command not found")
    ops.audit("rcon.saved-delete", "ok", saved_id=identifier)
    return {"ok": True, "id": identifier}


def firewall_status():
    result = subprocess.run(
        ["sudo", "-n", str(HERE / "rcon-firewall.py"), "check"],
        text=True, capture_output=True, timeout=20,
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"effective_private": False, "error": (result.stderr or result.stdout).strip()[-500:]}


def discovered_catalog():
    """Ask the installed plugin for its real RCON surface and fail closed."""
    try:
        output = rcon.execute("getrconcmds")
    except Exception:
        return [], False
    names = []
    for token in re.split(r"[;\s,]+", output[:32768]):
        match = re.fullmatch(r"/?([A-Za-z_][A-Za-z0-9_]{1,63})(?::\d+)?", token.strip())
        name = match.group(1) if match else ""
        if name and name.casefold() not in {item.casefold() for item in names}:
            names.append(name)
        if len(names) >= 256: break
    result = []
    for name in names:
        arguments, description, risk = PALDEFENDER_COMMANDS.get(
            name.casefold(), ("<arguments>", "Server-discovered PalDefender command", "critical"),
        )
        result.append({
            "command": name, "arguments": arguments, "description": description,
            "risk": risk, "source": "paldefender", "discovered": True,
        })
    return result, bool(result)


def overview():
    desired = boolean_env("PALWORLD_RCON_ENABLED")
    firewall = firewall_status()
    connection_state = {"ok": False, "output": "RCON is disabled"}
    if firewall.get("effective_private"):
        try:
            connection_state = {"ok": True, "output": rcon.execute("Info")[-500:]}
        except Exception as exc:
            connection_state = {"ok": False, "output": str(exc)[:500]}
    plugin_catalog, discovered = discovered_catalog() if connection_state["ok"] else ([], False)
    return {
        "configured": connection_state["ok"], "desired": desired,
        "port": int(os.environ.get("PALWORLD_RCON_PORT", "25575")),
        "firewall": firewall, "connection": connection_state,
        "catalog": CATALOG + plugin_catalog,
        "paldefender_commands_discovered": discovered,
        "paldefender_command_count": len(plugin_catalog),
        "saved": saved(), "history": history(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("overview")
    sub.add_parser("catalog")
    validate_parser = sub.add_parser("validate"); validate_parser.add_argument("command")
    history_parser = sub.add_parser("history"); history_parser.add_argument("--limit", type=int, default=200)
    execute_parser = sub.add_parser("execute"); execute_parser.add_argument("payload")
    sub.add_parser("saved-list")
    save_parser = sub.add_parser("saved-add"); save_parser.add_argument("payload")
    delete_parser = sub.add_parser("saved-delete"); delete_parser.add_argument("id", type=int)
    saved_exec = sub.add_parser("saved-execute"); saved_exec.add_argument("id", type=int); saved_exec.add_argument("payload")
    args = parser.parse_args()
    try:
        if args.action == "overview": result = overview()
        elif args.action == "catalog": result = CATALOG
        elif args.action == "validate": result = {"ok": True, "command": rcon.validate_command(args.command)}
        elif args.action == "history": result = history(args.limit)
        elif args.action == "execute":
            payload = json.loads(args.payload); result = execute(payload.get("command", ""), payload.get("actor", "operator"), payload.get("source", "console"), payload.get("confirm", ""))
        elif args.action == "saved-list": result = saved()
        elif args.action == "saved-add":
            payload = json.loads(args.payload); result = save_command(payload.get("name", ""), payload.get("command", ""))
        elif args.action == "saved-delete": result = delete_saved(args.id)
        elif args.action == "saved-execute":
            payload = json.loads(args.payload); item = saved_by_id(args.id)
            result = execute(item["command"], payload.get("actor", "operator"), "saved", payload.get("confirm", ""))
        print(json.dumps(result, indent=2))
    except Exception as exc:
        try:
            decoded = json.loads(str(exc))
            print(json.dumps(decoded, indent=2))
        except json.JSONDecodeError:
            print(json.dumps({"ok": False, "error": str(exc)[:500]}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
