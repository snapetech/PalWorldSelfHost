import importlib.util
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WhitelistTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        os.environ["PALWORLD_STATE_DIR"] = self.temporary.name
        self.manager = load(f"whitelist_manager_{id(self)}", ROOT / "scripts/whitelist-manager.py")
        self.manager.ops.STATE = pathlib.Path(self.temporary.name)
        self.manager.STATE_FILE = pathlib.Path(self.temporary.name) / "whitelist.json"

    def tearDown(self): self.temporary.cleanup()

    def test_transactional_replace_and_enable_require_exact_confirmations(self):
        entries = [{"user_id": "steam_alice", "name": "Alice"}]
        self.assertEqual(self.manager.plan(entries)["after_count"], 1)
        with self.assertRaises(ValueError): self.manager.replace(entries, "yes")
        self.manager.replace(entries, "REPLACE WHITELIST")
        with self.assertRaises(ValueError): self.manager.set_enabled(True, "yes")
        enabled = self.manager.set_enabled(True, "ENABLE WHITELIST")
        self.assertTrue(enabled["enabled"])
        self.assertGreater(enabled["grace_until"], enabled["enabled_at"])

    def test_empty_and_duplicate_allow_lists_are_refused(self):
        with self.assertRaises(ValueError): self.manager.set_enabled(True, "ENABLE WHITELIST")
        with self.assertRaises(ValueError): self.manager.normalize([
            {"user_id": "Steam_Alice", "name": "Alice"},
            {"user_id": "steam_alice", "name": "Duplicate"},
        ])

    def test_enforcer_kicks_only_outsiders_and_fails_closed_on_roster_error(self):
        enforcer = load(f"whitelist_enforcer_{id(self)}", ROOT / "scripts/whitelist-enforcer.py")
        enforcer.ops.STATE = pathlib.Path(self.temporary.name)
        enforcer.manager.STATE_FILE = pathlib.Path(self.temporary.name) / "whitelist.json"
        enforcer.manager.ops.STATE = pathlib.Path(self.temporary.name)
        enforcer.manager.replace([{"user_id": "steam_alice", "name": "Alice"}], "REPLACE WHITELIST")
        enforcer.manager.set_enabled(True, "ENABLE WHITELIST")
        state = enforcer.manager.state(); state["grace_until"] = 0
        enforcer.ops.atomic_json(enforcer.manager.STATE_FILE, state)
        enforcer.run_json = lambda *args: {"players": [
            {"userId": "steam_alice", "name": "Alice"},
            {"userId": "steam_eve", "name": "Eve"},
        ]}
        calls = []
        with mock.patch.object(enforcer.subprocess, "run", side_effect=lambda args, **kwargs: calls.append(args) or subprocess.CompletedProcess(args, 0, "{}", "")):
            result = enforcer.enforce(now=1000)
        self.assertEqual((result["allowed"], result["rejected"]), (1, 1))
        self.assertEqual(len(calls), 1)
        self.assertIn("steam_eve", calls[0])
        enforcer.run_json = lambda *args: (_ for _ in ()).throw(RuntimeError("roster unavailable"))
        with self.assertRaises(RuntimeError): enforcer.enforce(now=1001)


if __name__ == "__main__": unittest.main()
