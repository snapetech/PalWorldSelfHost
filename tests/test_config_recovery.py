import json
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]
WORLD = """[/Script/Pal.PalGameWorldSettings]
OptionSettings=(Difficulty=None,ExpRate=1.0,DeathPenalty=Item,ServerName="fixture",ServerDescription="fixture",PublicPort=8211,PublicIP="",AdminPassword="admin",ServerPassword="",RCONEnabled=False,RCONPort=25575,RESTAPIEnabled=True,RESTAPIPort=8212)
"""


class ConfigRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.install = self.root / "install"; self.state = self.root / "state"
        self.config = self.install / "Pal/Saved/Config/LinuxServer"
        self.config.mkdir(parents=True); self.state.mkdir()
        (self.install / "DefaultPalWorldSettings.ini").write_text(WORLD)
        (self.config / "PalWorldSettings.ini").write_text("[broken\n")
        (self.config / "Engine.ini").write_text("[/Script/Engine.Engine]\nbSmoothFrameRate=False\n")
        world = self.install / "Pal/Saved/SaveGames/0/ABCDEF"
        world.mkdir(parents=True); (world / "WorldOptions.sav").write_bytes(b"fixture")
        self.env_file = self.root / "palworld.env"
        self.env_file.write_text("\n".join([
            f"PALWORLD_INSTALL_DIR={self.install}", f"PALWORLD_STATE_DIR={self.state}",
            f"PALWORLD_BACKUP_LOCAL_ROOT={self.root / 'backups'}", "PALWORLD_PORT=8211",
            "PALWORLD_REST_PORT=8212", "PALWORLD_OPS_PORT=8213", "PALWORLD_RCON_PORT=25575",
            "PALWORLD_ADMIN_PASSWORD=admin",
        ]) + "\n")
        self.environment = {**os.environ, "PALWORLD_INSTALL_DIR": str(self.install), "PALWORLD_STATE_DIR": str(self.state),
                            "PALWORLD_BACKUP_LOCAL_ROOT": str(self.root / "backups"), "PALWORLD_ENV_FILE": str(self.env_file),
                            "PALWORLD_PORT": "8211", "PALWORLD_REST_PORT": "8212", "PALWORLD_OPS_PORT": "8213",
                            "PALWORLD_RCON_PORT": "25575", "PALWORLD_ADMIN_PASSWORD": "admin",
                            "PALWORLD_ASSUME_SERVICE_STOPPED": "true", "PALWORLD_SERVER_NAME": "Fixture",
                            "PALWORLD_SERVER_DESCRIPTION": "Fixture", "PALWORLD_PLAYER_EXP_RATE": "1",
                            "PALWORLD_PUBLIC_IP": "", "PALWORLD_BIND_IP": ""}

    def tearDown(self): self.temporary.cleanup()

    def command(self, *args, check=True):
        return subprocess.run([str(ROOT / "scripts/config-recovery.py"), *args], env=self.environment,
                              text=True, capture_output=True, timeout=20, check=check)

    def test_health_detects_corruption_environment_conflicts_and_worldoptions(self):
        payload = json.loads(self.command("health").stdout)
        self.assertTrue(payload["world"]["corrupted"])
        self.assertFalse(payload["engine"]["corrupted"])
        self.assertEqual(len(payload["world_options_conflicts"]), 1)
        self.assertTrue(payload["environment"]["valid"])
        self.environment["PALWORLD_REST_PORT"] = "8211"
        payload = json.loads(self.command("health").stdout)
        self.assertIn("port 8211 conflicts", " ".join(payload["environment"]["issues"]))

    def test_repair_preserves_corrupt_files_and_disables_worldoptions(self):
        result = json.loads(self.command("repair", "world", "--confirm", "RECOVER CONFIG").stdout)
        self.assertTrue(result["preserved"])
        self.assertIn("OptionSettings=(", (self.config / "PalWorldSettings.ini").read_text())
        self.assertTrue(pathlib.Path(result["preserved"][0]).is_file())
        target = self.install / "Pal/Saved/SaveGames/0/ABCDEF/WorldOptions.sav"
        result = json.loads(self.command("repair", "world-options", "--target", str(target), "--confirm", "RECOVER CONFIG").stdout)
        self.assertFalse(target.exists())
        self.assertTrue(pathlib.Path(result["preserved"]).is_file())

    def test_engine_repair_regenerates_from_authoritative_state(self):
        (self.config / "Engine.ini").write_text("[broken\n")
        result = json.loads(self.command("repair", "engine", "--confirm", "RECOVER CONFIG").stdout)
        self.assertEqual(result["kind"], "engine")
        self.assertTrue((self.config / "Engine.ini").is_file())
        self.assertTrue(pathlib.Path(result["preserved"][0]).is_file())


if __name__ == "__main__": unittest.main()
