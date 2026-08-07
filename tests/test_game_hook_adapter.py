import http.server
import importlib.util
import json
import os
import pathlib
import tempfile
import threading
import unittest


ROOT = pathlib.Path(__file__).parents[1]


class CaptureHandler(http.server.BaseHTTPRequestHandler):
    status = 200
    requests = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.__class__.requests.append((self.path, self.headers.get("Authorization"), json.loads(body)))
        self.send_response(self.__class__.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"relayed"}')

    def log_message(self, *_args):
        pass


class GameHookAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.spool = self.root / "spool"
        self.spool.mkdir()
        os.environ["PALWORLD_STATE_DIR"] = str(self.root / "state")
        spec = importlib.util.spec_from_file_location(
            f"game_hook_test_{id(self)}", ROOT / "scripts/game-hook-adapter.py",
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CaptureHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        CaptureHandler.requests = []
        CaptureHandler.status = 200
        os.environ.update({
            "PALWORLD_CHAT_RELAY_TOKEN": "fixture-chat-relay-token-123456",
            "PALWORLD_CHAT_RELAY_PORT": str(self.server.server_port),
            "PALWORLD_CHAT_RELAY_CATEGORIES": "global",
        })

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def event(self, number, **updates):
        value = {"schema": 1, "kind": "chat", "player": "Lamball", "message": "hello", "category": "global"}
        value.update(updates)
        path = self.spool / f"event-1784190000-{number:06d}.json"
        path.write_text(json.dumps(value))
        return path

    def test_valid_global_event_is_bearer_forwarded_then_deleted(self):
        path = self.event(1)
        ignored = self.event(2, category="guild")
        result = self.module.process_once(self.spool)
        self.assertEqual(result, {"relayed": 1, "ignored": 1, "rejected": 0, "retry": 0})
        self.assertFalse(path.exists())
        self.assertFalse(ignored.exists())
        route, authorization, payload = CaptureHandler.requests[0]
        self.assertEqual(route, "/chat")
        self.assertEqual(authorization, "Bearer fixture-chat-relay-token-123456")
        self.assertEqual(payload["player"], "Lamball")
        self.assertEqual(payload["message"], "hello")
        self.assertRegex(payload["event_id"], r"^ue4ss-[0-9a-f]{48}$")

    def test_server_failure_retries_but_permanent_rejection_is_removed(self):
        path = self.event(3)
        CaptureHandler.status = 503
        result = self.module.process_once(self.spool)
        self.assertEqual(result["retry"], 1)
        self.assertTrue(path.exists())
        CaptureHandler.status = 400
        result = self.module.process_once(self.spool)
        self.assertEqual(result["rejected"], 1)
        self.assertFalse(path.exists())

    def test_malformed_oversized_and_unsafe_payloads_are_dropped(self):
        malformed = self.spool / "event-1784190000-000004.json"
        malformed.write_text("{broken")
        oversized = self.spool / "event-1784190000-000005.json"
        oversized.write_bytes(b"x" * (self.module.MAX_EVENT_BYTES + 1))
        bad = self.event(6, player="x" * 81)
        result = self.module.process_once(self.spool)
        self.assertEqual(result["rejected"], 3)
        self.assertFalse(any((malformed.exists(), oversized.exists(), bad.exists())))
        with self.assertRaisesRegex(ValueError, "tmpfs"):
            os.environ["PALWORLD_GAME_HOOK_SPOOL"] = str(self.root / "persistent")
            self.module.volatile_spool()

    def test_lua_hook_uses_volatile_spool_without_network_credentials(self):
        lua = (ROOT / "deploy/wine-modded/PalWorldSelfHostRelay/Scripts/main.lua").read_text()
        self.assertIn("BroadcastChatMessage", lua)
        self.assertIn("PALWORLD_GAME_HOOK_SPOOL", lua)
        self.assertIn('spool = "Z:" .. spool:gsub("/", "\\\\")', lua)
        self.assertIn("os.rename(temporary, final)", lua)
        self.assertNotIn("curl", lua.casefold())
        self.assertNotIn("authorization", lua.casefold())
        unit = (ROOT / "systemd/palworld-game-hook.service").read_text()
        self.assertIn("RuntimeDirectory=palworld", unit)
        self.assertIn("ReadWritePaths=/run/palworld /var/lib/palworld", unit)
        installer = (ROOT / "scripts/install.sh").read_text()
        self.assertIn("PalWorldSelfHostRelay/Scripts/main.lua", installer)
        self.assertIn("palworld-game-hook.service", installer)
        package = (ROOT / "packaging/build-deb.sh").read_text()
        self.assertIn("deploy", package)
        self.assertIn("Recommends: wine64, xvfb", package)


if __name__ == "__main__":
    unittest.main()
