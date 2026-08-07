import importlib.util
import json
import os
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("mod_config_manager", ROOT / "scripts" / "mod-config-manager.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


class ModConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.install = self.root / "server"
        self.state = self.root / "state"
        self.paldefender = self.install / "Pal/Binaries/Win64/PalDefender"
        self.mods = self.install / "Pal/Binaries/Win64/ue4ss/Mods"
        self.paldefender.mkdir(parents=True)
        self.mods.mkdir(parents=True)
        self.state.mkdir()
        mod.INSTALL = self.install.resolve()
        mod.STATE = self.state.resolve()
        mod.ops.STATE = self.state.resolve()
        self.config = self.paldefender / "Config.json"
        self.original = {
            "version": "fixture", "shouldKickCheaters": False,
            "chatMessageMaxLen": 200, "whitelistMessage": "Apply in Discord", "MOTD": ["Welcome"],
            "adminIPs": ["192.0.2.44"], "futureSecretToken": "never-return-this",
        }
        self.config.write_text(json.dumps(self.original, indent=2) + "\n")

    def tearDown(self):
        self.temporary.cleanup()

    def test_status_is_structured_and_identifier_redacted(self):
        status = mod.status()
        defender = status["paldefender"]
        self.assertTrue(defender["installed"])
        self.assertTrue(defender["config_exists"])
        self.assertEqual(False, defender["values"]["shouldKickCheaters"])
        self.assertEqual(["Welcome"], defender["motd"])
        self.assertEqual(3, defender["unknown_key_count"])
        rendered = json.dumps(status)
        self.assertNotIn("192.0.2.44", rendered)
        self.assertNotIn("never-return-this", rendered)
        self.assertEqual(len(defender["schema"]), 57)

    def test_plan_apply_and_rollback_preserve_every_unknown_field(self):
        payload = {"updates": {"shouldKickCheaters": True, "RCONTimeout": 8.5}, "motd": ["Line one", "Line two"]}
        plan, _ = mod.planned(payload)
        self.assertTrue(plan["changed"])
        self.assertEqual(
            {"shouldKickCheaters", "RCONTimeout", "MOTD"},
            {item["key"] for item in plan["changes"]},
        )
        with self.assertRaisesRegex(ValueError, "confirmation"):
            mod.apply(payload, plan["current_sha256"], "")
        applied = mod.apply(payload, plan["current_sha256"], "APPLY MOD CONFIG")
        current = json.loads(self.config.read_text())
        self.assertTrue(current["shouldKickCheaters"])
        self.assertEqual(8.5, current["RCONTimeout"])
        self.assertEqual(["Line one", "Line two"], current["MOTD"])
        self.assertEqual(self.original["adminIPs"], current["adminIPs"])
        self.assertEqual(self.original["futureSecretToken"], current["futureSecretToken"])
        backup = self.state / "mod-config-backups" / applied["backup"]
        self.assertEqual(0o600, backup.stat().st_mode & 0o777)

        rolled_back = mod.rollback(applied["after_sha256"], "ROLLBACK MOD CONFIG")
        self.assertTrue(rolled_back["restart_or_reload_required"])
        self.assertEqual(self.original, json.loads(self.config.read_text()))

    def test_stale_hash_bad_types_ranges_and_malformed_config_are_refused(self):
        payload = {"updates": {"chatMessageMaxLen": 250}}
        plan, _ = mod.planned(payload)
        self.config.write_text(json.dumps({**self.original, "chatMessageMaxLen": 300}))
        with self.assertRaisesRegex(ValueError, "changed after review"):
            mod.apply(payload, plan["current_sha256"], "APPLY MOD CONFIG")
        for invalid in (
            {"updates": {"chatMessageMaxLen": True}},
            {"updates": {"chatMessageMaxLen": 1001}},
            {"updates": {"whitelistMessage": "x\nunsafe"}},
            {"updates": {"whitelistMessage": "x" * 501}},
            {"updates": {"notARealOption": 1}},
            {"updates": {}, "motd": ["x\ny"]},
        ):
            with self.assertRaises(ValueError): mod.planned(invalid)
        self.config.write_text("{broken")
        with self.assertRaisesRegex(ValueError, "malformed"): mod.status()

    def test_failed_post_write_validation_restores_original_bytes(self):
        payload = {"updates": {"shouldBanCheaters": True}}
        before = self.config.read_bytes()
        plan, _ = mod.planned(payload)
        original_reader = mod.read_config
        calls = 0

        def failing_reader(path=None):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValueError("fixture post-write failure")
            return original_reader(path)

        mod.read_config = failing_reader
        try:
            with self.assertRaisesRegex(ValueError, "post-write"):
                mod.apply(payload, plan["current_sha256"], "APPLY MOD CONFIG")
        finally:
            mod.read_config = original_reader
        self.assertEqual(before, self.config.read_bytes())

    def test_symlinked_paths_and_oversized_config_are_refused(self):
        self.config.unlink()
        self.config.symlink_to(self.root / "outside.json")
        with self.assertRaisesRegex(ValueError, "symlinked"):
            mod.status()
        self.config.unlink()
        self.config.write_bytes(b" " * (mod.MAX_CONFIG_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "256 KiB"):
            mod.status()

    def test_lua_mod_state_is_discovered_bounded_and_exactly_confirmed(self):
        first = self.mods / "ChatBridge"
        second = self.mods / "DisabledMod"
        shared = self.mods / "shared"
        for directory in (first, second, shared): directory.mkdir()
        (first / "enabled.txt").write_text("1\n")
        (self.mods / "mods.txt").write_text("DisabledMod : 0\n")
        listing = mod.list_lua_mods()
        self.assertEqual(
            [{"name": "ChatBridge", "enabled": True}, {"name": "DisabledMod", "enabled": False}],
            listing["mods"],
        )
        with self.assertRaisesRegex(ValueError, "confirmation"):
            mod.set_lua_mod("DisabledMod", True, "")
        result = mod.set_lua_mod("DisabledMod", True, "SET LUA MOD STATE")
        self.assertTrue(result["enabled"])
        self.assertTrue(mod.parse_mods_txt(self.mods)["DisabledMod"])
        result = mod.set_lua_mod("ChatBridge", False, "SET LUA MOD STATE")
        self.assertFalse(result["enabled"])
        self.assertFalse((first / "enabled.txt").exists())
        with self.assertRaisesRegex(ValueError, "valid Lua mod name"):
            mod.set_lua_mod("../escape", True, "SET LUA MOD STATE")

    def test_browser_contract_exposes_hash_bound_admin_workflow(self):
        html = (ROOT / "admin/static/index.html").read_text()
        javascript = (ROOT / "admin/static/app.js").read_text()
        server = (ROOT / "admin/ops-server.py").read_text()
        for identifier in (
            'id="mods-panel"', 'id="modConfig"', 'id="modConfigMotd"',
            'id="planModConfig"', 'id="applyModConfig"', 'id="luaModList"',
            'id="modLifecycleList"', 'id="modLifecycleExecute"',
        ):
            self.assertIn(identifier, html)
        self.assertIn("expected_sha256: modConfigStatus.paldefender.sha256", javascript)
        self.assertIn('window.prompt("Apply the reviewed PalDefender merge? Type APPLY MOD CONFIG.")', javascript)
        self.assertIn('/api/v1/mod-config/lua-state', javascript)
        self.assertIn('/api/v1/mod-lifecycle/plan', javascript)
        self.assertIn('expected_plan_hash: modLifecyclePlan.plan_hash', javascript)
        self.assertIn('path == "/mod-config"', server)
        self.assertIn('path == "/mod-lifecycle"', server)
        self.assertIn('path in {"/mod-config/plan", "/mod-config/apply"}', server)


if __name__ == "__main__":
    unittest.main()
