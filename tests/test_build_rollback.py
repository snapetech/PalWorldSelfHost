import os
import pathlib
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = pathlib.Path(__file__).parents[1]


class BuildRollbackWrapperTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.lib = self.root / "lib"
        self.bin = self.root / "bin"
        self.lib.mkdir()
        self.bin.mkdir()
        shutil.copy2(ROOT / "scripts" / "ops-build-rollback.sh", self.lib)
        (self.root / "build").write_text("200")
        (self.root / "save.sav").write_text("newer-save-sentinel")
        self.write_executable("lib/palworld-common.sh", """
            SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
            acquire_mutation_lock() { :; }
            render_settings() { :; }
            ops_event() { printf '%s\\n' "$*" >>"$LAB_ROOT/audit"; }
        """)
        self.write_executable("lib/steam-build-manager.py", """
            #!/bin/sh
            case "$1" in
              list) printf '{"current_build":"%s"}\\n' "$(cat "$LAB_ROOT/build")" ;;
              plan) [ "$2" = 100 ] ;;
              snapshot) printf '{"build_id":"%s"}\\n' "$(cat "$LAB_ROOT/build")" ;;
              restore) printf '%s' "$2" >"$LAB_ROOT/build" ;;
              pin) printf '%s' "$2" >"$LAB_ROOT/pin" ;;
              *) exit 64 ;;
            esac
        """)
        self.write_executable("lib/backup.sh", """
            #!/bin/sh
            printf 'verified\\n%s\\n' "$LAB_ROOT/protected.tar.zst"
        """)
        self.write_executable("lib/graceful-shutdown.sh", "#!/bin/sh\nexit 0\n")
        self.write_executable("lib/rest-client.py", """
            #!/bin/sh
            [ "$1" = info ] || exit 64
            [ "$(cat "$LAB_ROOT/build")" = 200 ]
        """)
        self.write_executable("bin/systemctl", """
            #!/bin/sh
            case "$1" in
              is-active) exit 1 ;;
              start|stop) printf '%s\\n' "$*" >>"$LAB_ROOT/systemctl" ;;
              *) exit 64 ;;
            esac
        """)
        self.write_executable("bin/seq", "#!/bin/sh\nprintf '1\\n'\n")
        self.write_executable("bin/sleep", "#!/bin/sh\nexit 0\n")

    def tearDown(self):
        self.temporary.cleanup()

    def write_executable(self, relative, content):
        path = self.root / relative
        path.write_text(textwrap.dedent(content).lstrip())
        path.chmod(0o755)

    def run_wrapper(self, confirmation="ROLLBACK BUILD"):
        env = os.environ.copy()
        env.update({"LAB_ROOT": str(self.root), "PATH": f"{self.bin}:{env['PATH']}"})
        return subprocess.run(
            [str(self.lib / "ops-build-rollback.sh"), "100", "--execute", "--confirm", confirmation],
            text=True, capture_output=True, env=env, timeout=10,
        )

    def test_failed_downgrade_restores_and_health_checks_prior_build_without_touching_save(self):
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 1)
        self.assertIn("prior executable build 200 was restored", result.stderr)
        self.assertEqual((self.root / "build").read_text(), "200")
        self.assertEqual((self.root / "save.sav").read_text(), "newer-save-sentinel")
        self.assertFalse((self.root / "pin").exists())
        self.assertGreaterEqual((self.root / "systemctl").read_text().count("start palworld.service"), 2)
        self.assertIn("automatic_restore=200", (self.root / "audit").read_text())

    def test_recovery_is_reported_incomplete_when_prior_build_is_not_healthy(self):
        (self.lib / "rest-client.py").write_text("#!/bin/sh\nexit 1\n")
        (self.lib / "rest-client.py").chmod(0o755)
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 1)
        self.assertIn("automatic executable recovery was incomplete", result.stderr)
        self.assertIn("recovered build REST health failed", result.stderr)

    def test_exact_confirmation_is_required_before_any_mutation(self):
        result = self.run_wrapper("yes")
        self.assertEqual(result.returncode, 64)
        self.assertEqual((self.root / "build").read_text(), "200")
        self.assertFalse((self.root / "systemctl").exists())


if __name__ == "__main__":
    unittest.main()
