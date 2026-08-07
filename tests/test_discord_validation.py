import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]
APP = "444444444444444444"
GUILD = "555555555555555555"
CHANNEL = "666666666666666666"
RELAY_CHANNEL = "777777777777777777"
ADMIN = "111111111111111111"


class DiscordValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.environment = mock.patch.dict(os.environ, {
            "PALWORLD_STATE_DIR": str(self.root),
            "PALWORLD_DISCORD_BOT_TOKEN": "fixture-discord-token-value-123456",
            "PALWORLD_DISCORD_APPLICATION_ID": APP,
            "PALWORLD_DISCORD_GUILD_ID": GUILD,
            "PALWORLD_DISCORD_CONTROL_CHANNELS": CHANNEL,
            "PALWORLD_DISCORD_ADMINS": ADMIN,
            "PALWORLD_DISCORD_MODERATORS": "",
            "PALWORLD_DISCORD_CHAT_CHANNEL": RELAY_CHANNEL,
            "PALWORLD_CHAT_RELAY_ENABLED": "true",
            "PALWORLD_CHAT_RELAY_TOKEN": "fixture-chat-relay-token-123456",
        })
        self.environment.start()
        spec = importlib.util.spec_from_file_location(
            f"discord_validation_test_{id(self)}",
            ROOT / "scripts/discord-validation.py",
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def test_plan_binds_secrets_but_never_returns_them_or_identifiers(self):
        planned = self.module.plan()
        encoded = json.dumps(planned)
        self.assertEqual("VALIDATE DISCORD DELIVERY", planned["confirmation"])
        self.assertEqual(7, len(planned["commands"]))
        self.assertNotIn(APP, encoded)
        self.assertNotIn(GUILD, encoded)
        self.assertNotIn(CHANNEL, encoded)
        self.assertNotIn(RELAY_CHANNEL, encoded)
        self.assertNotIn("fixture-discord-token", encoded)
        before = planned["plan_hash"]
        os.environ["PALWORLD_DISCORD_BOT_TOKEN"] += "-changed"
        self.assertNotEqual(before, self.module.plan()["plan_hash"])

    def test_execute_requires_exact_plan_and_writes_redacted_receipt(self):
        planned = self.module.plan()
        with self.assertRaisesRegex(ValueError, "plan changed"):
            self.module.execute("0" * 64, planned["confirmation"])
        with self.assertRaisesRegex(ValueError, "exact confirmation"):
            self.module.execute(planned["plan_hash"], "yes")
        proof = {
            "bot_authenticated": True,
            "application_matched": True,
            "guild_reachable": True,
            "channel_bound": True,
            "commands_registered": 7,
            "message_delivered": True,
            "message_deleted": True,
        }
        with mock.patch.object(
            self.module.control.DiscordBot,
            "validate_external_delivery",
            return_value=proof,
        ):
            receipt = self.module.execute(planned["plan_hash"], planned["confirmation"])
        stored = json.loads((self.root / "discord-validation.json").read_text())
        self.assertEqual(receipt, stored)
        encoded = json.dumps(stored)
        self.assertNotIn(APP, encoded)
        self.assertNotIn(GUILD, encoded)
        self.assertNotIn(CHANNEL, encoded)
        self.assertNotIn(RELAY_CHANNEL, encoded)
        self.assertNotIn("token", encoded.casefold())
        audit = (self.root / "audit.jsonl").read_text()
        self.assertIn('"action": "bot.discord-validation"', audit)
        self.assertNotIn(CHANNEL, audit)
        self.assertTrue(self.module.status()["validated"])
        os.environ["PALWORLD_CHAT_RELAY_TOKEN"] += "-changed"
        stale = self.module.status()
        self.assertFalse(stale["validated"])
        self.assertIn("changed after validation", stale["reason"])

    def test_relay_and_control_channel_must_be_fully_configured(self):
        os.environ["PALWORLD_CHAT_RELAY_ENABLED"] = "false"
        with self.assertRaisesRegex(ValueError, "PALWORLD_CHAT_RELAY_ENABLED"):
            self.module.plan()
        os.environ["PALWORLD_CHAT_RELAY_ENABLED"] = "true"
        os.environ["PALWORLD_DISCORD_CHAT_CHANNEL"] = ""
        with self.assertRaisesRegex(ValueError, "Discord validation channel"):
            self.module.plan()

    def test_installed_cli_exposes_the_environment_loading_wrapper(self):
        cli = (ROOT / "scripts/palworldctl").read_text()
        wrapper = (ROOT / "scripts/ops-discord-validation.sh").read_text()
        installer = (ROOT / "scripts/install.sh").read_text()
        self.assertIn('discord-validation) "$lib/ops-discord-validation.sh" "$@"', cli)
        self.assertIn('source "$ENV_FILE"', wrapper)
        self.assertIn('install -m 0755 "$repo/scripts/"*.sh', installer)


if __name__ == "__main__":
    unittest.main()
