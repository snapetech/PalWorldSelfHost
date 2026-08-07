#!/usr/bin/env python3
"""Optional allowlisted Matrix and Discord Palworld control bot."""

import importlib.util
import json
import os
import pathlib
import threading
import time
import urllib.parse
import urllib.request


HERE = pathlib.Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ops = load("ops_lib", HERE / "ops-lib.py")
control = load("bot_control", HERE / "bot_control.py")
engine = control.CommandEngine(HERE, ops)


class MatrixBot:
    def __init__(self, base, token, rooms, users):
        self.base = base.rstrip("/")
        self.token = token
        self.rooms = rooms
        self.users = users
        self.rate = control.RateLimiter(6, 60)

    def request(self, method, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=40) as response:
            return json.load(response)

    def send(self, room, text):
        transaction = str(time.time_ns())
        self.request(
            "PUT",
            f"/_matrix/client/v3/rooms/{urllib.parse.quote(room, safe='')}/send/m.room.message/{transaction}",
            {"msgtype": "m.text", "body": text[:4000]},
        )

    def command(self, sender, text):
        parts = text.strip().split(maxsplit=1)
        name = parts[0].removeprefix("!").lower()
        argument = parts[1] if len(parts) > 1 else ""
        if name not in control.COMMAND_LEVEL:
            return "Commands: !status !players !save !backup !announce TEXT !say TEXT !restart RESTART PALWORLD"
        if not self.rate.allow(sender):
            return "Palworld command rate limit reached. Try again later."
        options = {}
        if name in {"announce", "say"}:
            options["message"] = argument
        elif name == "restart":
            options["confirmation"] = argument
        try:
            return engine.execute(name, options, role="admin", actor=sender, source="matrix")
        except (ValueError, PermissionError) as exc:
            return str(exc)

    def run(self):
        # Establish a fresh sync position without executing historical timeline events.
        initial = self.request("GET", "/_matrix/client/v3/sync?timeout=0")
        since = initial["next_batch"]
        while True:
            try:
                data = self.request("GET", "/_matrix/client/v3/sync?timeout=30000&since=" + urllib.parse.quote(since))
                since = data["next_batch"]
                for room, body in data.get("rooms", {}).get("join", {}).items():
                    if room not in self.rooms:
                        continue
                    for event in body.get("timeline", {}).get("events", []):
                        sender = event.get("sender")
                        message = event.get("content", {}).get("body", "")
                        if event.get("type") == "m.room.message" and sender in self.users and message.startswith("!"):
                            self.send(room, self.command(sender, message))
            except Exception as exc:
                ops.audit("bot.poll", "failed", remote_source="matrix", error=type(exc).__name__)
                time.sleep(10)


def configured_matrix():
    base = os.environ.get("PALWORLD_MATRIX_HOMESERVER", "").strip()
    token = os.environ.get("PALWORLD_MATRIX_ACCESS_TOKEN", "").strip()
    rooms = control.csv_set(os.environ.get("PALWORLD_MATRIX_ROOMS"))
    users = control.csv_set(os.environ.get("PALWORLD_MATRIX_ADMINS"))
    return MatrixBot(base, token, rooms, users) if base and token and rooms and users else None


def configured_discord():
    token = os.environ.get("PALWORLD_DISCORD_BOT_TOKEN", "").strip()
    if not token:
        return None
    return control.DiscordBot(
        token=token,
        application_id=os.environ.get("PALWORLD_DISCORD_APPLICATION_ID"),
        guild_id=os.environ.get("PALWORLD_DISCORD_GUILD_ID"),
        channels=control.snowflake_set(os.environ.get("PALWORLD_DISCORD_CONTROL_CHANNELS"), "Discord control channels", required=True),
        admins=control.snowflake_set(os.environ.get("PALWORLD_DISCORD_ADMINS"), "Discord administrators", required=True),
        moderators=control.snowflake_set(os.environ.get("PALWORLD_DISCORD_MODERATORS"), "Discord moderators"),
        engine=engine,
        ops=ops,
        state_dir=os.environ.get("PALWORLD_STATE_DIR", "/var/lib/palworld"),
    )


def main():
    matrix = configured_matrix()
    discord = configured_discord()
    if not matrix and not discord:
        raise SystemExit("Matrix or Discord bot configuration is required")
    if matrix and discord:
        threading.Thread(target=matrix.run, daemon=True).start()
    if discord:
        registered = discord.register_commands()
        ops.audit("bot.discord-commands", "ok", remote_source="discord", count=len(registered))
        if os.environ.get("PALWORLD_CHAT_RELAY_ENABLED", "false").lower() == "true":
            relay = control.ChatRelay(
                token=os.environ.get("PALWORLD_CHAT_RELAY_TOKEN"),
                categories=control.csv_set(os.environ.get("PALWORLD_CHAT_RELAY_CATEGORIES", "global")),
                discord=discord,
                channel_id=os.environ.get("PALWORLD_DISCORD_CHAT_CHANNEL"),
                ops=ops,
                state_dir=os.environ.get("PALWORLD_STATE_DIR", "/var/lib/palworld"),
            )
            control.start_chat_relay(
                relay, "127.0.0.1", int(os.environ.get("PALWORLD_CHAT_RELAY_PORT", "8215")),
            )
            ops.audit("chat-relay.start", "ok", categories=sorted(relay.categories))
        discord.run_gateway()
    else:
        matrix.run()


if __name__ == "__main__":
    main()
