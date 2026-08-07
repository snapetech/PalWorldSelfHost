import json
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]


class WineRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.install = self.root / "server"
        self.state = self.root / "state"
        self.backups = self.root / "backups"
        self.bin = self.root / "bin"
        for path in (self.install, self.state, self.backups, self.bin):
            path.mkdir()
        (self.install / "DefaultPalWorldSettings.ini").write_text(
            "[/Script/Pal.PalGameWorldSettings]\n"
            'OptionSettings=(DeathPenalty=Item,ExpRate=1.000000,ServerName="Fixture",'
            'ServerDescription="",AdminPassword="",PublicPort=8211,PublicIP="",'
            'RESTAPIEnabled=True,RESTAPIPort=8212,RCONEnabled=True,RCONPort=25575)\n'
        )
        (self.install / "PalServer.exe").write_bytes(b"fixture")
        self.capture = self.root / "launch.json"
        (self.bin / "wine").write_text("#!/bin/sh\nexit 0\n")
        (self.bin / "xvfb-run").write_text(
            "#!/bin/sh\n"
            "python3 -c 'import json,os,sys; json.dump({\"argv\":sys.argv[1:],"
            "\"prefix\":os.environ.get(\"WINEPREFIX\"),"
            "\"overrides\":os.environ.get(\"WINEDLLOVERRIDES\")},open(os.environ[\"CAPTURE\"],\"w\"))' \"$@\"\n"
        )
        for path in self.bin.iterdir():
            path.chmod(0o755)
        self.env_file = self.root / "palworld.env"
        self.env_file.write_text(
            "\n".join(
                (
                    f"PALWORLD_INSTALL_DIR={self.install}",
                    f"PALWORLD_STATE_DIR={self.state}",
                    f"PALWORLD_BACKUP_LOCAL_ROOT={self.backups}",
                    "PALWORLD_RUNTIME=wine-windows",
                    f"PALWORLD_WINE_PREFIX={self.state / 'wine-prefix'}",
                    "PALWORLD_USER=fixture",
                    'PALWORLD_SERVER_NAME="Wine fixture"',
                    "PALWORLD_ADMIN_PASSWORD=fixture-admin",
                    "PALWORLD_RCON_ENABLED=true",
                    "PALWORLD_RCON_PORT=25575",
                    "PALWORLD_REST_PORT=8212",
                    "PALWORLD_PORT=8211",
                )
            )
            + "\n"
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_start_renders_windows_config_and_executes_native_first_wine_profile(self):
        env = {
            **os.environ,
            "PALWORLD_ENV_FILE": str(self.env_file),
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "CAPTURE": str(self.capture),
        }
        result = subprocess.run(
            [str(ROOT / "scripts/start.sh")], env=env, check=False,
            text=True, capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        settings = self.install / "Pal/Saved/Config/WindowsServer/PalWorldSettings.ini"
        self.assertTrue(settings.is_file())
        self.assertIn('ServerName="Wine fixture"', settings.read_text())
        launch = json.loads(self.capture.read_text())
        self.assertEqual(launch["argv"][:3], ["-a", "wine", "./PalServer.exe"])
        self.assertIn("-RenderOffScreen", launch["argv"])
        self.assertIn("-port=8211", launch["argv"])
        self.assertEqual(launch["prefix"], str(self.state / "wine-prefix"))
        self.assertEqual(launch["overrides"], "d3d9=n,b;dwmapi=n,b")

    def test_update_selects_windows_depot_before_force_install_dir(self):
        script = (ROOT / "scripts/update.sh").read_text()
        platform = 'steamcmd+=(+@sSteamCmdForcePlatformType windows)'
        destination = 'steamcmd+=(+force_install_dir "$PALWORLD_INSTALL_DIR"'
        self.assertLess(script.index(platform), script.index(destination))

    def test_configuration_managers_select_windows_server_directory(self):
        env = {
            **os.environ,
            "PALWORLD_INSTALL_DIR": str(self.install),
            "PALWORLD_STATE_DIR": str(self.state),
            "PALWORLD_RUNTIME": "wine-windows",
        }
        subprocess.run(
            [str(ROOT / "scripts/server-config-manager.py"), "render"],
            env=env, check=True, text=True, capture_output=True,
        )
        self.assertTrue((self.install / "Pal/Saved/Config/WindowsServer/Engine.ini").is_file())
        self.assertFalse((self.install / "Pal/Saved/Config/LinuxServer").exists())


if __name__ == "__main__":
    unittest.main()
