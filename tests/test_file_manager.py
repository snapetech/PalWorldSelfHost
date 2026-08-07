import hashlib
import importlib.util
import os
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("file_manager", ROOT / "scripts" / "file-manager.py")
file_manager = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(file_manager)


class FileManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.root = pathlib.Path(self.temporary.name)
        self.install = self.root / "server"; self.state = self.root / "state"
        for path in (self.install / "Pal/Saved/Config/LinuxServer", self.install / "Pal/Content/Paks", self.install / "Pal/Saved/Logs", self.state): path.mkdir(parents=True)
        self.old = os.environ.get("PALWORLD_INSTALL_DIR"), os.environ.get("PALWORLD_STATE_DIR")
        os.environ["PALWORLD_INSTALL_DIR"] = str(self.install); os.environ["PALWORLD_STATE_DIR"] = str(self.state)

    def tearDown(self):
        for key, value in zip(("PALWORLD_INSTALL_DIR", "PALWORLD_STATE_DIR"), self.old):
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value
        self.temporary.cleanup()

    def test_list_read_atomic_replace_preserves_prior_file(self):
        target = self.install / "Pal/Saved/Config/LinuxServer/Engine.ini"; target.write_text("old\n")
        source = self.root / "new.ini"; source.write_text("new\n")
        plan = file_manager.plan_upload("config", "Engine.ini", source)
        self.assertEqual(hashlib.sha256(b"new\n").hexdigest(), plan["after"]["sha256"])
        result = file_manager.upload("config", "Engine.ini", source, plan["after"]["sha256"], "UPLOAD FILE")
        self.assertEqual("new\n", target.read_text())
        self.assertEqual("old\n", pathlib.Path(result["preserved"]).read_text())
        self.assertEqual("new\n", file_manager.read_text("config", "Engine.ini")["content"])
        self.assertEqual("Engine.ini", file_manager.listing("config")["entries"][0]["name"])

    def test_traversal_symlinks_extensions_and_hash_mismatch_are_refused(self):
        source = self.root / "payload.exe"; source.write_bytes(b"x")
        with self.assertRaisesRegex(ValueError, "parent"):
            file_manager.plan_upload("mods", "../escape.pak", source)
        with self.assertRaisesRegex(ValueError, "extension"):
            file_manager.plan_upload("mods", "payload.exe", source)
        link = self.install / "Pal/Content/Paks/link"; link.symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, "symlink"):
            file_manager.plan_upload("mods", "link/file.pak", source)
        pak = self.root / "file.pak"; pak.write_bytes(b"pak")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            file_manager.upload("mods", "file.pak", pak, "0" * 64, "UPLOAD FILE")

    def test_quarantine_is_reversible_and_readonly_root_refuses_writes(self):
        target = self.install / "Pal/Content/Paks/mod.pak"; target.write_bytes(b"pak")
        result = file_manager.quarantine("mods", "mod.pak", "QUARANTINE FILE")
        self.assertFalse(target.exists()); self.assertEqual(b"pak", pathlib.Path(result["quarantine"]).read_bytes())
        source = self.root / "server.log"; source.write_text("log")
        with self.assertRaisesRegex(ValueError, "read-only"):
            file_manager.plan_upload("logs", "server.log", source)

    def test_first_write_creates_missing_state_directory(self):
        self.state.rmdir()
        source = self.root / "first.ini"; source.write_text("first\n")
        digest = hashlib.sha256(b"first\n").hexdigest()
        result = file_manager.upload("config", "first.ini", source, digest, "UPLOAD FILE")
        self.assertEqual("installed", result["status"])
        self.assertTrue((self.state / "file-manager.lock").is_file())


if __name__ == "__main__": unittest.main()
