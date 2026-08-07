import importlib.util
import os
import pathlib
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("auto_pause", ROOT / "scripts" / "auto-pause.py")
auto_pause = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(auto_pause)


class AutoPauseTests(unittest.TestCase):
    def test_disabled_and_roster_failure_never_pause(self):
        action, state = auto_pause.decision(100, {"empty_since": 1}, enabled=False, service_active=True, players=0)
        self.assertEqual("none", action["action"]); self.assertEqual("disabled", state["status"])
        action, state = auto_pause.decision(100, {"empty_since": 1}, enabled=True, service_active=True, players=None)
        self.assertEqual("none", action["action"]); self.assertNotIn("empty_since", state)

    def test_empty_countdown_becomes_due_and_players_reset_it(self):
        with mock.patch.dict(os.environ, {"PALWORLD_AUTO_PAUSE_EMPTY_MINUTES": "5"}):
            action, state = auto_pause.decision(100, {}, enabled=True, service_active=True, players=0)
            self.assertEqual(300, action["remaining_seconds"])
            action, state = auto_pause.decision(401, state, enabled=True, service_active=True, players=0)
            self.assertEqual("pause", action["action"])
            action, state = auto_pause.decision(402, state, enabled=True, service_active=True, players=2)
            self.assertEqual("active", state["status"]); self.assertNotIn("empty_since", state)

    def test_paused_state_and_on_demand_resume_get_fresh_idle_window(self):
        _, state = auto_pause.decision(500, {"auto_paused": True}, enabled=True, service_active=False, players=None)
        self.assertEqual("paused", state["status"])
        action, state = auto_pause.decision(600, state, enabled=True, service_active=True, players=0)
        self.assertEqual("counting-down", action["status"])
        self.assertEqual(600, state["resumed_at"]); self.assertEqual(600, state["empty_since"])


if __name__ == "__main__": unittest.main()
