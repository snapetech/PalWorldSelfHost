import importlib.util
import json
import os
import pathlib
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]


class ExposureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "exposure_check_test", ROOT / "scripts" / "exposure-check.py",
        )
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_listener_parser_handles_ipv4_ipv6_and_detects_control_plane_exposure(self):
        listeners = self.module.parse_listeners(
            "udp UNCONN 0 0 0.0.0.0:8211 0.0.0.0:*\n"
            "tcp LISTEN 0 4096 127.0.0.1:8212 0.0.0.0:*\n"
            "tcp LISTEN 0 4096 [::]:8213 [::]:*\n"
            "tcp LISTEN 0 4096 [::1]:25575 [::]:*\n"
        )
        result = self.module.assess(listeners, "", 8211, 8212, 8213, 25575)
        self.assertIn("operations TCP 8213 listens on a non-loopback address", result["issues"])
        self.assertFalse(result["safe"])
        self.assertTrue(result["channels"]["rest"]["listening"])
        self.assertEqual(result["channels"]["rcon"]["non_loopback"], [])

    def test_loopback_controls_and_game_listener_are_safe_without_claiming_public_proof(self):
        listeners = self.module.parse_listeners(
            "udp UNCONN 0 0 0.0.0.0:8211 0.0.0.0:*\n"
            "tcp LISTEN 0 4096 127.0.0.1:8212 0.0.0.0:*\n"
            "tcp LISTEN 0 4096 127.0.0.1:8213 0.0.0.0:*\n"
        )
        result = self.module.assess(listeners, "", 8211, 8212, 8213, 25575)
        self.assertTrue(result["safe"])
        self.assertFalse(result["reachability_proven"])
        self.assertEqual(result["public_game"]["status"], "unconfigured")

    def test_external_probe_requires_bounded_boolean_contract(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, _): return json.dumps({"reachable": True, "detail": "ok"}).encode()

        with mock.patch.dict(os.environ, {
            "PALWORLD_PUBLIC_PROBE_URL": "https://probe.example/check",
        }, clear=False), mock.patch.object(self.module.urllib.request, "urlopen", return_value=Response()) as opened:
            result = self.module.probe_public("203.0.113.10", 8211)
        self.assertTrue(result["reachable"])
        self.assertIn("protocol=udp", opened.call_args.args[0].full_url)

    def test_verified_firewall_makes_wildcard_rcon_effectively_private(self):
        listeners = [
            {"protocol": "tcp", "address": "0.0.0.0", "port": 25575},
            {"protocol": "udp", "address": "0.0.0.0", "port": 8211},
        ]
        result = self.module.assess(listeners, "", 8211, 8212, 8213, 25575, True)
        self.assertTrue(result["safe"])
        self.assertTrue(result["channels"]["rcon"]["firewall_protected"])
        self.assertTrue(result["channels"]["rcon"]["effective_private"])
        self.assertFalse(any("rcon" in issue for issue in result["issues"]))

    def test_verified_firewall_makes_wildcard_rest_effectively_private(self):
        listeners = [
            {"protocol": "tcp", "address": "0.0.0.0", "port": 8212},
            {"protocol": "udp", "address": "0.0.0.0", "port": 8211},
        ]
        result = self.module.assess(listeners, "", 8211, 8212, 8213, 25575, False, True)
        self.assertTrue(result["safe"])
        self.assertTrue(result["channels"]["rest"]["effective_private"])
        self.assertFalse(any("rest" in issue for issue in result["issues"]))


if __name__ == "__main__":
    unittest.main()
