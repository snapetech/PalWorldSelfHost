import importlib.util
import os
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]


class SteamBuildManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = pathlib.Path(cls.temporary.name)
        cls.install = cls.root / "server"
        cls.state = cls.root / "state"
        cls.backups = cls.root / "backups"
        cls.snapshots = cls.root / "builds"
        cls.install.mkdir(); cls.state.mkdir(); cls.backups.mkdir()
        (cls.install / "Pal/Binaries/Linux").mkdir(parents=True)
        (cls.install / "Pal/Saved/SaveGames/0/world").mkdir(parents=True)
        (cls.install / "PalServer.sh").write_text("build one launcher")
        (cls.install / "Pal/Binaries/Linux/PalServer-Linux-Shipping").write_text("build one binary")
        (cls.install / "Pal/Saved/SaveGames/0/world/Level.sav").write_text("save one")
        (cls.root / "steamapps").mkdir()
        cls.manifest = cls.root / "steamapps/appmanifest_2394010.acf"
        cls.manifest.write_text('"AppState" { "appid" "2394010" "buildid" "100" }')
        os.environ.update({
            "PALWORLD_INSTALL_DIR": str(cls.install),
            "PALWORLD_STATE_DIR": str(cls.state),
            "PALWORLD_BACKUP_LOCAL_ROOT": str(cls.backups),
            "PALWORLD_BUILD_SNAPSHOT_ROOT": str(cls.snapshots),
        })
        spec = importlib.util.spec_from_file_location(
            "steam_build_manager_test", ROOT / "scripts" / "steam-build-manager.py",
        )
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_snapshot_and_restore_replace_executables_but_never_saved_world(self):
        snapshot = self.module.create_snapshot()
        self.assertEqual(snapshot["build_id"], "100")
        self.assertFalse((pathlib.Path(snapshot["path"]) / "tree/Pal/Saved").exists())
        (self.install / "PalServer.sh").write_text("build two launcher")
        (self.install / "Pal/Binaries/Linux/PalServer-Linux-Shipping").write_text("build two binary")
        (self.install / "Pal/Saved/SaveGames/0/world/Level.sav").write_text("save two")
        self.manifest.write_text('"AppState" { "appid" "2394010" "buildid" "200" }')
        restored = self.module.restore_snapshot("100")
        self.assertEqual(restored["build_id"], "100")
        self.assertEqual((self.install / "PalServer.sh").read_text(), "build one launcher")
        self.assertEqual((self.install / "Pal/Saved/SaveGames/0/world/Level.sav").read_text(), "save two")
        self.assertEqual(self.module.build_id(), "100")

    def test_snapshot_verification_rejects_critical_file_tampering(self):
        record = self.module.find_snapshot("100")
        critical = pathlib.Path(record["path"]) / "tree/PalServer.sh"
        original = critical.read_text()
        critical.write_text("tampered")
        try:
            with self.assertRaisesRegex(ValueError, "critical-file mismatch"):
                self.module.verify_snapshot(record)
        finally:
            critical.write_text(original)

    def test_pin_only_accepts_installed_or_snapshotted_builds(self):
        pin = self.module.set_pin("100")
        self.assertEqual(pin["build_id"], "100")
        with self.assertRaisesRegex(ValueError, "installed or locally snapshotted"):
            self.module.set_pin("999")


if __name__ == "__main__":
    unittest.main()
