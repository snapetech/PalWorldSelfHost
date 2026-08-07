import importlib.util
import json
import os
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("adopt_existing", ROOT / "scripts" / "adopt-existing.py")
adopt_existing = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(adopt_existing)


class AdoptExistingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.root = pathlib.Path(self.temporary.name)
        self.install = self.root / "server"; self.state = self.root / "state"
        files = {
            "PalServer.sh": "#!/bin/sh\n",
            "Pal/Binaries/Linux/PalServer-Linux-Shipping": "server\n",
            "DefaultPalWorldSettings.ini": "[/Script/Pal.PalGameWorldSettings]\nOptionSettings=(ServerName=\"Default\")\n",
            "Pal/Saved/Config/LinuxServer/PalWorldSettings.ini": "[/Script/Pal.PalGameWorldSettings]\nOptionSettings=(ServerName=\"Existing\")\n",
            "steamapps/appmanifest_2394010.acf": '"AppState" { "appid" "2394010" "buildid" "12345" }\n',
            "Pal/Saved/SaveGames/0/world/Level.sav": "world",
            "Pal/Saved/SaveGames/0/world/Players/player.sav": "player",
        }
        for relative, content in files.items():
            path = self.install / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content)
        os.chmod(self.install / "PalServer.sh", 0o755)
        os.chmod(self.install / "Pal/Binaries/Linux/PalServer-Linux-Shipping", 0o755)

    def tearDown(self):
        self.temporary.cleanup()

    def test_plan_and_exact_adoption_preserve_existing_settings(self):
        plan = adopt_existing.adoption_plan(self.install, self.state)
        self.assertEqual("ready", plan["status"])
        self.assertEqual("12345", plan["build_id"])
        self.assertEqual(1, plan["worlds"][0]["players"])
        self.assertFalse(plan["game_files_reinstalled"])
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            adopt_existing.adopt(self.install, self.state, "0" * 64, "ADOPT EXISTING INSTALL")
        result = adopt_existing.adopt(self.install, self.state, plan["fingerprint"], "ADOPT EXISTING INSTALL")
        self.assertEqual("adopted", result["status"])
        self.assertIn("Existing", (self.state / "settings-raw.ini").read_text())
        receipt = json.loads((self.state / "adoption-receipt.json").read_text())
        self.assertEqual(plan["fingerprint"], receipt["fingerprint"])
        self.assertTrue(pathlib.Path(receipt["preserved_settings"]).is_file())

    def test_changed_tree_and_symlinked_critical_file_are_refused(self):
        plan = adopt_existing.adoption_plan(self.install, self.state)
        (self.install / "PalServer.sh").write_text("#!/bin/sh\nchanged\n")
        os.chmod(self.install / "PalServer.sh", 0o755)
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            adopt_existing.adopt(self.install, self.state, plan["fingerprint"], "ADOPT EXISTING INSTALL")
        server = self.install / "Pal/Binaries/Linux/PalServer-Linux-Shipping"
        server.unlink(); server.symlink_to(self.install / "PalServer.sh")
        with self.assertRaisesRegex(ValueError, "symlink|regular file"):
            adopt_existing.adoption_plan(self.install, self.state)

    def test_overlapping_state_tree_is_refused(self):
        with self.assertRaisesRegex(ValueError, "state directory"):
            adopt_existing.adoption_plan(self.install, self.install / "toolkit-state")

    def test_completed_adoption_can_resume_only_while_critical_files_match(self):
        plan = adopt_existing.adoption_plan(self.install, self.state)
        adopt_existing.adopt(self.install, self.state, plan["fingerprint"], "ADOPT EXISTING INSTALL")
        verified = adopt_existing.verify_receipt(self.install, self.state, plan["fingerprint"])
        self.assertEqual("verified", verified["status"])
        self.assertFalse(verified["game_files_reinstalled"])

        (self.install / "Pal/Saved/Config/LinuxServer/PalWorldSettings.ini").write_text("runtime settings changed")
        self.assertEqual(
            "verified",
            adopt_existing.verify_receipt(self.install, self.state, plan["fingerprint"])["status"],
        )
        (self.install / "PalServer.sh").write_text("#!/bin/sh\nchanged\n")
        os.chmod(self.install / "PalServer.sh", 0o755)
        with self.assertRaisesRegex(ValueError, "changed"):
            adopt_existing.verify_receipt(self.install, self.state, plan["fingerprint"])


if __name__ == "__main__": unittest.main()
