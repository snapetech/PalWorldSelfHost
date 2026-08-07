import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
import urllib.error


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("game_data", ROOT / "scripts" / "game-data-poller.py")
game_data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(game_data)


class GameDataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.temporary.name) / "game-data.json"
        self.original = os.environ.get("PALWORLD_GAME_DATA_ENABLED")

    def tearDown(self):
        if self.original is None:
            os.environ.pop("PALWORLD_GAME_DATA_ENABLED", None)
        else:
            os.environ["PALWORLD_GAME_DATA_ENABLED"] = self.original
        self.temporary.cleanup()

    def snapshot(self, actors=None):
        return {"Time": "12:00", "FPS": 60.0, "AverageFPS": 58.5, "ActorData": actors or [{
            "Type": "Character", "UnitType": "BaseCampPal", "InstanceID": "secret-instance",
            "userid": "secret-user", "ip": "192.0.2.1", "NickName": "Worker", "Class": "BOSS_ElecCat_C",
            "Action": "Work_Mining", "IsActive": "true", "HP": 80, "MaxHP": 100,
            "level": 12, "LocationX": 1.0, "LocationY": 2.0, "LocationZ": 3.0,
        }]}

    def test_projection_is_bounded_and_drops_sensitive_fields(self):
        projection = game_data.project_snapshot(self.snapshot())
        self.assertEqual(1, projection["counts"]["base_pals"])
        actor = projection["actors"][0]
        self.assertEqual("ElecCat", actor["character_id"])
        self.assertTrue(actor["boss"])
        self.assertEqual("working", actor["activity"])
        self.assertEqual(80.0, actor["hp_percent"])
        encoded = json.dumps(projection)
        self.assertNotIn("secret-instance", encoded)
        self.assertNotIn("secret-user", encoded)
        self.assertNotIn("192.0.2.1", encoded)

    def test_disabled_state_never_fetches(self):
        os.environ["PALWORLD_GAME_DATA_ENABLED"] = "false"
        result = game_data.poll(self.path, fetch=lambda: self.fail("fetch called"), now=10)
        self.assertEqual("disabled", result["state"])

    def test_404_is_explicitly_unsupported(self):
        os.environ["PALWORLD_GAME_DATA_ENABLED"] = "true"
        def missing():
            raise urllib.error.HTTPError("http://127.0.0.1", 404, "Not Found", {}, None)
        result = game_data.poll(self.path, fetch=missing, now=20)
        self.assertEqual("unsupported", result["state"])
        self.assertEqual(404, result["http_status"])

    def test_first_population_collapse_keeps_last_good(self):
        os.environ["PALWORLD_GAME_DATA_ENABLED"] = "true"
        many = [dict(self.snapshot()["ActorData"][0], LocationX=float(i)) for i in range(20)]
        game_data.poll(self.path, fetch=lambda: self.snapshot(many), now=30)
        result = game_data.poll(self.path, fetch=lambda: self.snapshot([many[0]]), now=40)
        self.assertEqual("stale", result["state"])
        self.assertEqual(20, result["source_actor_count"])
        self.assertTrue(result["collapse_pending"])
        result = game_data.poll(self.path, fetch=lambda: self.snapshot([many[0]]), now=50)
        self.assertEqual("ready", result["state"])
        self.assertEqual(1, result["source_actor_count"])

    def test_nonfinite_coordinates_are_rejected(self):
        actor = dict(self.snapshot()["ActorData"][0], LocationX=float("nan"))
        with self.assertRaisesRegex(ValueError, "finite"):
            game_data.project_snapshot(self.snapshot([actor]))


if __name__ == "__main__":
    unittest.main()
