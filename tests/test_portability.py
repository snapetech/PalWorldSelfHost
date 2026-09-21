import os
import pathlib
import shutil
import signal
import subprocess
import tempfile
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "deploy" / "container" / "entrypoint.sh"
CHART = ROOT / "deploy" / "helm" / "palworld-selfhost"
DIGEST = "sha256:0d293cafd503a91a6d11d71f7bf770ee0c3c5ecf37db988349b2c1758f4e9358"
ARM64_INDEX = "sha256:85ae20d8756dd398ec24f4253a59b80907e5ffe411148a67b2c80c4527a8855e"
ARM64_MANIFEST = "sha256:e09c1e16db753e01113e0f8a1f7f8b3af46664e51646e3d265b5b72bec113218"
ARM64 = ROOT / "deploy" / "arm64"


class PortabilityTests(unittest.TestCase):
    def fixture(self, root: pathlib.Path):
        package = root / "Package"
        package.mkdir()
        marker = root / "graceful.marker"
        server = package / "PalServer.sh"
        server.write_text(
            "#!/bin/sh\n"
            f"trap 'printf graceful > {marker}; exit 0' INT\n"
            f": > {root / 'server.started'}\n"
            "while :; do sleep 0.1; done\n"
        )
        server.chmod(0o755)
        settings = root / "PalWorldSettings.ini"
        settings.write_text(
            "[/Script/Pal.PalGameWorldSettings]\n"
            'OptionSettings=(ServerName="portable",AdminPassword="fixture")\n'
        )
        env = os.environ.copy()
        env.update(
            {
                "PALWORLD_PACKAGE_DIR": str(package),
                "PALWORLD_CONFIG_SOURCE": str(settings),
                "PALWORLD_READY_FILE": str(root / "ready"),
                "PALWORLD_PID_FILE": str(root / "pid"),
                "PALWORLD_SKIP_CHOWN": "true",
                "PALWORLD_DISABLE_REST_SHUTDOWN": "true",
                "PALWORLD_WORLD_ID": "A" * 32,
            }
        )
        return package, settings, marker, env

    def test_entrypoint_atomically_installs_settings_and_forwards_graceful_stop(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            package, settings, marker, env = self.fixture(root)
            process = subprocess.Popen(["sh", str(ENTRYPOINT), "-port=8211"], env=env)
            deadline = time.monotonic() + 5
            # "ready" only means the entrypoint spawned the server. Wait for the server to
            # install its INT trap too, or SIGTERM can land before it can shut down gracefully.
            while not ((root / "ready").exists() and (root / "server.started").exists()) and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue((root / "ready").exists())
            self.assertTrue((root / "server.started").exists())
            process.send_signal(signal.SIGTERM)
            self.assertEqual(0, process.wait(timeout=5))
            self.assertEqual("graceful", marker.read_text())
            installed = package / "Pal" / "Saved" / "Config" / "LinuxServer" / "PalWorldSettings.ini"
            self.assertEqual(settings.read_text(), installed.read_text())
            self.assertEqual(0o600, installed.stat().st_mode & 0o777)
            game_user = package / "Pal" / "Saved" / "Config" / "LinuxServer" / "GameUserSettings.ini"
            self.assertIn(f'DedicatedServerName={"A" * 32}', game_user.read_text())
            self.assertFalse((root / "ready").exists())
            self.assertFalse((root / "pid").exists())

    def test_entrypoint_refuses_malformed_new_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            _package, settings, _marker, env = self.fixture(root)
            settings.write_text("[/Script/Pal.PalGameWorldSettings]\nServerName=broken\n")
            result = subprocess.run(["sh", str(ENTRYPOINT)], env=env, text=True, capture_output=True)
            self.assertEqual(64, result.returncode)
            self.assertIn("refusing invalid PalWorldSettings.ini", result.stderr)

    def test_compose_and_chart_pin_the_reviewed_official_image(self):
        compose = (ROOT / "deploy" / "container" / "compose.yaml").read_text()
        values = (CHART / "values.yaml").read_text()
        statefulset = (CHART / "templates" / "statefulset.yaml").read_text()
        self.assertIn(DIGEST, compose)
        self.assertIn(DIGEST, values)
        self.assertIn("/pal/Package/Pal/Saved", compose)
        self.assertIn("/pal/Package/Pal/Saved", statefulset)
        self.assertNotIn('"8212:8212', compose)
        self.assertIn('user: "0:0"', compose)
        self.assertIn("sudo -u user -g usergroup", ENTRYPOINT.read_text())
        self.assertEqual(
            ENTRYPOINT.read_text(),
            (CHART / "files" / "entrypoint.sh").read_text(),
            "Compose and Helm adapters must not drift",
        )

    def test_arm64_compose_pins_peer_release_and_private_controls(self):
        compose = (ARM64 / "compose.yaml").read_text()
        preflight = (ARM64 / "preflight.sh").read_text()
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertIn(f"v2.6.0@{ARM64_INDEX}", compose)
        self.assertIn("platform: linux/arm64", compose)
        self.assertIn('RCON_ENABLED: "false"', compose)
        self.assertIn('"8211:8211/udp"', compose)
        self.assertNotIn("8212:8212", compose)
        self.assertIn("palworld-arm64-data:/palworld", compose)
        self.assertIn(ARM64_MANIFEST, preflight)
        self.assertIn("getconf PAGESIZE", preflight)
        self.assertIn("ubuntu-24.04-arm", workflow)
        self.assertIn("/usr/local/bin/box64-generic", workflow)

    def test_arm64_preflight_accepts_only_native_4k_reviewed_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            binary = root / "bin"; binary.mkdir()
            environment = root / "palworld.env"
            environment.write_text(
                "SERVER_NAME=fixture\nADMIN_PASSWORD=fixture-password-1234\n"
                "PUBLIC_IP=192.0.2.10\nARM64_DEVICE=generic\n"
            )
            environment.chmod(0o600)
            (binary / "uname").write_text("#!/bin/sh\nprintf aarch64\n")
            (binary / "getconf").write_text("#!/bin/sh\nprintf 4096\n")
            (binary / "docker").write_text(
                "#!/bin/sh\n"
                "case \"$1 $2\" in\n"
                "  'info --format') printf aarch64;;\n"
                "  'compose version') exit 0;;\n"
                "  'buildx imagetools') printf '%s\\n' 'Digest:    " + ARM64_INDEX + "' 'Name: " + ARM64_MANIFEST + "';;\n"
                "  'compose --env-file') exit 0;;\n"
                "  *) exit 1;;\n"
                "esac\n"
            )
            for path in binary.iterdir(): path.chmod(0o755)
            run_environment = {**os.environ, "PATH": f"{binary}:{os.environ['PATH']}"}
            result = subprocess.run(
                [str(ARM64 / "preflight.sh"), str(environment)], env=run_environment,
                text=True, capture_output=True,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("ARM64 preflight passed", result.stdout)
            (binary / "getconf").write_text("#!/bin/sh\nprintf 16384\n"); (binary / "getconf").chmod(0o755)
            refused = subprocess.run(
                [str(ARM64 / "preflight.sh"), str(environment)], env=run_environment,
                text=True, capture_output=True,
            )
            self.assertEqual(64, refused.returncode)
            self.assertIn("4096-byte", refused.stderr)

    @unittest.skipUnless(shutil.which("helm"), "helm is not installed")
    def test_helm_lint_and_both_persistence_modes_render(self):
        lint = subprocess.run(["helm", "lint", str(CHART)], text=True, capture_output=True)
        self.assertEqual(0, lint.returncode, lint.stdout + lint.stderr)
        default = subprocess.run(
            ["helm", "template", "palworld", str(CHART)], text=True, capture_output=True
        )
        self.assertEqual(0, default.returncode, default.stderr)
        self.assertIn("volumeClaimTemplates:", default.stdout)
        self.assertIn(DIGEST, default.stdout)
        existing = subprocess.run(
            [
                "helm", "template", "palworld", str(CHART),
                "--set", "settings.existingSecret=live-settings",
                "--set", "persistence.existingClaim=live-saves",
            ],
            text=True,
            capture_output=True,
        )
        self.assertEqual(0, existing.returncode, existing.stderr)
        self.assertIn("secretName: live-settings", existing.stdout)
        self.assertIn("claimName: live-saves", existing.stdout)
        self.assertNotIn("kind: Secret", existing.stdout)


if __name__ == "__main__":
    unittest.main()
