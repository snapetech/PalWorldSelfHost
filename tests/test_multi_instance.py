import importlib.util
import json
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "multi_instance", ROOT / "deploy/multi-instance/instance-manager.py")
multi = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(multi)


class MultiInstanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name) / "state"
        self.root.mkdir()
        self.settings = pathlib.Path(self.temporary.name) / "settings.ini"
        self.settings.write_text(
            '[/Script/Pal.PalGameWorldSettings]\n'
            'OptionSettings=(ServerName="Friends",AdminPassword="strong-secret",'
            'PublicPort=8211,RESTAPIEnabled=True,RESTAPIPort=8212,RCONPort=25575)\n')
        self.calls = []

    def tearDown(self):
        self.temporary.cleanup()

    def runner(self, argv, **_kwargs):
        self.calls.append([str(value) for value in argv])
        output = ""
        if "ps" in argv:
            output = json.dumps({"Service": "palworld", "State": "running", "Health": "healthy"})
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    def args(self, name="friends", **changes):
        values = {"name": name, "settings": self.settings, "game_port": 8211,
                  "query_port": 27015, "rest_port": 8212, "rcon_port": 25575,
                  "world_id": None, "start": True}
        values.update(changes)
        return SimpleNamespace(**values)

    def test_create_two_instances_with_isolated_projects_ports_settings_and_saves(self):
        registry = {"version": 1, "instances": {}}
        with mock.patch.object(multi, "port_available", return_value=True):
            first = multi.create(self.root, self.args(), registry, self.runner)
            second = multi.create(self.root, self.args(
                "challenge", game_port=8221, query_port=27025,
                rest_port=8222, rcon_port=25585), registry, self.runner)
        self.assertEqual("created", first["status"])
        self.assertTrue(first["started"])
        loaded = multi.load_registry(self.root)
        self.assertEqual({"friends", "challenge"}, set(loaded["instances"]))
        friends = self.root / "instances/friends"
        challenge = self.root / "instances/challenge"
        self.assertTrue((friends / "saved").is_dir())
        self.assertEqual(0o555, (friends / "entrypoint.sh").stat().st_mode & 0o777)
        self.assertTrue((challenge / "saved").is_dir())
        self.assertNotEqual((friends / "saved").resolve(), (challenge / "saved").resolve())
        compose = (challenge / "compose.yaml").read_text()
        self.assertIn(multi.IMAGE, compose)
        self.assertIn('"8221:8211/udp"', compose)
        self.assertIn('"127.0.0.1:8222:8212/tcp"', compose)
        self.assertIn(f'PALWORLD_WORLD_ID: "{second["instance"]["world_id"]}"', compose)
        self.assertNotEqual(first["instance"]["world_id"], second["instance"]["world_id"])
        self.assertTrue(any("palworldselfhost-friends" in call for command in self.calls for call in command))
        self.assertTrue(any("palworldselfhost-challenge" in call for command in self.calls for call in command))

    def test_duplicate_ports_bound_ports_and_bad_internal_settings_fail_closed(self):
        registry = {"version": 1, "instances": {
            "one": {"game_port": 8211, "query_port": 27015,
                    "rest_port": 8212, "rcon_port": 25575}}}
        with mock.patch.object(multi, "port_available", return_value=True):
            with self.assertRaisesRegex(ValueError, "another managed instance"):
                multi.create(self.root, self.args("two"), registry, self.runner)
        with mock.patch.object(multi, "port_available", return_value=False):
            with self.assertRaisesRegex(ValueError, "already bound"):
                multi.create(self.root, self.args("free", game_port=8301, query_port=8302,
                                                  rest_port=8303, rcon_port=8304),
                             {"version": 1, "instances": {}}, self.runner)
        bad = pathlib.Path(self.temporary.name) / "bad.ini"
        bad.write_text('OptionSettings=(AdminPassword="strong-secret",PublicPort=9999,RESTAPIPort=8212)\n')
        with self.assertRaisesRegex(ValueError, "PublicPort"):
            multi.option_line(bad)
        with self.assertRaisesRegex(ValueError, "world_id"):
            multi.validate_world_id("../not-a-world")

    def test_lifecycle_is_project_scoped_and_delete_preserves_saved_tree(self):
        registry = {"version": 1, "instances": {}}
        with mock.patch.object(multi, "port_available", return_value=True):
            multi.create(self.root, self.args(start=False), registry, self.runner)
        saved = self.root / "instances/friends/saved/world.marker"
        saved.write_text("preserve me")
        for action in ("start", "stop", "restart"):
            result = multi.lifecycle(self.root, registry, "friends", action, self.runner)
            self.assertIn(result["status"], {"started", "stopped", "restarted"})
        observed = multi.status(self.root, registry, "friends", self.runner)
        self.assertEqual("healthy", observed["containers"][0]["Health"])
        with self.assertRaisesRegex(ValueError, "exact confirmation"):
            multi.delete(self.root, registry, "friends", "wrong", self.runner)
        result = multi.delete(self.root, registry, "friends", multi.CONFIRM_DELETE, self.runner)
        preserved = pathlib.Path(result["preserved"])
        self.assertEqual("preserve me", (preserved / "saved/world.marker").read_text())
        self.assertNotIn("friends", multi.load_registry(self.root)["instances"])
        down = next(call for call in reversed(self.calls) if "down" in call)
        self.assertNotIn("--volumes", down)
        self.assertIn("palworldselfhost-friends", down)

    def test_failed_started_create_removes_registry_and_partial_directory(self):
        registry = {"version": 1, "instances": {}}

        def failing_runner(argv, **_kwargs):
            if "up" in argv:
                return SimpleNamespace(returncode=1, stdout="", stderr="port collision")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch.object(multi, "port_available", return_value=True):
            with self.assertRaisesRegex(ValueError, "port collision"):
                multi.create(self.root, self.args(), registry, failing_runner)
        self.assertFalse((self.root / "instances/friends").exists())
        self.assertNotIn("friends", multi.load_registry(self.root)["instances"])


if __name__ == "__main__":
    unittest.main()
