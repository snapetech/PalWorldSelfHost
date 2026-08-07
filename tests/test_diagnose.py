import json
import os
import pathlib
import subprocess
import tarfile
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]


class DiagnoseTests(unittest.TestCase):
    def test_bundle_redacts_state_credentials_and_addresses(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            state = root / "state"
            output = root / "output"
            state.mkdir()
            output.mkdir()
            (state / "health.json").write_text(json.dumps({
                "detail": "token=fixture-secret endpoint=192.168.50.25",
            }))
            environment = {**os.environ, "PALWORLD_STATE_DIR": str(state)}
            process = subprocess.run(
                [str(ROOT / "scripts" / "diagnose.py")], cwd=output,
                env=environment, text=True, capture_output=True, timeout=60,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            archive = pathlib.Path(process.stdout.strip())
            self.assertTrue(archive.is_file())
            with tarfile.open(archive, "r:gz") as bundle:
                health = bundle.extractfile("palworld-support/health.json").read().decode()
            self.assertNotIn("fixture-secret", health)
            self.assertNotIn("192.168.50.25", health)
            self.assertIn("<redacted>", health)


if __name__ == "__main__":
    unittest.main()
