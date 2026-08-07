import importlib.util
import json
import os
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.state = pathlib.Path(self.temporary.name)
        self.original_state = os.environ.get("PALWORLD_STATE_DIR")
        os.environ["PALWORLD_STATE_DIR"] = str(self.state)
        spec = importlib.util.spec_from_file_location(f"scheduler_test_{id(self)}", ROOT / "scripts/scheduler.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def tearDown(self):
        if self.original_state is None:
            os.environ.pop("PALWORLD_STATE_DIR", None)
        else:
            os.environ["PALWORLD_STATE_DIR"] = self.original_state
        self.temporary.cleanup()

    def test_timed_event_captures_touched_values_and_automatically_restores(self):
        (self.state / "settings-overrides.json").write_text(json.dumps({"ExpRate": 1.5, "UnrelatedRate": 3.0}))
        jobs = [{
            "id": "fixture", "enabled": True, "type": "settings_event", "preset": "double_xp",
            "duration_seconds": 3600, "due_at": 1000, "phase": "pending",
        }]
        (self.state / "jobs.json").write_text(json.dumps(jobs))
        commands = []
        self.module.execute = lambda command: commands.append(command) or 0
        active = self.module.run_due_jobs(1000)[0]
        self.assertEqual(active["phase"], "active")
        self.assertEqual(active["next_run"], 4600)
        self.assertEqual(active["restore_patch"], {"ExpRate": {"present": True, "value": 1.5}})
        self.assertTrue(any("APPLY SETTINGS" in command for command in commands))
        restored = self.module.run_due_jobs(4600)[0]
        self.assertFalse(restored["enabled"])
        self.assertEqual(restored["phase"], "restored")
        self.assertNotIn("restore_patch", restored)
        self.assertTrue(any("RESTORE EVENT SETTINGS" in command for command in commands))

    def test_active_event_cancellation_restores_even_before_deadline(self):
        jobs = [{
            "id": "fixture", "enabled": True, "type": "settings_event", "preset": "resource_rush",
            "duration_seconds": 86400, "due_at": 1000, "phase": "active", "next_run": 90000,
            "cancel_requested": True, "restore_patch": {"CollectionDropRate": {"present": False, "value": None}},
        }]
        (self.state / "jobs.json").write_text(json.dumps(jobs))
        self.module.execute = lambda command: 0
        restored = self.module.run_due_jobs(1200)[0]
        self.assertEqual(restored["phase"], "restored")
        self.assertFalse(restored["enabled"])

    def test_rcon_jobs_execute_and_repeat(self):
        job = {
            "id": "rcon-fixture", "enabled": True, "type": "rcon", "command": "Info",
            "due_at": 100, "interval_seconds": 300,
        }
        self.module.ops.atomic_json(self.module.JOBS_FILE, [job])
        calls = []
        self.module.execute = lambda arguments: calls.append(arguments) or 0
        jobs = self.module.run_due_jobs(now=100)
        self.assertTrue(jobs[0]["enabled"])
        self.assertEqual(jobs[0]["next_run"], 400)
        self.assertTrue(any(str(item).endswith("rcon-console.py") for item in calls[0]))
        self.assertIn("EXECUTE RCON", calls[0][-1])


if __name__ == "__main__":
    unittest.main()
