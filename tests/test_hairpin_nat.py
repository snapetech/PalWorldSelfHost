import importlib.util
import os
import pathlib
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("hairpin_nat", ROOT / "scripts" / "hairpin-nat.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class HairpinNatTests(unittest.TestCase):
    def setUp(self):
        self.environment = {
            "PALWORLD_HAIRPIN_NAT_ENABLED": "true",
            "PALWORLD_PUBLIC_IP": "203.0.113.10",
            "PALWORLD_BIND_IP": "192.168.50.85",
            "PALWORLD_LAN_CIDR": "192.168.50.0/24",
            "PALWORLD_NETWORK_INTERFACE": "enp5s0",
            "PALWORLD_PORT": "8211",
        }

    def test_rules_are_scoped_to_lan_public_ip_and_game_port(self):
        with mock.patch.dict(os.environ, self.environment, clear=True):
            rendered = " ".join(MODULE.rule_arguments())
        self.assertIn("-i enp5s0", rendered)
        self.assertIn("-s 192.168.50.0/24", rendered)
        self.assertIn("-d 203.0.113.10", rendered)
        self.assertIn("--dport 8211", rendered)
        self.assertIn("--to-destination 192.168.50.85:8211", rendered)

    def test_disabled_mode_does_not_require_addresses(self):
        with mock.patch.dict(os.environ, {"PALWORLD_HAIRPIN_NAT_ENABLED": "false"}, clear=True), mock.patch.object(MODULE, "clear_legacy_table"):
            self.assertTrue(MODULE.apply()["effective"])

    def test_invalid_interface_is_rejected(self):
        self.environment["PALWORLD_NETWORK_INTERFACE"] = "bad interface"
        with mock.patch.dict(os.environ, self.environment, clear=True):
            with self.assertRaisesRegex(ValueError, "INTERFACE"):
                MODULE.settings()


if __name__ == "__main__":
    unittest.main()
