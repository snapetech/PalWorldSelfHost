#!/usr/bin/env python3
"""Permissioned remote-control and chat-relay primitives."""

from __future__ import annotations

import collections
import hmac
import json
import os
import pathlib
import random
import re
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


DISCORD_API = "https://discord.com/api/v10"
SNOWFLAKE = re.compile(r"[0-9]{17,20}")
EVENT_ID = re.compile(r"[A-Za-z0-9_.:-]{8,128}")
ROLE_LEVEL = {"viewer": 1, "moderator": 2, "admin": 3}
COMMAND_LEVEL = {
    "status": "viewer",
    "players": "viewer",
    "announce": "moderator",
    "say": "moderator",
    "save": "admin",
    "backup": "admin",
    "restart": "admin",
}
MUTATING_COMMANDS = {"announce", "say", "save", "backup", "restart"}
EPHEMERAL = 1 << 6


def csv_set(value):
    return {item.strip() for item in str(value or "").split(",") if item.strip()}


def snowflake(value, label):
    value = str(value or "")
    if not SNOWFLAKE.fullmatch(value):
        raise ValueError(f"{label} must be a Discord snowflake")
    return value


def snowflake_set(value, label, *, required=False):
    values = csv_set(value)
    if required and not values:
        raise ValueError(f"{label} must not be empty")
    return {snowflake(item, label) for item in values}


def bounded_text(value, label, maximum, *, minimum=0):
    value = str(value or "").strip()
    if len(value) < minimum or len(value) > maximum:
        raise ValueError(f"{label} must contain {minimum}-{maximum} characters")
    if any(ord(character) < 32 and character not in "\t" for character in value):
        raise ValueError(f"{label} contains a control character")
    return value


def redact_output(value):
    value = str(value or "")
    value = re.sub(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b", "<ip-redacted>", value)
    value = re.sub(r"\b[0-9]{17,20}\b", "<id-redacted>", value)
    value = re.sub(
        r"(?i)(password|authorization|token|secret)([=: ]+)[^\s,;]+",
        r"\1\2<redacted>",
        value,
    )
    return value[-1800:]


def safe_json(output):
    try:
        value = json.loads(output)
        return value if isinstance(value, dict) else None
    except (TypeError, json.JSONDecodeError):
        return None


class RateLimiter:
    def __init__(self, limit, window, *, now=time.time):
        self.limit = int(limit)
        self.window = float(window)
        self.now = now
        self.events = collections.defaultdict(collections.deque)
        self.lock = threading.Lock()

    def allow(self, key):
        current = self.now()
        with self.lock:
            bucket = self.events[str(key)]
            while bucket and bucket[0] <= current - self.window:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False
            bucket.append(current)
            return True


class ReplayGuard:
    def __init__(self, path, ops, *, ttl=86400, maximum=2048, now=time.time):
        self.path = pathlib.Path(path)
        self.ops = ops
        self.ttl = int(ttl)
        self.maximum = int(maximum)
        self.now = now
        self.lock = threading.Lock()

    def claim(self, event_id):
        event_id = bounded_text(event_id, "event identifier", 128, minimum=8)
        if not EVENT_ID.fullmatch(event_id):
            raise ValueError("event identifier has invalid characters")
        current = int(self.now())
        with self.lock:
            payload = self.ops.read_json(self.path, {})
            events = payload.get("events", {}) if isinstance(payload, dict) else {}
            if not isinstance(events, dict):
                events = {}
            events = {
                str(key): int(value) for key, value in events.items()
                if isinstance(value, int) and value > current - self.ttl
            }
            if event_id in events:
                return False
            events[event_id] = current
            if len(events) > self.maximum:
                events = dict(sorted(events.items(), key=lambda item: item[1])[-self.maximum:])
            self.ops.atomic_json(self.path, {"events": events, "updated_at": current})
            return True

    def release(self, event_id):
        with self.lock:
            payload = self.ops.read_json(self.path, {})
            events = payload.get("events", {}) if isinstance(payload, dict) else {}
            if isinstance(events, dict) and event_id in events:
                del events[event_id]
                self.ops.atomic_json(self.path, {"events": events, "updated_at": int(self.now())})


class CommandEngine:
    def __init__(self, here, ops, *, runner=subprocess.run):
        self.here = pathlib.Path(here)
        self.ops = ops
        self.runner = runner

    def _run(self, argv, timeout=1800):
        if self.runner is not subprocess.run:
            return self.runner(argv, text=True, capture_output=True, timeout=timeout)

        # Drain both pipes so a noisy child cannot deadlock, but retain only a
        # bounded diagnostic tail. A timeout terminates the complete command
        # process group rather than leaving sudo/script descendants behind.
        process = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
        retained = collections.deque()
        retained_bytes = 0
        retained_lock = threading.Lock()

        def drain(stream):
            nonlocal retained_bytes
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    return
                with retained_lock:
                    retained.append(chunk)
                    retained_bytes += len(chunk)
                    while retained_bytes > 65536 and retained:
                        retained_bytes -= len(retained.popleft())

        readers = [threading.Thread(target=drain, args=(stream,), daemon=True)
                   for stream in (process.stdout, process.stderr)]
        for reader in readers:
            reader.start()
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise
        finally:
            for reader in readers:
                reader.join(timeout=2)
            for stream in (process.stdout, process.stderr):
                stream.close()
        output = b"".join(retained).decode("utf-8", errors="replace")
        return subprocess.CompletedProcess(argv, returncode, output, "")

    @staticmethod
    def _format_status(output):
        data = safe_json(output)
        if not data:
            return redact_output(output) or "Status unavailable."
        fields = [
            ("World", data.get("servername") or data.get("name")),
            ("Version", data.get("version")),
            ("Players", f"{data.get('currentplayernum', '?')}/{data.get('maxplayernum', '?')}"),
            ("FPS", data.get("serverfps")),
            ("Uptime", data.get("uptime")),
            ("Day", data.get("days")),
        ]
        return " · ".join(f"{label}: {value}" for label, value in fields if value not in (None, ""))[:1800]

    @staticmethod
    def _format_players(output):
        data = safe_json(output)
        if not data or not isinstance(data.get("players"), list):
            return redact_output(output) or "Roster unavailable."
        names = []
        for player in data["players"][:50]:
            if isinstance(player, dict):
                name = bounded_text(player.get("name", "Unnamed"), "player name", 80, minimum=1)
                names.append(name)
        if not names:
            return "No players online."
        suffix = "" if len(data["players"]) <= 50 else f" · +{len(data['players']) - 50} more"
        return f"{len(data['players'])} online: " + ", ".join(names) + suffix

    def execute(self, name, options, *, role, actor, source):
        name = str(name or "").lower()
        if name not in COMMAND_LEVEL:
            raise ValueError("unknown remote command")
        if role not in ROLE_LEVEL or ROLE_LEVEL[role] < ROLE_LEVEL[COMMAND_LEVEL[name]]:
            raise PermissionError("role is not allowed to run this command")
        options = options if isinstance(options, dict) else {}
        if name in {"announce", "say"}:
            message = bounded_text(options.get("message"), "message", 500, minimum=1)
            argv = [str(self.here / "rest-client.py"), "announce", "--message", message]
        elif name == "status":
            argv = [str(self.here / "rest-client.py"), "info"]
        elif name == "players":
            argv = [str(self.here / "rest-client.py"), "players"]
        elif name == "save":
            argv = [str(self.here / "rest-client.py"), "save"]
        elif name == "backup":
            argv = ["sudo", "-n", str(self.here / "backup.sh"), "daily"]
        else:
            if options.get("confirmation") != "RESTART PALWORLD":
                raise ValueError("restart requires exact confirmation RESTART PALWORLD")
            argv = ["sudo", "-n", str(self.here / "graceful-restart.sh")]

        try:
            completed = self._run(argv)
            ok = completed.returncode == 0
            raw = (completed.stdout or "") + (completed.stderr or "")
        except subprocess.TimeoutExpired:
            ok = False
            raw = "Command timed out."
        self.ops.audit(
            f"bot.{name}", "ok" if ok else "failed",
            remote_actor=f"{source}:{actor}", remote_role=role, remote_source=source,
        )
        if not ok:
            return redact_output(raw) or "Command failed."
        if name == "status":
            return self._format_status(raw)
        if name == "players":
            return self._format_players(raw)
        return {
            "announce": "Announcement sent.",
            "say": "Discord message relayed to the game.",
            "save": "World save requested.",
            "backup": "Verified daily backup completed.",
            "restart": "Graceful restart completed.",
        }[name]


def discord_commands():
    text_option = lambda name, description, maximum: {
        "type": 3, "name": name, "description": description, "required": True,
        "min_length": 1, "max_length": maximum,
    }
    return [
        {"name": "status", "description": "Show the private Palworld server status", "type": 1},
        {"name": "players", "description": "List online player display names", "type": 1},
        {"name": "save", "description": "Request a world save", "type": 1},
        {"name": "backup", "description": "Create a verified daily backup", "type": 1},
        {"name": "announce", "description": "Send an operator announcement", "type": 1,
         "options": [text_option("message", "Announcement text", 500)]},
        {"name": "say", "description": "Relay a Discord message into Palworld", "type": 1,
         "options": [text_option("message", "Message shown in game", 500)]},
        {"name": "restart", "description": "Gracefully restart the Palworld server", "type": 1,
         "options": [text_option("confirmation", "Type RESTART PALWORLD", 32)]},
    ]


class DiscordHTTP:
    def __init__(self, token, *, base=DISCORD_API, opener=urllib.request.urlopen, sleeper=time.sleep):
        self.token = bounded_text(token, "Discord bot token", 256, minimum=24)
        self.base = base.rstrip("/")
        self.opener = opener
        self.sleeper = sleeper

    def request(self, method, path, payload=None, *, authenticated=True, retry=True):
        data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json", "User-Agent": "PalWorldSelfHost (self-hosted)"}
        if authenticated:
            headers["Authorization"] = "Bot " + self.token
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with self.opener(request, timeout=20) as response:
                body = response.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and retry:
                try:
                    wait = min(float(json.loads(exc.read()).get("retry_after", 1)), 10.0)
                except (ValueError, json.JSONDecodeError):
                    wait = 1.0
                self.sleeper(wait)
                return self.request(method, path, payload, authenticated=authenticated, retry=False)
            raise RuntimeError(f"Discord HTTP request failed with status {exc.code}") from None
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Discord HTTP request failed: {type(exc).__name__}") from None


class DiscordBot:
    def __init__(self, *, token, application_id, guild_id, channels, admins,
                 moderators, engine, ops, state_dir, http=None, now=time.time):
        self.token = bounded_text(token, "Discord bot token", 256, minimum=24)
        self.application_id = snowflake(application_id, "Discord application ID")
        self.guild_id = snowflake(guild_id, "Discord guild ID")
        self.channels = {snowflake(item, "Discord control channel") for item in channels}
        self.admins = {snowflake(item, "Discord administrator") for item in admins}
        self.moderators = {snowflake(item, "Discord moderator") for item in moderators}
        if not self.channels or not self.admins:
            raise ValueError("Discord control channels and administrators must not be empty")
        if self.admins & self.moderators:
            raise ValueError("Discord administrators and moderators must be distinct")
        self.engine = engine
        self.ops = ops
        self.http = http or DiscordHTTP(self.token)
        self.replay = ReplayGuard(pathlib.Path(state_dir) / "discord-interactions.json", ops, now=now)
        self.general_rate = RateLimiter(6, 60, now=now)
        self.mutation_rate = RateLimiter(2, 300, now=now)
        self.sequence = None
        self.session_id = None
        self.resume_url = None
        self.heartbeat_ack = True
        self.heartbeat_stop = threading.Event()

    def register_commands(self):
        return self.http.request(
            "PUT",
            f"/applications/{self.application_id}/guilds/{self.guild_id}/commands",
            discord_commands(),
        )

    def role_for(self, user_id):
        if user_id in self.admins:
            return "admin"
        if user_id in self.moderators:
            return "moderator"
        return None

    @staticmethod
    def interaction_options(interaction):
        options = interaction.get("data", {}).get("options", [])
        if not isinstance(options, list) or len(options) > 10:
            raise ValueError("interaction options are invalid")
        result = {}
        for item in options:
            if not isinstance(item, dict) or not re.fullmatch(r"[a-z0-9_-]{1,32}", str(item.get("name", ""))):
                raise ValueError("interaction option is invalid")
            result[item["name"]] = item.get("value")
        return result

    def _callback(self, interaction, payload):
        self.http.request(
            "POST", f"/interactions/{interaction['id']}/{interaction['token']}/callback",
            payload, authenticated=False,
        )

    def _immediate(self, interaction, message):
        self._callback(interaction, {
            "type": 4,
            "data": {"content": message[:1800], "flags": EPHEMERAL, "allowed_mentions": {"parse": []}},
        })

    def _finish(self, interaction, message):
        self.http.request(
            "PATCH",
            f"/webhooks/{self.application_id}/{interaction['token']}/messages/@original",
            {"content": message[:1800], "allowed_mentions": {"parse": []}},
            authenticated=False,
        )

    def handle_interaction(self, interaction, *, background=True):
        if not isinstance(interaction, dict) or interaction.get("type") != 2:
            return False
        if str(interaction.get("application_id")) != self.application_id:
            return False
        guild_id = str(interaction.get("guild_id", ""))
        channel_id = str(interaction.get("channel_id", ""))
        member = interaction.get("member", {})
        user_id = str(member.get("user", {}).get("id", "")) if isinstance(member, dict) else ""
        if guild_id != self.guild_id or channel_id not in self.channels or not SNOWFLAKE.fullmatch(user_id):
            self._immediate(interaction, "This command is outside the configured Palworld control boundary.")
            return True
        role = self.role_for(user_id)
        if not role:
            self.ops.audit("bot.denied", "failed", remote_actor=f"discord:{user_id}", remote_source="discord")
            self._immediate(interaction, "Your Discord identity is not allowlisted for Palworld control.")
            return True
        try:
            interaction_id = snowflake(interaction.get("id"), "interaction ID")
        except ValueError:
            return False
        if not self.replay.claim(interaction_id):
            self.ops.audit("bot.replay", "failed", remote_actor=f"discord:{user_id}", remote_source="discord")
            return True
        command = str(interaction.get("data", {}).get("name", "")).lower()
        if command not in COMMAND_LEVEL:
            self._immediate(interaction, "Unknown Palworld command.")
            return True
        if not self.general_rate.allow(user_id) or (command in MUTATING_COMMANDS and not self.mutation_rate.allow(user_id)):
            self.ops.audit("bot.rate-limit", "failed", remote_actor=f"discord:{user_id}", remote_source="discord")
            self._immediate(interaction, "Palworld command rate limit reached. Try again later.")
            return True
        try:
            options = self.interaction_options(interaction)
        except ValueError:
            self._immediate(interaction, "Palworld command options are invalid.")
            return True
        try:
            self._callback(interaction, {"type": 5, "data": {"flags": EPHEMERAL}})
        except Exception:
            self.replay.release(interaction_id)
            raise

        def work():
            try:
                result = self.engine.execute(command, options, role=role, actor=user_id, source="discord")
            except (ValueError, PermissionError) as exc:
                result = str(exc)
            except Exception as exc:
                self.ops.audit("bot.command", "failed", remote_actor=f"discord:{user_id}", remote_source="discord", error=type(exc).__name__)
                result = "Palworld command failed before completion."
            try:
                self._finish(interaction, result)
            except RuntimeError as exc:
                self.ops.audit("bot.response", "failed", remote_actor=f"discord:{user_id}", remote_source="discord", error=type(exc).__name__)

        if background:
            threading.Thread(target=work, daemon=True).start()
        else:
            work()
        return True

    def send_channel_message(self, channel_id, content):
        channel_id = snowflake(channel_id, "Discord relay channel")
        content = bounded_text(content, "relay message", 1800, minimum=1)
        return self.http.request(
            "POST", f"/channels/{channel_id}/messages",
            {"content": content, "allowed_mentions": {"parse": []}},
        )

    def validate_external_delivery(self, channel_id, marker):
        """Prove the configured Discord boundary and one reversible delivery."""
        channel_id = snowflake(channel_id, "Discord validation channel")
        marker = bounded_text(marker, "Discord validation marker", 64, minimum=16)

        user = self.http.request("GET", "/users/@me")
        application = self.http.request("GET", "/oauth2/applications/@me")
        guild = self.http.request("GET", f"/guilds/{self.guild_id}")
        channel = self.http.request("GET", f"/channels/{channel_id}")
        if (not isinstance(user, dict) or not SNOWFLAKE.fullmatch(str(user.get("id", "")))
                or user.get("bot") is not True):
            raise RuntimeError("Discord bot identity response was invalid")
        if not isinstance(application, dict) or str(application.get("id", "")) != self.application_id:
            raise RuntimeError("Discord token does not belong to the configured application")
        application_bot = application.get("bot")
        if not isinstance(application_bot, dict) or str(application_bot.get("id", "")) != str(user["id"]):
            raise RuntimeError("Discord application bot identity did not match the token")
        if not isinstance(guild, dict) or str(guild.get("id", "")) != self.guild_id:
            raise RuntimeError("Discord bot cannot verify the configured guild")
        if not isinstance(channel, dict) or str(channel.get("id", "")) != channel_id or str(channel.get("guild_id", "")) != self.guild_id:
            raise RuntimeError("Discord control channel is outside the configured guild")

        registered = self.register_commands()
        expected_commands = {item["name"] for item in discord_commands()}
        registered_commands = {
            str(item.get("name", "")) for item in registered if isinstance(item, dict)
        } if isinstance(registered, list) else set()
        if registered_commands != expected_commands:
            raise RuntimeError("Discord did not acknowledge the complete guild command set")

        content = f"PalWorldSelfHost validation {marker}. This message will be deleted automatically."
        message_id = None
        deleted = False
        try:
            message = self.send_channel_message(channel_id, content)
            message_id = str(message.get("id", "")) if isinstance(message, dict) else ""
            if not SNOWFLAKE.fullmatch(message_id):
                raise RuntimeError("Discord delivery response did not contain a message identifier")
            if str(message.get("channel_id", "")) != channel_id or message.get("content") != content:
                raise RuntimeError("Discord delivery response did not match the reviewed probe")
            self.http.request("DELETE", f"/channels/{channel_id}/messages/{message_id}")
            deleted = True
        finally:
            if message_id and not deleted:
                try:
                    self.http.request("DELETE", f"/channels/{channel_id}/messages/{message_id}")
                except RuntimeError:
                    pass
        return {
            "bot_authenticated": True,
            "application_matched": True,
            "guild_reachable": True,
            "channel_bound": True,
            "commands_registered": len(registered_commands),
            "message_delivered": True,
            "message_deleted": deleted,
        }

    def _heartbeat(self, ws, interval):
        if self.heartbeat_stop.wait(random.random() * interval):
            return
        while not self.heartbeat_stop.is_set():
            if not self.heartbeat_ack:
                ws.close()
                return
            self.heartbeat_ack = False
            ws.send(json.dumps({"op": 1, "d": self.sequence}))
            if self.heartbeat_stop.wait(interval):
                return

    def gateway_message(self, ws, raw):
        payload = json.loads(raw)
        if payload.get("s") is not None:
            self.sequence = payload["s"]
        opcode = payload.get("op")
        if opcode == 10:
            interval = max(1.0, float(payload["d"]["heartbeat_interval"]) / 1000.0)
            self.heartbeat_ack = True
            self.heartbeat_stop.clear()
            threading.Thread(target=self._heartbeat, args=(ws, interval), daemon=True).start()
            if self.session_id and self.resume_url and self.sequence is not None:
                ws.send(json.dumps({"op": 6, "d": {"token": self.token, "session_id": self.session_id, "seq": self.sequence}}))
            else:
                ws.send(json.dumps({"op": 2, "d": {
                    "token": self.token, "intents": 1,
                    "properties": {"os": "linux", "browser": "PalWorldSelfHost", "device": "PalWorldSelfHost"},
                }}))
        elif opcode == 11:
            self.heartbeat_ack = True
        elif opcode == 1:
            ws.send(json.dumps({"op": 1, "d": self.sequence}))
        elif opcode == 7:
            ws.close()
        elif opcode == 9:
            if not payload.get("d"):
                self.session_id = None
                self.resume_url = None
                self.sequence = None
            ws.close()
        elif opcode == 0 and payload.get("t") == "READY":
            self.session_id = payload["d"].get("session_id")
            self.resume_url = payload["d"].get("resume_gateway_url")
        elif opcode == 0 and payload.get("t") == "INTERACTION_CREATE":
            self.handle_interaction(payload.get("d"))

    def run_gateway(self):
        try:
            import websocket
        except ImportError as exc:
            raise RuntimeError("Discord control requires the python3-websocket package") from exc
        gateway = self.http.request("GET", "/gateway/bot").get("url")
        if not isinstance(gateway, str) or not gateway.startswith("wss://"):
            raise RuntimeError("Discord returned an invalid Gateway URL")
        backoff = 1
        while True:
            url = (self.resume_url or gateway).rstrip("/") + "/?v=10&encoding=json"
            self.heartbeat_stop = threading.Event()
            client = websocket.WebSocketApp(
                url,
                on_message=lambda ws, message: self.gateway_message(ws, message),
                on_error=lambda ws, error: self.ops.audit("bot.gateway", "failed", remote_source="discord", error=type(error).__name__),
                on_close=lambda ws, code, message: self.heartbeat_stop.set(),
            )
            client.run_forever(ping_interval=0)
            self.heartbeat_stop.set()
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)


class ChatRelay:
    def __init__(self, *, token, categories, discord, channel_id, ops, state_dir, now=time.time):
        self.token = bounded_text(token, "chat relay token", 256, minimum=24)
        self.categories = {bounded_text(item, "chat category", 16, minimum=1).lower() for item in categories}
        if not self.categories or not self.categories <= {"say", "guild", "global"}:
            raise ValueError("chat relay categories must be a non-empty subset of say,guild,global")
        self.discord = discord
        self.channel_id = snowflake(channel_id, "Discord relay channel")
        self.ops = ops
        self.replay = ReplayGuard(pathlib.Path(state_dir) / "chat-relay-events.json", ops, now=now)
        self.rate = RateLimiter(30, 60, now=now)
        self.lock = threading.Lock()

    def ingest(self, authorization, payload):
        expected = "Bearer " + self.token
        if not hmac.compare_digest(str(authorization or ""), expected):
            raise PermissionError("valid relay bearer required")
        if not isinstance(payload, dict):
            raise ValueError("chat relay body must be a JSON object")
        event_id = bounded_text(payload.get("event_id"), "event identifier", 128, minimum=8)
        player = bounded_text(payload.get("player"), "player display name", 80, minimum=1)
        message = bounded_text(payload.get("message"), "chat message", 500, minimum=1)
        category = bounded_text(payload.get("category"), "chat category", 16, minimum=1).lower()
        if category not in self.categories:
            raise ValueError("chat category is not enabled for relay")
        if not self.rate.allow("global"):
            raise RuntimeError("chat relay rate limit reached")
        with self.lock:
            if not self.replay.claim(event_id):
                raise FileExistsError("chat event was already relayed")
            try:
                self.discord.send_channel_message(self.channel_id, f"[Palworld/{category}] {player}: {message}")
            except Exception:
                self.replay.release(event_id)
                self.ops.audit("chat-relay.forward", "failed", event_id=event_id, category=category, remote_source="discord")
                raise
        self.ops.audit("chat-relay.forward", "ok", event_id=event_id, category=category, remote_source="discord")
        return {"status": "relayed", "event_id": event_id}


class ChatRelayHandler(BaseHTTPRequestHandler):
    relay = None
    server_version = "PalWorldChatRelay/1"

    def log_message(self, format_string, *args):
        return

    def respond(self, status, payload):
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/api/v1/chat":
            self.respond(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 4096:
                raise ValueError("chat relay body must contain 1-4096 bytes")
            payload = json.loads(self.rfile.read(length))
            result = self.relay.ingest(self.headers.get("Authorization"), payload)
            self.respond(202, result)
        except PermissionError as exc:
            self.respond(401, {"error": str(exc)})
        except FileExistsError as exc:
            self.respond(409, {"error": str(exc)})
        except RuntimeError as exc:
            self.respond(429, {"error": str(exc)})
        except (ValueError, json.JSONDecodeError) as exc:
            self.respond(400, {"error": str(exc)})


def start_chat_relay(relay, bind, port):
    if bind not in {"127.0.0.1", "::1"}:
        raise ValueError("chat relay must bind to loopback")
    port = int(port)
    if not 1024 <= port <= 65535:
        raise ValueError("chat relay port must be between 1024 and 65535")
    handler = type("ConfiguredChatRelayHandler", (ChatRelayHandler,), {"relay": relay})
    server = ThreadingHTTPServer((bind, port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
