import json
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]
ENGINE = """; operator comment
[/Script/OnlineSubsystemUtils.IpNetDriver]
NetServerMaxTickRate=30
UnmanagedNetKey=keep

[Custom.Plugin]
Enabled=True
"""


class ServerConfigManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.install = self.root / "server"
        self.state = self.root / "state"
        self.active = self.install / "Pal/Saved/Config/LinuxServer/Engine.ini"
        self.active.parent.mkdir(parents=True)
        self.state.mkdir()
        self.active.write_text(ENGINE)
        self.environment = {
            **os.environ,
            "PALWORLD_INSTALL_DIR": str(self.install),
            "PALWORLD_STATE_DIR": str(self.state),
        }

    def tearDown(self):
        self.temporary.cleanup()

    def command(self, *arguments, check=True):
        return subprocess.run(
            [str(ROOT / "scripts/server-config-manager.py"), *arguments],
            env=self.environment, text=True, capture_output=True, timeout=20, check=check,
        )

    def test_schema_exposes_ranges_presets_and_current_launch_arguments(self):
        payload = json.loads(self.command("schema").stdout)
        self.assertEqual(payload["engine"]["NetServerMaxTickRate"]["min"], 20)
        self.assertEqual(payload["engine"]["NetServerMaxTickRate"]["max"], 120)
        self.assertEqual(payload["launch"]["NumberOfWorkerThreadsServer"]["max"], 128)
        self.assertEqual(payload["launch"]["logformat"]["choices"], ["text", "json"])
        self.assertIn("Balanced", payload["presets"])
        self.assertIn("-publiclobby", payload["launch_args"])

    def test_typed_apply_preserves_unmanaged_ini_and_rolls_back(self):
        updates = {"engine": {"NetServerMaxTickRate": 60, "bSmoothFrameRate": True},
                   "launch": {"logformat": "json", "NumberOfWorkerThreadsServer": 8}}
        plan = json.loads(self.command("plan", json.dumps(updates)).stdout)
        self.assertIn("NetServerMaxTickRate=60", plan["preview_ini"])
        self.command("apply", json.dumps(updates), "--confirm", "APPLY SERVER CONFIG")
        rendered = self.active.read_text()
        self.assertIn("; operator comment", rendered)
        self.assertIn("UnmanagedNetKey=keep", rendered)
        self.assertIn("[Custom.Plugin]", rendered)
        self.assertIn("bSmoothFrameRate=True", rendered)
        args = self.command("launch-args").stdout.splitlines()
        self.assertIn("-NumberOfWorkerThreadsServer=8", args)
        self.assertIn("-logformat=json", args)
        self.assertTrue((self.state / "server-config-restart-required.json").is_file())
        self.command("rollback", "--confirm", "ROLLBACK SERVER CONFIG")
        self.assertEqual(json.loads((self.state / "engine-settings.json").read_text()), {})
        self.assertNotIn("bSmoothFrameRate=True", self.active.read_text())

    def test_raw_apply_rejects_bad_input_and_restores_snapshot(self):
        bad = self.command("raw-plan", "[broken", check=False)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("malformed", bad.stderr)
        huge = self.command("raw-plan", "x" * (60 * 1024 + 1), check=False)
        self.assertNotEqual(huge.returncode, 0)
        self.assertIn("exceeds", huge.stderr)
        proposed = ENGINE.replace("NetServerMaxTickRate=30", "NetServerMaxTickRate=75")
        self.command("raw-apply", proposed, "--confirm", "APPLY ENGINE INI")
        self.assertIn("NetServerMaxTickRate=75", self.active.read_text())
        self.command("raw-rollback", "--confirm", "ROLLBACK ENGINE INI")
        self.assertIn("NetServerMaxTickRate=30", self.active.read_text())

    def test_start_reapplies_engine_state_and_uses_configured_launch_args(self):
        script = (ROOT / "scripts/start.sh").read_text()
        self.assertIn('server-config-manager.py" render', script)
        self.assertIn('server-config-manager.py" launch-args', script)
        self.assertNotIn("-useperfthreads -NoAsyncLoadingThread -UseMultithreadForDS", script)


if __name__ == "__main__":
    unittest.main()
