import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("save_import", ROOT / "scripts" / "save-import.py")
save_import = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(save_import)
WORLD = "A" * 32
PLAYER = "C" * 32


class FakeRunner:
    def __init__(self, info_ok=True): self.commands = []; self.info_ok = info_ok
    def __call__(self, argv, **kwargs):
        self.commands.append([str(item) for item in argv])
        if argv[:4] == ["systemctl", "is-active", "--quiet", "palworld.service"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if str(argv[-1]) == "players":
            return subprocess.CompletedProcess(argv, 0, json.dumps({"players": []}), "")
        if str(argv[-1]) == "info":
            return subprocess.CompletedProcess(argv, 0 if self.info_ok else 1, "{}" if self.info_ok else "", "not ready")
        return subprocess.CompletedProcess(argv, 0, "ok\n", "")


class SaveImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.root = pathlib.Path(self.temporary.name)
        self.install = self.root / "server"; self.state = self.root / "state"; self.source = self.root / "incoming"
        self.target = self.install / "Pal/Saved/SaveGames/0" / WORLD
        self.target.mkdir(parents=True); (self.target / "Level.sav").write_bytes(b"old-world")
        settings = self.install / "Pal/Saved/Config/LinuxServer/GameUserSettings.ini"
        settings.parent.mkdir(parents=True); settings.write_text(f"[/Script/Pal.PalGameLocalSettings]\nDedicatedServerName={WORLD}\n")
        (self.source / "Players").mkdir(parents=True)
        (self.source / "Level.sav").write_bytes(b"new-world")
        (self.source / "LocalData.sav").write_bytes(b"local")
        (self.source / f"Players/{PLAYER}.sav").write_bytes(b"player")

    def tearDown(self): self.temporary.cleanup()

    def test_plan_and_protected_import_select_and_preserve_world(self):
        plan = save_import.import_plan(self.source, self.install, self.state)
        self.assertEqual("co-op", plan["source_kind"]); self.assertEqual(1, plan["players"])
        runner = FakeRunner()
        with mock.patch.object(save_import, "chown_tree"):
            result = save_import.execute(self.source, self.install, self.state, None, plan["fingerprint"], "IMPORT WORLD", runner=runner, require_root=False)
        self.assertEqual("imported", result["status"])
        self.assertEqual(b"new-world", (self.target / "Level.sav").read_bytes())
        self.assertEqual(b"old-world", (pathlib.Path(result["preserved_target"]) / "Level.sav").read_bytes())
        self.assertTrue(any(command[-1] == "protected" for command in runner.commands))
        self.assertTrue(any(command == ["systemctl", "start", "palworld.service"] for command in runner.commands))

    def test_failed_health_check_restores_prior_world_and_selection(self):
        plan = save_import.import_plan(self.source, self.install, self.state)
        runner = FakeRunner(info_ok=False)
        with mock.patch.object(save_import, "chown_tree"), mock.patch.object(save_import.time, "sleep"):
            with self.assertRaisesRegex(ValueError, "startup verification"):
                save_import.execute(self.source, self.install, self.state, None, plan["fingerprint"], "IMPORT WORLD", runner=runner, require_root=False)
        self.assertEqual(b"old-world", (self.target / "Level.sav").read_bytes())
        settings = self.install / "Pal/Saved/Config/LinuxServer/GameUserSettings.ini"
        self.assertIn(f"DedicatedServerName={WORLD}", settings.read_text())

    def test_source_change_and_ambiguous_target_are_refused(self):
        plan = save_import.import_plan(self.source, self.install, self.state)
        (self.source / "Level.sav").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            save_import.execute(self.source, self.install, self.state, None, plan["fingerprint"], "IMPORT WORLD", runner=FakeRunner(), require_root=False)
        second = self.install / "Pal/Saved/SaveGames/0" / ("B" * 32)
        second.mkdir(); (second / "Level.sav").write_bytes(b"second")
        with self.assertRaisesRegex(ValueError, "--target-world"):
            save_import.import_plan(self.source, self.install, self.state)

    def test_unexpected_source_files_are_refused(self):
        (self.source / "run-me.py").write_text("pass\n")
        with self.assertRaisesRegex(ValueError, "unexpected world file"):
            save_import.import_plan(self.source, self.install, self.state)


if __name__ == "__main__": unittest.main()
