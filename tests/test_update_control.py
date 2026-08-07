import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]


class UpdateControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        os.environ["PALWORLD_STATE_DIR"] = cls.temporary.name
        spec = importlib.util.spec_from_file_location(
            "update_control_test", ROOT / "scripts" / "update-control.py",
        )
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_check_persists_first_seen_and_current_build_state(self):
        with mock.patch.object(self.module, "run_json", return_value={
            "local_build": "100", "remote_build": "101", "update_available": True,
        }):
            first = self.module.check()
            second = self.module.check()
        self.assertEqual(first["first_seen_at"], second["first_seen_at"])
        persisted = json.loads((pathlib.Path(self.temporary.name) / "update.json").read_text())
        self.assertEqual(persisted["remote_build"], "101")

    def test_plan_binds_execution_to_numeric_remote_build_and_reports_players(self):
        responses = [
            {"local_build": "100", "remote_build": "101", "update_available": True},
            {"pin": {}, "snapshots": []},
            {"players": [{"name": "Player"}]},
            {"current_build": "100", "pin": {}, "snapshots": [
                {"build_id": "99"}, {"build_id": "100"},
            ]},
        ]
        with mock.patch.object(self.module, "run_json", side_effect=responses):
            plan = self.module.plan()
        self.assertTrue(plan["executable"])
        self.assertEqual(plan["target_build"], "101")
        self.assertEqual(plan["players_online"], 1)
        self.assertTrue(any("protected backup" in step for step in plan["steps"]))
        self.assertTrue(plan["rollback_available"])
        self.assertEqual(plan["rollback_builds"], ["100", "99"])
        self.assertTrue(any("automatically restore" in step for step in plan["steps"]))
        self.assertIn("preserves Pal/Saved", plan["rollback_note"])

    def test_privileged_wrapper_rechecks_target_and_requires_exact_confirmation(self):
        script = (ROOT / "scripts" / "ops-update.sh").read_text()
        self.assertIn('"$4" == "INSTALL UPDATE"', script)
        self.assertIn('"$remote" == "$target"', script)
        self.assertIn('"$SCRIPT_DIR/maintenance.sh" update', script)
        self.assertIn("minutes * 60", script)


if __name__ == "__main__":
    unittest.main()
