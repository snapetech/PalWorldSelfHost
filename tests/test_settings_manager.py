import json
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]
TEMPLATE = """[/Script/Pal.PalGameWorldSettings]
; fixture comment must survive raw editing
OptionSettings=(Difficulty=None,ExpRate=1.0,PalCaptureRate=1.0,CollectionDropRate=1.0,EnemyDropItemRate=1.0,PalDamageRateAttack=1.0,DeathPenalty=Item,PalEggDefaultHatchingTime=72.0,ServerPlayerMaxNum=32,bIsPvP=False,AutoSaveSpan=30.0,LogFormatType=Text,PhysicsActiveDropItemMaxNum=-1,RandomizerType=None,RandomizerSeed=\"\",bAllowClientMod=True,ServerName=\"fixture\",ServerDescription=\"fixture\",PublicPort=8211,PublicIP=\"\",AdminPassword=\"admin-template\",ServerPassword=\"keep-me\",RCONEnabled=False,RCONPort=25575,RESTAPIEnabled=True,RESTAPIPort=8212)
"""


class SettingsManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.install = self.root / "server"
        self.state = self.root / "state"
        self.backups = self.root / "backups"
        self.install.mkdir()
        self.state.mkdir()
        self.backups.mkdir()
        (self.install / "DefaultPalWorldSettings.ini").write_text(TEMPLATE)
        active = self.install / "Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"
        active.parent.mkdir(parents=True)
        active.write_text(TEMPLATE)
        self.active = active
        self.env_file = self.root / "palworld.env"
        self.env_file.write_text("\n".join([
            f"PALWORLD_INSTALL_DIR={self.install}",
            f"PALWORLD_STATE_DIR={self.state}",
            f"PALWORLD_BACKUP_LOCAL_ROOT={self.backups}",
            "PALWORLD_PORT=8211",
            "PALWORLD_PUBLIC_IP=",
            "PALWORLD_BIND_IP=",
            "PALWORLD_SERVER_NAME=Fixture",
            "PALWORLD_SERVER_DESCRIPTION=Fixture",
            "PALWORLD_PLAYER_EXP_RATE=0.5",
            "PALWORLD_ADMIN_PASSWORD=runtime-admin",
            "PALWORLD_REST_PORT=8212",
        ]) + "\n")
        self.environment = {
            **os.environ,
            "PALWORLD_INSTALL_DIR": str(self.install),
            "PALWORLD_STATE_DIR": str(self.state),
            "PALWORLD_ENV_FILE": str(self.env_file),
        }

    def tearDown(self):
        self.temporary.cleanup()

    def command(self, *arguments, check=True):
        return subprocess.run(
            [str(ROOT / "scripts" / "settings-manager.py"), *arguments],
            env=self.environment, text=True, capture_output=True, timeout=20, check=check,
        )

    def test_schema_has_guidance_presets_and_secret_safe_effective_values(self):
        payload = json.loads(self.command("schema").stdout)
        exp = payload["settings"]["ExpRate"]
        self.assertEqual(exp["category"], "Difficulty and rates")
        self.assertIn("description", exp)
        self.assertEqual(exp["recommended_min"], 0.1)
        self.assertIn("Casual", payload["presets"])
        self.assertEqual(payload["effective"]["AdminPassword"], "<managed>")
        self.assertNotIn("admin-template", json.dumps(payload))

    def test_uncommon_current_settings_have_curated_metadata(self):
        settings = json.loads(self.command("schema").stdout)["settings"]
        self.assertEqual(settings["AutoSaveSpan"]["recommended_min"], 10)
        self.assertEqual(settings["AutoSaveSpan"]["recommended_max"], 600)
        self.assertEqual(settings["AutoSaveSpan"]["unit"], "seconds")
        self.assertEqual(settings["PhysicsActiveDropItemMaxNum"]["recommended_min"], -1)
        self.assertIn("-1 means unlimited", settings["PhysicsActiveDropItemMaxNum"]["description"])
        self.assertEqual(settings["RandomizerType"]["choices"], ["None", "Region", "All"])
        self.assertEqual(settings["LogFormatType"]["choices"], ["Text", "Json"])
        self.assertIn("mods", settings["bAllowClientMod"]["description"])
        self.assertEqual(settings["RandomizerSeed"]["max_length"], 32)
        for item in settings.values():
            self.assertNotIn("Palworld setting", item["description"])

    def test_string_metadata_limit_is_enforced(self):
        process = self.command("plan", json.dumps({"RandomizerSeed": "x" * 33}), check=False)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("at most 32 characters", process.stdout)

    def test_event_restore_only_reverts_touched_settings(self):
        (self.state / "settings-overrides.json").write_text(json.dumps({
            "ExpRate": 2.0, "PalCaptureRate": 2.0, "CollectionDropRate": 3.0,
        }))
        patch = {
            "ExpRate": {"present": True, "value": 1.25},
            "PalCaptureRate": {"present": False, "value": None},
        }
        self.command("restore-patch", json.dumps(patch), "--confirm", "RESTORE EVENT SETTINGS")
        restored = json.loads((self.state / "settings-overrides.json").read_text())
        self.assertEqual(restored["ExpRate"], 1.25)
        self.assertNotIn("PalCaptureRate", restored)
        self.assertEqual(restored["CollectionDropRate"], 3.0)

    def test_raw_plan_apply_and_rollback_preserve_comments_unknowns_and_secrets(self):
        shown = json.loads(self.command("raw-show").stdout)
        self.assertIn("__PALWORLD_MANAGED_SECRET__", shown["content"])
        self.assertNotIn("keep-me", shown["content"])
        proposed = shown["content"].replace("ExpRate=1.0", "ExpRate=2.0")
        plan = json.loads(self.command("raw-plan", proposed).stdout)
        self.assertTrue(plan["changed"])
        self.assertIn("ExpRate=2.0", plan["diff"])
        self.assertNotIn("keep-me", plan["diff"])
        self.command("raw-apply", proposed, "--confirm", "APPLY RAW SETTINGS")
        stored = (self.state / "settings-raw.ini").read_text()
        self.assertIn("; fixture comment must survive raw editing", stored)
        self.assertIn('ServerPassword="keep-me"', stored)
        rendered = self.active.read_text()
        self.assertIn("ExpRate=2.0", rendered)
        self.assertIn('AdminPassword="runtime-admin"', rendered)
        self.assertTrue((self.state / "settings-restart-required.json").is_file())
        subprocess.run(
            [str(ROOT / "scripts" / "settings-loaded.py")], env=self.environment,
            check=True, text=True, capture_output=True,
        )
        self.assertFalse((self.state / "settings-restart-required.json").exists())
        self.assertTrue((self.state / "settings-last-loaded.json").is_file())
        self.command("raw-rollback", "--confirm", "ROLLBACK RAW SETTINGS")
        self.assertIn("ExpRate=1.0", self.active.read_text())

    def test_typed_apply_marks_restart_and_can_restore_previous_overrides(self):
        self.command("apply", json.dumps({"ExpRate": 3.0}), "--confirm", "APPLY SETTINGS")
        self.assertEqual(json.loads((self.state / "settings-overrides.json").read_text()), {"ExpRate": 3.0})
        self.assertIn("ExpRate=3.0", self.active.read_text())
        pending = json.loads((self.state / "settings-restart-required.json").read_text())
        self.assertEqual(pending["source"], "typed")
        self.command("rollback", "--confirm", "ROLLBACK SETTINGS")
        self.assertEqual(json.loads((self.state / "settings-overrides.json").read_text()), {})
        self.assertIn("ExpRate=0.500000", self.active.read_text())

    def test_raw_mode_refuses_removed_protected_fields_and_oversized_input(self):
        shown = json.loads(self.command("raw-show").stdout)["content"]
        removed = shown.replace(',ServerPassword="__PALWORLD_MANAGED_SECRET__"', "")
        process = self.command("raw-plan", removed, check=False)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("cannot be removed", process.stderr)
        process = self.command("raw-plan", shown + ("x" * 50000), check=False)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("exceeds", process.stderr)


if __name__ == "__main__":
    unittest.main()
