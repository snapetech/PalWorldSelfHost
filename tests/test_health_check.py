import importlib.util
import os
import pathlib
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]


class HealthRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "health_check_test", ROOT / "scripts" / "health-check.py",
        )
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def decide(self, now, state, **values):
        defaults = {
            "service_active": True, "rest_ok": True, "uptime": now,
            "rss_bytes": 1024, "players_online": 0,
        }
        return self.module.recovery_decision(now, state, **{**defaults, **values})

    def test_rest_failure_requires_consecutive_samples_before_forced_restart(self):
        with mock.patch.dict(os.environ, {
            "PALWORLD_STALL_FAILURE_THRESHOLD": "3",
            "PALWORLD_HEALTH_RECOVERY_ENABLED": "true",
        }, clear=False):
            state = {}
            for now in (10_000, 10_300):
                decision, state = self.decide(now, state, rest_ok=False, uptime=None)
                self.assertEqual(decision["action"], "none")
            decision, state = self.decide(10_600, state, rest_ok=False, uptime=None)
            self.assertEqual(decision["action"], "restart")
            self.assertEqual(decision["mode"], "forced")
            self.assertEqual(state["stall_streak"], 3)

    def test_unchanged_world_uptime_is_treated_as_a_stall_but_advancement_resets_it(self):
        with mock.patch.dict(os.environ, {"PALWORLD_STALL_FAILURE_THRESHOLD": "2"}, clear=False):
            decision, state = self.decide(20_000, {}, uptime=500)
            self.assertEqual(decision["action"], "none")
            decision, state = self.decide(20_300, state, uptime=500)
            self.assertEqual(decision["action"], "none")
            decision, state = self.decide(20_600, state, uptime=500)
            self.assertEqual(decision["action"], "restart")
            decision, state = self.decide(20_900, state, uptime=800)
            self.assertEqual(decision["action"], "none")
            self.assertEqual(state["stall_streak"], 0)

    def test_memory_recovery_defers_for_players_and_is_graceful_when_empty(self):
        environment = {
            "PALWORLD_MEMORY_RESTART_GIB": "8",
            "PALWORLD_MEMORY_RESTART_DEFER_PLAYERS": "true",
            "PALWORLD_HEALTH_RECOVERY_ENABLED": "true",
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            decision, state = self.decide(
                30_000, {}, rss_bytes=9 * 1024**3, players_online=2,
            )
            self.assertEqual(decision["action"], "defer")
            decision, _ = self.decide(
                30_300, state, rss_bytes=9 * 1024**3, players_online=0,
            )
            self.assertEqual(decision["action"], "restart")
            self.assertEqual(decision["mode"], "graceful")

    def test_cooldown_and_window_limit_stop_restart_loops(self):
        environment = {
            "PALWORLD_STALL_FAILURE_THRESHOLD": "2",
            "PALWORLD_RECOVERY_COOLDOWN_MINUTES": "30",
            "PALWORLD_RECOVERY_WINDOW_HOURS": "6",
            "PALWORLD_RECOVERY_MAX_RESTARTS": "2",
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            state = {
                "stall_streak": 1, "last_recovery_at": 40_000,
                "last_checked_at": 40_000, "recoveries": [38_000],
            }
            decision, _ = self.decide(40_300, state, rest_ok=False, uptime=None)
            self.assertEqual(decision["action"], "cooldown")
            state.update({"last_recovery_at": 30_000, "recoveries": [38_000, 39_000]})
            decision, _ = self.decide(41_000, state, rest_ok=False, uptime=None)
            self.assertEqual(decision["action"], "blocked")


if __name__ == "__main__":
    unittest.main()
