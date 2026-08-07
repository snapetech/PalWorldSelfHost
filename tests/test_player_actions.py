import importlib.util
import os
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]


class PlayerActionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        os.environ["PALWORLD_STATE_DIR"] = self.temporary.name
        spec = importlib.util.spec_from_file_location("player_action_test", ROOT / "scripts/player-actions.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.catalog = ([
            {"command": "tp"}, {"command": "getpos"}, {"command": "give"},
            {"command": "givepal"}, {"command": "givestats"}, {"command": "spawnpal"},
        ], True)

    def tearDown(self): self.temporary.cleanup()

    def test_capabilities_fail_closed_to_live_discovery(self):
        with mock.patch.object(self.module.console, "discovered_catalog", return_value=([], False)):
            status = self.module.capabilities()
        self.assertFalse(status["discovery_succeeded"])
        self.assertFalse(any(item["available"] for item in status["actions"].values()))

    def test_typed_plans_are_hash_bound_and_identifier_redacted(self):
        payload = {"action": "teleport", "target": "steam_123", "x": 1.25, "y": -2, "z": 3}
        with mock.patch.object(self.module.console, "discovered_catalog", return_value=self.catalog):
            plan = self.module.plan(payload)
        self.assertEqual(plan["confirmation"], "TELEPORT PLAYER")
        self.assertNotIn("steam_123", str(plan))
        self.assertEqual(plan["verification"], "post-position")
        self.assertEqual(len(plan["reviewed_sha256"]), 64)

    def test_spawn_pal_plan_has_no_player_identity_and_executes_reviewed_coordinates(self):
        payload = {"action": "spawn_pal", "pal_id": "Lamball", "level": 12,
                   "x": 10.25, "y": -20, "z": 3}
        with mock.patch.object(self.module.console, "discovered_catalog", return_value=self.catalog), \
             mock.patch.object(self.module.console, "execute", return_value={"output": "spawned"}) as execute:
            plan = self.module.plan(payload)
            result = self.module.execute(payload, plan["reviewed_sha256"], "SPAWN PAL", "admin")
        self.assertIsNone(plan["target_hash"])
        self.assertNotIn("target", plan["parameters"])
        self.assertEqual("command-acknowledged", result["verification"])
        self.assertIsNone(result["target_hash"])
        self.assertEqual("spawnpal Lamball 10.250 -20.000 3.000 12", execute.call_args.args[0])
        self.assertEqual("player-action", execute.call_args.kwargs["source"])

    def test_browser_contract_starts_disabled_and_binds_plan_before_execute(self):
        html = (ROOT / "admin/static/index.html").read_text()
        javascript = (ROOT / "admin/static/app.js").read_text()
        self.assertRegex(html, r'id="planPlayerAction"[^>]*disabled')
        self.assertRegex(html, r'id="executePlayerAction"[^>]*disabled')
        self.assertIn('api("/api/v1/player-actions/plan"', javascript)
        self.assertIn("expected_sha256: playerActionPlan.reviewed_sha256", javascript)
        self.assertIn("confirmation !== playerActionPlan.confirmation", javascript)
        self.assertIn("playerActionCapabilities = null", javascript)

    def test_refuses_bad_types_ranges_assets_and_unadvertised_commands(self):
        bad = [
            {"action": "teleport", "target": "x", "x": 1, "y": 2},
            {"action": "teleport", "target": "steam_1", "x": float("nan"), "y": 2},
            {"action": "grant_item", "target": "steam_1", "item_id": "../bad", "amount": 1},
            {"action": "grant_pal", "target": "steam_1", "pal_id": "SheepBall", "level": 101},
            {"action": "spawn_pal", "pal_id": "../bad", "level": 1, "x": 1, "y": 2},
            {"action": "spawn_pal", "pal_id": "Lamball", "level": 0, "x": 1, "y": 2},
            {"action": "grant_status", "target": "steam_1", "points": True},
        ]
        for payload in bad:
            with self.subTest(payload=payload), self.assertRaises(ValueError): self.module.normalize(payload)
        with mock.patch.object(self.module.console, "discovered_catalog", return_value=([], False)), self.assertRaisesRegex(ValueError, "did not advertise"):
            self.module.plan({"action": "grant_status", "target": "steam_1", "points": 1})

    def test_execute_requires_exact_confirmation_and_reviewed_hash(self):
        payload = {"action": "grant_item", "target": "steam_123", "item_id": "Wood", "amount": 5}
        with mock.patch.object(self.module.console, "discovered_catalog", return_value=self.catalog):
            plan = self.module.plan(payload)
            with self.assertRaisesRegex(ValueError, "exact confirmation"):
                self.module.execute(payload, plan["reviewed_sha256"], "yes")
            changed = {**payload, "amount": 6}
            with self.assertRaisesRegex(ValueError, "differs"):
                self.module.execute(changed, plan["reviewed_sha256"], "GRANT ITEM")

    def test_application_level_rcon_rejections_are_never_reported_as_acknowledged(self):
        payload = {"action": "spawn_pal", "pal_id": "Anubis", "level": 1, "x": 0, "y": 0, "z": 100}
        with mock.patch.object(self.module.console, "discovered_catalog", return_value=self.catalog), \
             mock.patch.object(self.module.console, "execute", return_value={"output": "Invalid PalID: 'Anubis'."}), \
             self.assertRaisesRegex(RuntimeError, "rejected the command"):
            plan = self.module.plan(payload)
            self.module.execute(payload, plan["reviewed_sha256"], "SPAWN PAL", "admin")

    def test_execute_captures_position_and_uses_private_audited_console(self):
        payload = {"action": "teleport", "target": "steam_123", "x": 10, "y": 20, "z": 30}
        positions = ["X=1 Y=2 Z=3", "X=10 Y=20 Z=30"]
        with mock.patch.object(self.module.console, "discovered_catalog", return_value=self.catalog), \
             mock.patch.object(self.module.console.rcon, "execute", side_effect=positions), \
             mock.patch.object(self.module.console, "execute", return_value={"output": "teleported"}) as execute:
            plan = self.module.plan(payload)
            result = self.module.execute(payload, plan["reviewed_sha256"], "TELEPORT PLAYER", "admin")
        self.assertEqual(result["before_position"], {"x": 1.0, "y": 2.0, "z": 3.0})
        self.assertEqual(result["after_position"], {"x": 10.0, "y": 20.0, "z": 30.0})
        self.assertEqual(result["verification"], "destination-observed")
        self.assertEqual(result["destination_delta_xy"], 0)
        command = execute.call_args.args[0]
        self.assertEqual(command, "tp steam_123 10.000 20.000 30.000")
        self.assertEqual(execute.call_args.kwargs["source"], "player-action")
        audit = (pathlib.Path(self.temporary.name) / "audit.jsonl").read_text()
        self.assertNotIn("steam_123", audit)

    def test_teleport_fails_when_observed_position_misses_reviewed_destination(self):
        payload = {"action": "teleport", "target": "steam_123", "x": 10, "y": 20, "z": 0}
        positions = ["X=1 Y=2 Z=3", "X=9999 Y=9999 Z=3"]
        with mock.patch.object(self.module.console, "discovered_catalog", return_value=self.catalog), \
             mock.patch.object(self.module.console.rcon, "execute", side_effect=positions), \
             mock.patch.object(self.module.console, "execute", return_value={"output": "teleported"}), \
             self.assertRaisesRegex(RuntimeError, "did not reach"):
            plan = self.module.plan(payload)
            self.module.execute(payload, plan["reviewed_sha256"], "TELEPORT PLAYER", "admin")
        audit = (pathlib.Path(self.temporary.name) / "audit.jsonl").read_text()
        self.assertIn('"result": "failed"', audit)


if __name__ == "__main__": unittest.main()
