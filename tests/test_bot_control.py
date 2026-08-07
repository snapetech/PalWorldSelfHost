import importlib.util
import json
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bot_control", ROOT / "scripts" / "bot_control.py")
bot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bot)

ADMIN = "111111111111111111"
MODERATOR = "222222222222222222"
OUTSIDER = "333333333333333333"
APP = "444444444444444444"
GUILD = "555555555555555555"
CONTROL = "666666666666666666"
RELAY_CHANNEL = "777777777777777777"
MESSAGE = "888888888888888888"


class FakeOps:
    def __init__(self):
        self.audits = []

    @staticmethod
    def read_json(path, default):
        try:
            return json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    @staticmethod
    def atomic_json(path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload))
        os.chmod(temporary, 0o660)
        temporary.replace(path)

    def audit(self, action, result, **details):
        self.audits.append({"action": action, "result": result, **details})


class FakeHTTP:
    def __init__(self):
        self.calls = []
        self.fail_next_callback = False
        self.wrong_validation_content = False

    def request(self, method, path, payload=None, **kwargs):
        self.calls.append((method, path, payload, kwargs))
        if self.fail_next_callback and "/callback" in path:
            self.fail_next_callback = False
            raise RuntimeError("fixture callback failure")
        if path.endswith("/commands"):
            return [{"id": str(index), **item} for index, item in enumerate(payload)]
        if path == "/gateway/bot":
            return {"url": "wss://gateway.discord.gg"}
        if path == "/users/@me":
            return {"id": APP, "bot": True}
        if path == "/oauth2/applications/@me":
            return {"id": APP, "bot": {"id": APP}}
        if path == f"/guilds/{GUILD}":
            return {"id": GUILD}
        if path == f"/channels/{CONTROL}":
            return {"id": CONTROL, "guild_id": GUILD}
        if path == f"/channels/{RELAY_CHANNEL}":
            return {"id": RELAY_CHANNEL, "guild_id": GUILD}
        if method == "POST" and path == f"/channels/{RELAY_CHANNEL}/messages":
            content = "wrong" if self.wrong_validation_content else payload["content"]
            return {"id": MESSAGE, "channel_id": RELAY_CHANNEL, "content": content}
        return {}


def interaction(identifier, user=ADMIN, channel=CONTROL, command="status", options=None):
    return {
        "id": str(identifier),
        "application_id": APP,
        "type": 2,
        "guild_id": GUILD,
        "channel_id": channel,
        "member": {"user": {"id": user}},
        "token": f"interaction-token-{identifier}",
        "data": {"name": command, "options": options or []},
    }


class BotControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.ops = FakeOps()
        self.runner_calls = []

        def runner(argv, **kwargs):
            self.runner_calls.append(argv)
            command = pathlib.Path(argv[0]).name
            if command == "rest-client.py" and argv[1] == "info":
                output = json.dumps({
                    "servername": "Fixture", "version": "v1", "currentplayernum": 2,
                    "maxplayernum": 32, "serverfps": 60, "uptime": 123,
                    "ip": "192.0.2.1", "token": "should-not-escape",
                })
            elif command == "rest-client.py" and argv[1] == "players":
                output = json.dumps({"players": [
                    {"name": "Lamball", "userId": "123456789012345678", "ip": "192.0.2.2"},
                    {"name": "Cattiva", "userId": "987654321098765432", "ip": "192.0.2.3"},
                ]})
            else:
                output = "sensitive path /tmp/fixture"
            return subprocess.CompletedProcess(argv, 0, output, "")

        self.engine = bot.CommandEngine(ROOT / "scripts", self.ops, runner=runner)
        self.http = FakeHTTP()
        self.discord = bot.DiscordBot(
            token="fixture-discord-token-value-123456",
            application_id=APP, guild_id=GUILD, channels={CONTROL}, admins={ADMIN},
            moderators={MODERATOR}, engine=self.engine, ops=self.ops,
            state_dir=self.root, http=self.http,
        )

    def tearDown(self):
        self.discord.heartbeat_stop.set()
        self.temporary.cleanup()

    def test_command_engine_redacts_status_and_player_identifiers(self):
        status = self.engine.execute("status", {}, role="viewer", actor=ADMIN, source="discord")
        self.assertIn("World: Fixture", status)
        self.assertIn("Players: 2/32", status)
        self.assertNotIn("192.0.2.1", status)
        self.assertNotIn("should-not-escape", status)

        players = self.engine.execute("players", {}, role="moderator", actor=MODERATOR, source="discord")
        self.assertEqual("2 online: Lamball, Cattiva", players)
        self.assertNotIn("123456789012345678", players)
        self.assertTrue(all(event["remote_source"] == "discord" for event in self.ops.audits))

    def test_mutations_are_role_scoped_and_restart_exactly_confirmed(self):
        with self.assertRaises(PermissionError):
            self.engine.execute("backup", {}, role="moderator", actor=MODERATOR, source="discord")
        with self.assertRaisesRegex(ValueError, "RESTART PALWORLD"):
            self.engine.execute("restart", {"confirmation": "restart"}, role="admin", actor=ADMIN, source="discord")
        self.assertFalse(self.runner_calls)

        result = self.engine.execute(
            "restart", {"confirmation": "RESTART PALWORLD"},
            role="admin", actor=ADMIN, source="discord",
        )
        self.assertEqual("Graceful restart completed.", result)
        self.assertIn("graceful-restart.sh", self.runner_calls[0][-1])

    def test_guild_commands_register_and_allowed_interaction_defers_then_edits(self):
        registered = self.discord.register_commands()
        self.assertEqual(7, len(registered))
        method, path, payload, kwargs = self.http.calls.pop(0)
        self.assertEqual(("PUT", f"/applications/{APP}/guilds/{GUILD}/commands"), (method, path))
        self.assertEqual({"status", "players", "save", "backup", "announce", "say", "restart"}, {item["name"] for item in payload})

        self.assertTrue(self.discord.handle_interaction(interaction("800000000000000001"), background=False))
        self.assertEqual(1, len(self.runner_calls))
        callback = self.http.calls[0]
        response = self.http.calls[1]
        self.assertEqual(5, callback[2]["type"])
        self.assertEqual(bot.EPHEMERAL, callback[2]["data"]["flags"])
        self.assertEqual({"parse": []}, response[2]["allowed_mentions"])
        self.assertIn("World: Fixture", response[2]["content"])

        self.discord.handle_interaction(interaction("800000000000000001"), background=False)
        self.assertEqual(1, len(self.runner_calls), "a replayed interaction must not run twice")

    def test_external_validation_proves_boundary_delivery_and_deletion(self):
        result = self.discord.validate_external_delivery(RELAY_CHANNEL, "0123456789abcdef")
        self.assertEqual(7, result["commands_registered"])
        self.assertTrue(result["message_delivered"])
        self.assertTrue(result["message_deleted"])
        calls = {(method, path) for method, path, _, _ in self.http.calls}
        self.assertIn(("GET", "/users/@me"), calls)
        self.assertIn(("GET", f"/guilds/{GUILD}"), calls)
        self.assertIn(("POST", f"/channels/{RELAY_CHANNEL}/messages"), calls)
        self.assertIn(("DELETE", f"/channels/{RELAY_CHANNEL}/messages/{MESSAGE}"), calls)
        message = next(call[2] for call in self.http.calls if call[0] == "POST" and call[1].endswith("/messages"))
        self.assertEqual({"parse": []}, message["allowed_mentions"])

    def test_external_validation_deletes_probe_after_response_mismatch(self):
        self.http.wrong_validation_content = True
        with self.assertRaisesRegex(RuntimeError, "did not match"):
            self.discord.validate_external_delivery(RELAY_CHANNEL, "0123456789abcdef")
        self.assertTrue(any(
            method == "DELETE" and path == f"/channels/{RELAY_CHANNEL}/messages/{MESSAGE}"
            for method, path, _, _ in self.http.calls
        ))

    def test_wrong_boundary_and_unknown_identity_never_execute(self):
        self.discord.handle_interaction(
            interaction("800000000000000002", channel="888888888888888888"), background=False,
        )
        self.discord.handle_interaction(
            interaction("800000000000000003", user=OUTSIDER), background=False,
        )
        self.assertFalse(self.runner_calls)
        self.assertEqual([4, 4], [call[2]["type"] for call in self.http.calls])
        self.assertTrue(any(event["action"] == "bot.denied" for event in self.ops.audits))

    def test_moderator_cannot_backup_and_callback_failure_releases_replay_claim(self):
        denied = interaction("800000000000000004", user=MODERATOR, command="backup")
        self.discord.handle_interaction(denied, background=False)
        self.assertFalse(self.runner_calls)
        self.assertIn("not allowed", self.http.calls[-1][2]["content"])

        retryable = interaction("800000000000000005")
        self.http.fail_next_callback = True
        with self.assertRaisesRegex(RuntimeError, "callback"):
            self.discord.handle_interaction(retryable, background=False)
        self.discord.handle_interaction(retryable, background=False)
        self.assertEqual(1, len(self.runner_calls))

    def test_discord_to_game_say_is_bounded_and_audited(self):
        say = interaction(
            "800000000000000006", user=MODERATOR, command="say",
            options=[{"name": "message", "type": 3, "value": "Hello Palpagos"}],
        )
        self.discord.handle_interaction(say, background=False)
        self.assertEqual("announce", self.runner_calls[0][1])
        self.assertEqual("Hello Palpagos", self.runner_calls[0][-1])
        self.assertEqual("Discord message relayed to the game.", self.http.calls[-1][2]["content"])

    def test_gateway_identify_uses_only_guild_intent_and_resume_state(self):
        class Socket:
            def __init__(self): self.messages = []; self.closed = False
            def send(self, value): self.messages.append(json.loads(value))
            def close(self): self.closed = True

        socket = Socket()
        self.discord.gateway_message(socket, json.dumps({"op": 10, "d": {"heartbeat_interval": 60000}}))
        self.assertEqual(2, socket.messages[0]["op"])
        self.assertEqual(1, socket.messages[0]["d"]["intents"])
        self.assertNotEqual(1 << 15, socket.messages[0]["d"]["intents"] & (1 << 15))
        self.discord.heartbeat_stop.set()

        self.discord.session_id = "session"
        self.discord.resume_url = "wss://resume.discord.gg"
        self.discord.sequence = 42
        self.discord.heartbeat_stop = __import__("threading").Event()
        resumed = Socket()
        self.discord.gateway_message(resumed, json.dumps({"op": 10, "d": {"heartbeat_interval": 60000}}))
        self.assertEqual({"token": self.discord.token, "session_id": "session", "seq": 42}, resumed.messages[0]["d"])

    def test_chat_relay_auth_category_replay_mentions_and_failed_send_retry(self):
        relay = bot.ChatRelay(
            token="fixture-chat-relay-token-123456",
            categories={"global"}, discord=self.discord, channel_id=RELAY_CHANNEL,
            ops=self.ops, state_dir=self.root,
        )
        payload = {"event_id": "chat-event-0001", "player": "Lamball", "message": "hello @everyone", "category": "global"}
        with self.assertRaises(PermissionError):
            relay.ingest("Bearer wrong", payload)
        with self.assertRaisesRegex(ValueError, "not enabled"):
            relay.ingest("Bearer fixture-chat-relay-token-123456", {**payload, "category": "guild"})

        result = relay.ingest("Bearer fixture-chat-relay-token-123456", payload)
        self.assertEqual("relayed", result["status"])
        channel_call = self.http.calls[-1]
        self.assertEqual(f"/channels/{RELAY_CHANNEL}/messages", channel_call[1])
        self.assertEqual({"parse": []}, channel_call[2]["allowed_mentions"])
        self.assertIn("@everyone", channel_call[2]["content"])
        with self.assertRaises(FileExistsError):
            relay.ingest("Bearer fixture-chat-relay-token-123456", payload)
        self.assertNotIn("hello @everyone", (self.root / "chat-relay-events.json").read_text())

        original = self.discord.send_channel_message
        self.discord.send_channel_message = lambda channel, content: (_ for _ in ()).throw(RuntimeError("offline"))
        retry_payload = {**payload, "event_id": "chat-event-0002"}
        with self.assertRaises(RuntimeError):
            relay.ingest("Bearer fixture-chat-relay-token-123456", retry_payload)
        self.discord.send_channel_message = original
        self.assertEqual("relayed", relay.ingest("Bearer fixture-chat-relay-token-123456", retry_payload)["status"])
        self.assertTrue(any(event["action"] == "chat-relay.forward" and event["result"] == "failed" for event in self.ops.audits))

    def test_relay_listener_refuses_non_loopback(self):
        relay = bot.ChatRelay(
            token="fixture-chat-relay-token-123456", categories={"global"},
            discord=self.discord, channel_id=RELAY_CHANNEL, ops=self.ops, state_dir=self.root,
        )
        with self.assertRaisesRegex(ValueError, "loopback"):
            bot.start_chat_relay(relay, "0.0.0.0", 8215)

    def test_real_command_runner_bounds_output_and_times_out_process_group(self):
        engine = bot.CommandEngine(ROOT / "scripts", self.ops)
        completed = engine._run([
            "python3", "-c", "import sys; sys.stdout.write('x'*100000 + 'TAIL')",
        ], timeout=5)
        self.assertEqual(0, completed.returncode)
        self.assertLessEqual(len(completed.stdout.encode()), 65536)
        self.assertTrue(completed.stdout.endswith("TAIL"))
        with self.assertRaises(subprocess.TimeoutExpired):
            engine._run(["python3", "-c", "import time; time.sleep(5)"], timeout=0.05)

    def test_malformed_interaction_options_are_refused_before_execution(self):
        malformed = interaction(
            "800000000000000007", options=[{"name": "BAD NAME", "value": "ignored"}],
        )
        self.assertTrue(self.discord.handle_interaction(malformed, background=False))
        self.assertFalse(self.runner_calls)
        self.assertEqual(4, self.http.calls[-1][2]["type"])
        self.assertIn("invalid", self.http.calls[-1][2]["data"]["content"])

    def test_installed_service_can_use_only_the_exact_sudo_wrappers(self):
        unit = (ROOT / "systemd" / "palworld-bot.service").read_text()
        sudoers = (ROOT / "config" / "palworld-ops.sudoers").read_text()
        installer = (ROOT / "scripts" / "install.sh").read_text()
        self.assertNotIn("NoNewPrivileges=true", unit)
        self.assertIn("/usr/local/lib/palworld/graceful-restart.sh", sudoers)
        self.assertIn("/usr/local/lib/palworld/backup.sh", sudoers)
        self.assertIn('PALWORLD_DISCORD_BOT_TOKEN:-', installer)
        self.assertIn("systemctl enable --now palworld-bot.service", installer)


if __name__ == "__main__":
    unittest.main()
