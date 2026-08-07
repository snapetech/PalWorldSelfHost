import importlib.util
import pathlib
import sqlite3
import unittest


ROOT = pathlib.Path(__file__).parents[1]


class CollectHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "collect_history_test", ROOT / "scripts" / "collect-history.py",
        )
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_presence_diff_emits_join_leave_and_retains_session_history(self):
        database = sqlite3.connect(":memory:")
        audited = []
        original_audit = self.module.ops.audit
        self.module.ops.audit = lambda action, result, **details: audited.append((action, result, details))
        try:
            events = self.module.track_players(
                database, [{"userId": "alice-id", "name": "Alice"}], 1000, emit=False,
            )
            self.assertEqual(events, [])
            events = self.module.track_players(database, [
                {"userId": "alice-id", "name": "Alice"},
                {"userId": "bob-id", "name": "Bob"},
            ], 1060)
            self.assertEqual(events[0][:3], ("join", "bob-id", "Bob"))
            events = self.module.track_players(
                database, [{"userId": "bob-id", "name": "Bob"}], 1120,
            )
            self.assertEqual(events[0], ("leave", "alice-id", "Alice", 120))
            alice = database.execute(
                "select first_seen,last_seen,sessions,total_seconds,online from player_presence where user_key='alice-id'"
            ).fetchone()
            self.assertEqual(alice, (1000, 1120, 1, 120, 0))
            self.assertEqual([event[0] for event in audited], ["player.join", "player.leave"])
        finally:
            self.module.ops.audit = original_audit
            database.close()

    def test_player_identity_never_uses_network_address(self):
        key, name = self.module.player_identity({
            "userId": "platform-id", "name": "Player", "ip": "192.0.2.1",
        })
        self.assertEqual((key, name), ("platform-id", "Player"))


if __name__ == "__main__":
    unittest.main()
