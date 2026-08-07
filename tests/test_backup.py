import hashlib
import io
import os
import pathlib
import subprocess
import tarfile
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]


class BackupTests(unittest.TestCase):
    def test_already_saved_mode_requires_the_held_mutation_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            install = root / "server"; backups = root / "backups"; state = root / "state"; tools = root / "tools"
            for path in (install / "Pal/Saved", backups, state, tools): path.mkdir(parents=True)
            systemctl = tools / "systemctl"
            systemctl.write_text("#!/bin/sh\nexit 0\n"); systemctl.chmod(0o755)
            environment_file = root / "palworld.env"
            environment_file.write_text("\n".join([
                f"PALWORLD_INSTALL_DIR={install}", f"PALWORLD_BACKUP_LOCAL_ROOT={backups}",
                f"PALWORLD_STATE_DIR={state}", "PALWORLD_RCLONE_DEST=fixture:palworld",
                "PALWORLD_SERVER_NAME=Fixture", "PALWORLD_ADMIN_PASSWORD=fixture-secret",
            ]) + "\n")
            environment = dict(os.environ, PALWORLD_ENV_FILE=str(environment_file),
                               PALWORLD_BACKUP_ALREADY_SAVED="true",
                               PATH=f"{tools}:{os.environ['PATH']}")
            result = subprocess.run([str(ROOT / "scripts" / "backup.sh"), "protected"],
                                    text=True, capture_output=True, env=environment, timeout=15)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("requires the held mutation lock", result.stderr)

    def test_backup_is_verified_before_atomic_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            install = root / "server"
            backups = root / "backups"
            state = root / "state"
            tools = root / "tools"
            world = install / "Pal" / "Saved" / "SaveGames" / "0" / "world"
            world.mkdir(parents=True)
            backups.mkdir()
            state.mkdir()
            tools.mkdir()
            (world / "Level.sav").write_bytes(b"world fixture")
            (install / "DefaultPalWorldSettings.ini").write_text("OptionSettings=()\n")
            for name in ("rclone", "systemctl"):
                executable = tools / name
                executable.write_text("#!/bin/sh\nexit 0\n" if name == "rclone" else "#!/bin/sh\nexit 3\n")
                executable.chmod(0o755)
            environment_file = root / "palworld.env"
            environment_file.write_text("\n".join([
                f"PALWORLD_INSTALL_DIR={install}",
                f"PALWORLD_BACKUP_LOCAL_ROOT={backups}",
                f"PALWORLD_STATE_DIR={state}",
                "PALWORLD_RCLONE_DEST=fixture:palworld",
                "PALWORLD_SERVER_NAME=Fixture",
                "PALWORLD_ADMIN_PASSWORD=fixture-secret",
                "PALWORLD_BACKUP_RETENTION_DAYS=14",
            ]) + "\n")
            environment = dict(os.environ)
            environment.update({
                "PALWORLD_ENV_FILE": str(environment_file),
                "PATH": f"{tools}:{environment['PATH']}",
            })
            completed = subprocess.run(
                [str(ROOT / "scripts" / "backup.sh"), "daily"],
                text=True, capture_output=True, env=environment, timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            archives = list(backups.glob("palworld-daily-*.tar.zst"))
            self.assertEqual(len(archives), 1)
            archive = archives[0]
            self.assertTrue(pathlib.Path(str(archive) + ".sha256").is_file())
            self.assertTrue(pathlib.Path(str(archive) + ".manifest.json").is_file())
            self.assertFalse(list(backups.glob(".backup-staging-*")))
            self.assertEqual(archive.stat().st_mode & 0o777, 0o640)
            verified = subprocess.run(
                [str(ROOT / "scripts" / "verify-backup.sh"), str(archive)],
                text=True, capture_output=True, cwd="/", timeout=15,
            )
            self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
            extracted = root / "extracted"
            extracted.mkdir()
            unpacked = subprocess.run(
                [str(ROOT / "scripts" / "verify-backup.py"), str(archive), "--extract-to", str(extracted)],
                text=True, capture_output=True, timeout=15,
            )
            self.assertEqual(unpacked.returncode, 0, unpacked.stdout + unpacked.stderr)
            self.assertEqual(
                (extracted / "Pal" / "Saved" / "SaveGames" / "0" / "world" / "Level.sav").read_bytes(),
                b"world fixture",
            )

    def test_structural_validation_rejects_traversal_and_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            for unsafe_type in ("traversal", "link"):
                with self.subTest(unsafe_type=unsafe_type):
                    plain = root / f"{unsafe_type}.tar"
                    archive = root / f"{unsafe_type}.tar.zst"
                    with tarfile.open(plain, "w") as bundle:
                        level = tarfile.TarInfo("Pal/Saved/SaveGames/0/world/Level.sav")
                        level.size = 5
                        bundle.addfile(level, io.BytesIO(b"world"))
                        if unsafe_type == "traversal":
                            unsafe = tarfile.TarInfo("../../outside")
                            unsafe.size = 3
                            bundle.addfile(unsafe, io.BytesIO(b"bad"))
                        else:
                            unsafe = tarfile.TarInfo("Pal/Saved/escape-link")
                            unsafe.type = tarfile.SYMTYPE
                            unsafe.linkname = "/etc/passwd"
                            bundle.addfile(unsafe)
                    subprocess.run(["zstd", "-q", "-f", str(plain), "-o", str(archive)], check=True)
                    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                    pathlib.Path(str(archive) + ".sha256").write_text(f"{digest}  {archive.name}\n")
                    result = subprocess.run(
                        [str(ROOT / "scripts" / "verify-backup.py"), str(archive)],
                        text=True, capture_output=True, timeout=15,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("invalid backup archive", result.stderr)


if __name__ == "__main__":
    unittest.main()
