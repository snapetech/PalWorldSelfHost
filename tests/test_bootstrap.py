import importlib.util
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bootstrap_server", ROOT / "scripts" / "bootstrap-server.py")
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def base_payload(root, mode="fresh"):
    return {
        "mode": mode,
        "install_dir": str(root / "server"),
        "state_dir": str(root / "state"),
        "backup_root": str(root / "backups"),
        "public_dir": str(root / "public"),
        "steamcmd": str(root / "steamcmd"),
        "rclone_dest": "offsite:palworld",
        "rclone_config": str(root / "rclone.conf"),
        "server_name": "Fixture world",
        "server_description": "Bootstrap fixture",
        "player_exp_rate": 1.0,
        "game_port": 8211,
        "rest_port": 8212,
        "ops_port": 8213,
        "rcon_port": 25575,
        "public_ip": "",
        "bind_ip": "",
    }


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.env_file = self.root / "etc" / "palworld-server.env"
        steamcmd = self.root / "steamcmd"
        steamcmd.write_text("#!/bin/sh\nexit 0\n")
        os.chmod(steamcmd, 0o755)
        self.calls = []

    def tearDown(self):
        self.temporary.cleanup()

    def runner(self, command, append, environment):
        self.calls.append((command, environment))
        append("starting password=never-print-real-secrets")
        append("complete")
        return 0

    def manager(self, **kwargs):
        return bootstrap.BootstrapManager(
            repo=ROOT,
            env_path=self.env_file,
            runner=kwargs.pop("runner", self.runner),
            prerequisite_checker=kwargs.pop("prerequisite_checker", lambda config: []),
            **kwargs,
        )

    def test_configuration_refuses_overlaps_ports_addresses_and_nonempty_fresh_tree(self):
        payload = base_payload(self.root)
        payload["state_dir"] = str(self.root / "server" / "state")
        with self.assertRaisesRegex(bootstrap.BootstrapError, "overlap"):
            bootstrap.normalize_config(payload)

        payload = base_payload(self.root)
        payload["rest_port"] = payload["game_port"]
        with self.assertRaisesRegex(bootstrap.BootstrapError, "distinct"):
            bootstrap.normalize_config(payload)

        payload = base_payload(self.root)
        payload["public_ip"] = "999.1.1.1"
        with self.assertRaisesRegex(bootstrap.BootstrapError, "IPv4"):
            bootstrap.normalize_config(payload)

        install = pathlib.Path(payload["install_dir"])
        install.mkdir(); (install / "unmanaged.txt").write_text("data")
        with self.assertRaisesRegex(bootstrap.BootstrapError, "not empty"):
            self.manager().create_plan(base_payload(self.root))

    def test_prerequisites_and_exact_confirmation_block_environment_write(self):
        manager = self.manager(prerequisite_checker=lambda config: ["rclone"])
        plan = manager.create_plan(base_payload(self.root))
        self.assertEqual(["rclone"], plan["missing_prerequisites"])
        with self.assertRaisesRegex(bootstrap.BootstrapError, "prerequisites"):
            manager.execute({
                "plan_id": plan["plan_id"], "confirmation": "INSTALL PALWORLD",
                "admin_password": "a" * 32, "ops_token": "b" * 32,
            }, background=False)
        self.assertFalse(self.env_file.exists())

        manager = self.manager()
        plan = manager.create_plan(base_payload(self.root))
        with self.assertRaisesRegex(bootstrap.BootstrapError, "exactly"):
            manager.execute({
                "plan_id": plan["plan_id"], "confirmation": "install palworld",
                "admin_password": "a" * 32, "ops_token": "b" * 32,
            }, background=False)
        self.assertFalse(self.env_file.exists())

    def test_fresh_execution_writes_root_only_shell_safe_credentials_and_redacts_output(self):
        manager = self.manager()
        payload = base_payload(self.root)
        payload["server_name"] = "Keith's fixture world"
        plan = manager.create_plan(payload)
        admin = "admin-'quoted-value-1234567890"
        token = "ops-token-value-123456789012345"
        manager.runner = lambda command, append, environment: (append(f"password={admin} token={token}"), self.calls.append((command, environment)), 0)[2]
        result = manager.execute({
            "plan_id": plan["plan_id"], "confirmation": plan["confirmation"],
            "admin_password": admin, "ops_token": token,
        }, background=False)
        self.assertEqual("running", result["status"])
        self.assertEqual("complete", manager.status()["phase"])
        self.assertNotIn(admin, manager.status()["output"])
        self.assertNotIn(token, manager.status()["output"])
        self.assertEqual(0o600, self.env_file.stat().st_mode & 0o777)
        receipt = pathlib.Path(payload["state_dir"]) / "bootstrap-receipt.json"
        self.assertTrue(receipt.is_file())
        self.assertEqual(0o640, receipt.stat().st_mode & 0o777)
        self.assertNotIn(admin, receipt.read_text())
        self.assertNotIn(token, receipt.read_text())
        command, environment = self.calls[0]
        self.assertEqual(str(ROOT / "scripts" / "install.sh"), command[0])
        self.assertEqual(str(self.env_file), environment["PALWORLD_ENV_FILE"])

        shell = subprocess.run(
            ["bash", "-c", 'set -a; source "$1"; printf "%s\\n%s\\n%s" "$PALWORLD_SERVER_NAME" "$PALWORLD_ADMIN_PASSWORD" "$PALWORLD_OPS_TOKEN"', "bash", str(self.env_file)],
            text=True, capture_output=True, check=True,
        )
        self.assertEqual([payload["server_name"], admin, token], shell.stdout.splitlines())

    def test_existing_environment_is_never_overwritten(self):
        self.env_file.parent.mkdir(); self.env_file.write_text("existing\n")
        manager = self.manager()
        plan = manager.create_plan(base_payload(self.root))
        with self.assertRaisesRegex(bootstrap.BootstrapError, "already exists"):
            manager.execute({
                "plan_id": plan["plan_id"], "confirmation": plan["confirmation"],
                "admin_password": "a" * 32, "ops_token": "b" * 32,
            }, background=False)
        self.assertEqual("existing\n", self.env_file.read_text())

    def test_adoption_plan_is_redacted_and_exact_fingerprint_reaches_installer(self):
        adoption = {
            "status": "ready", "build_id": "12345", "worlds": [{"world": "A", "players": 2}],
            "fingerprint": "f" * 64, "files": {"server": {"sha256": "secret-file-hash"}},
        }
        planner_calls = []
        manager = self.manager(adoption_planner=lambda install, state: (planner_calls.append((install, state)), adoption)[1])
        plan = manager.create_plan(base_payload(self.root, "adopt"))
        self.assertEqual("f" * 64, plan["adoption"]["fingerprint"])
        self.assertNotIn("files", plan["adoption"])
        manager.execute({
            "plan_id": plan["plan_id"], "confirmation": "ADOPT EXISTING INSTALL",
            "admin_password": "a" * 32, "ops_token": "b" * 32,
        }, background=False)
        command = self.calls[0][0]
        self.assertIn("--adopt-existing", command)
        self.assertEqual("f" * 64, command[command.index("--adoption-fingerprint") + 1])
        self.assertEqual(1, len(planner_calls))

    def test_plan_expiry_secret_separation_and_loopback_contract(self):
        clock = [1000]
        manager = self.manager(now=lambda: clock[0])
        plan = manager.create_plan(base_payload(self.root))
        clock[0] += bootstrap.PLAN_TTL + 1
        with self.assertRaisesRegex(bootstrap.BootstrapError, "expired"):
            manager.execute({
                "plan_id": plan["plan_id"], "confirmation": plan["confirmation"],
                "admin_password": "a" * 32, "ops_token": "b" * 32,
            }, background=False)
        self.assertFalse(self.env_file.exists())

        manager = self.manager()
        plan = manager.create_plan(base_payload(self.root))
        with self.assertRaisesRegex(bootstrap.BootstrapError, "must be different"):
            manager.execute({
                "plan_id": plan["plan_id"], "confirmation": plan["confirmation"],
                "admin_password": "x" * 32, "ops_token": "x" * 32,
            }, background=False)
        self.assertTrue(bootstrap.loopback_address("127.0.0.1"))
        self.assertTrue(bootstrap.loopback_address("::1"))
        self.assertFalse(bootstrap.loopback_address("0.0.0.0"))

    def test_failed_install_retries_only_same_bound_plan_and_credentials(self):
        attempts = []
        def runner(command, append, environment):
            attempts.append(command)
            append("bounded output")
            return 9 if len(attempts) == 1 else 0

        clock = [1000]
        manager = self.manager(runner=runner, now=lambda: clock[0])
        plan = manager.create_plan(base_payload(self.root))
        execute = {
            "plan_id": plan["plan_id"], "confirmation": plan["confirmation"],
            "admin_password": "a" * 32, "ops_token": "b" * 32,
        }
        manager.execute(execute, background=False)
        self.assertEqual("failed", manager.status()["phase"])
        with self.assertRaisesRegex(bootstrap.BootstrapError, "same bound plan"):
            manager.create_plan(base_payload(self.root))
        with self.assertRaisesRegex(bootstrap.BootstrapError, "same saved credentials"):
            manager.execute({**execute, "ops_token": "c" * 32}, background=False)

        clock[0] += bootstrap.PLAN_TTL + 100
        manager.execute(execute, background=False)
        self.assertEqual("complete", manager.status()["phase"])
        self.assertEqual(2, len(attempts))


if __name__ == "__main__":
    unittest.main()
