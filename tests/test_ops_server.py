import http.client
import importlib.util
import json
import os
import pathlib
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer


ROOT = pathlib.Path(__file__).parents[1]


class OpsServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = pathlib.Path(cls.temporary.name)
        cls.backups = cls.root / "backups"
        cls.state = cls.root / "state"
        cls.backups.mkdir()
        cls.state.mkdir()
        cls.install = cls.root / "server"
        for path in (cls.install / "Pal/Saved/Config/LinuxServer", cls.install / "Pal/Content/Paks", cls.install / "Pal/Saved/Logs"):
            path.mkdir(parents=True)
        os.environ.update({
            "PALWORLD_LIB_DIR": str(ROOT / "scripts"),
            "PALWORLD_BACKUP_LOCAL_ROOT": str(cls.backups),
            "PALWORLD_STATE_DIR": str(cls.state),
            "PALWORLD_INSTALL_DIR": str(cls.install),
            "PALWORLD_OPS_TOKEN": "admin-secret",
            "PALWORLD_OPS_MODERATOR_TOKEN": "moderator-secret",
            "PALWORLD_OPS_VIEWER_TOKEN": "viewer-secret",
            "PALWORLD_OPS_INTEGRATION_TOKEN": "integration-secret",
        })
        spec = importlib.util.spec_from_file_location("ops_server_test", ROOT / "admin" / "ops-server.py")
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), cls.module.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)
        cls.temporary.cleanup()

    def setUp(self):
        self.module.LOGIN_ATTEMPTS.clear()
        self.module.SESSIONS.clear()

    def request(self, method, path, payload=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        body = None if payload is None else json.dumps(payload)
        request_headers = dict(headers or {})
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        data = response.read()
        result = (response.status, dict(response.getheaders()), data)
        connection.close()
        return result

    def login(self, token):
        status, headers, body = self.request("POST", "/api/v1/auth/login", {"token": token})
        self.assertEqual(status, 200)
        data = json.loads(body)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        return data, cookie

    def test_role_tokens_create_csrf_protected_sessions(self):
        data, cookie = self.login("viewer-secret")
        self.assertEqual(data["role"], "viewer")
        status, _, body = self.request("GET", "/api/v1/auth/session", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["role"], "viewer")
        status, _, _ = self.request("POST", "/api/v1/auth/logout", {}, {"Cookie": cookie})
        self.assertEqual(status, 403)
        status, headers, _ = self.request("POST", "/api/v1/auth/logout", {}, {
            "Cookie": cookie, "X-CSRF-Token": data["csrf"],
        })
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])

    def test_integration_bearer_is_read_only_scoped_and_machine_documented(self):
        status, _, body = self.request("GET", "/api/v1/openapi.json")
        self.assertEqual(200, status)
        contract = json.loads(body)
        self.assertEqual("3.1.0", contract["openapi"])
        self.assertIn("/api/v1/integration/status", contract["paths"])
        bearer = {"Authorization": "Bearer integration-secret"}
        status, _, body = self.request("GET", "/api/v1/integration/status", headers=bearer)
        self.assertEqual(200, status)
        self.assertIn("player_count", json.loads(body))
        status, _, _ = self.request("GET", "/api/v1/status", headers=bearer)
        self.assertEqual(401, status)
        status, _, _ = self.request("POST", "/api/v1/action/restart", {}, bearer)
        self.assertEqual(401, status)
        status, headers, _ = self.request("GET", "/api/v1/integration/status")
        self.assertEqual(401, status)
        self.assertIn("Bearer", headers["WWW-Authenticate"])

    def test_pairing_code_creates_one_normal_role_session_then_expires(self):
        result = self.module.pairing.create("viewer", 60, self.state / "pairing.json")
        original = self.module.pairing.PAIRINGS
        self.module.pairing.PAIRINGS = self.state / "pairing.json"
        try:
            status, headers, body = self.request("POST", "/api/v1/auth/pair", {"code": result["code"]})
            self.assertEqual(200, status)
            self.assertEqual("viewer", json.loads(body)["role"])
            self.assertIn("HttpOnly", headers["Set-Cookie"])
            status, _, _ = self.request("POST", "/api/v1/auth/pair", {"code": result["code"]})
            self.assertEqual(401, status)
        finally:
            self.module.pairing.PAIRINGS = original

    def test_admin_can_resume_and_exact_confirm_graceful_stop(self):
        original_run = self.module.run
        calls = []
        self.module.run = lambda *args, **kwargs: calls.append(args) or {"ok": True, "code": 0, "output": "completed"}
        try:
            _, viewer_cookie = self.login("viewer-secret")
            status, _, _ = self.request("POST", "/api/v1/action/start", {}, {"Cookie": viewer_cookie})
            self.assertEqual(403, status)
            admin, cookie = self.login("admin-secret")
            headers = {"Cookie": cookie, "X-CSRF-Token": admin["csrf"]}
            status, _, _ = self.request("POST", "/api/v1/action/stop", {}, headers)
            self.assertEqual(400, status)
            status, _, _ = self.request("POST", "/api/v1/action/start", {}, headers)
            self.assertEqual(200, status)
            status, _, _ = self.request("POST", "/api/v1/action/stop", {"confirm": "STOP SERVER"}, headers)
            self.assertEqual(200, status)
            self.assertTrue(any(call[-1] == "start" for call in calls))
            self.assertTrue(any(call[-2:] == ("stop", "STOP SERVER") for call in calls))
        finally:
            self.module.run = original_run

    def test_restore_routes_plan_then_require_exact_execution_confirmation(self):
        archive = self.backups / "palworld-daily-20260715T130000Z.tar.zst"
        archive.write_bytes(b"restore fixture")
        original_run = self.module.run
        calls = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            if str(args[0]).endswith("restore.py"):
                return {"ok": True, "code": 0, "output": json.dumps({"archive": str(archive), "steps": ["verify"]})}
            return {"ok": True, "code": 0, "output": "restore complete"}

        self.module.run = fake_run
        try:
            admin, cookie = self.login("admin-secret")
            headers = {"Cookie": cookie, "X-CSRF-Token": admin["csrf"]}
            status, _, body = self.request(
                "POST", "/api/v1/backups/restore/plan", {"name": archive.name}, headers,
            )
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["plan"]["steps"], ["verify"])
            status, _, _ = self.request(
                "POST", "/api/v1/backups/restore", {"name": archive.name, "confirm": "yes"}, headers,
            )
            self.assertEqual(status, 400)
            status, _, body = self.request(
                "POST", "/api/v1/backups/restore", {"name": archive.name, "confirm": "RESTORE WORLD"}, headers,
            )
            self.assertEqual(status, 200)
            self.assertIn("restore complete", json.loads(body)["output"])
            self.assertTrue(any(str(call[2]).endswith("ops-restore.sh") for call in calls if len(call) > 2))
        finally:
            self.module.run = original_run

    def test_backup_inventory_is_bounded_and_download_is_admin_only(self):
        archive = self.backups / "palworld-daily-20260715T120000Z.tar.zst"
        archive.write_bytes(b"fixture archive")
        pathlib.Path(str(archive) + ".sha256").write_text("fixture")
        pathlib.Path(str(archive) + ".manifest.json").write_text(json.dumps({"tier": "daily", "verified": True}))
        viewer, viewer_cookie = self.login("viewer-secret")
        status, _, body = self.request("GET", "/api/v1/backups", headers={"Cookie": viewer_cookie})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["backups"][0]["name"], archive.name)
        status, _, _ = self.request("GET", f"/api/v1/backups/{archive.name}/download", headers={"Cookie": viewer_cookie})
        self.assertEqual(status, 403)
        _, admin_cookie = self.login("admin-secret")
        status, headers, body = self.request("GET", f"/api/v1/backups/{archive.name}/download", headers={"Cookie": admin_cookie})
        self.assertEqual(status, 200)
        self.assertEqual(body, b"fixture archive")
        self.assertIn("attachment", headers["Content-Disposition"])
        status, _, _ = self.request("GET", "/api/v1/backups/%2e%2e%2fetc%2fpasswd/download", headers={"Cookie": admin_cookie})
        self.assertEqual(status, 404)
        self.assertTrue(viewer["csrf"])

    def test_redaction_covers_nested_identifiers_and_credentials(self):
        value = self.module.redact({
            "userId": "steam_123456", "nested": ["ip=192.168.1.8 token=abc", {"AdminPassword": "secret"}],
        })
        self.assertEqual(value["userId"], "<redacted>")
        self.assertEqual(value["nested"][1]["AdminPassword"], "<redacted>")
        self.assertNotIn("192.168.1.8", value["nested"][0])
        self.assertNotIn("abc", value["nested"][0])

    def test_history_is_time_bounded_and_downsampled(self):
        database = self.state / "history.sqlite3"
        connection = sqlite3.connect(database)
        connection.execute("create table samples(ts integer primary key, players integer, fps real, frame_ms real, uptime integer, day integer, rss_bytes integer, cpu_ticks integer, restarts integer)")
        now = int(time.time())
        connection.executemany(
            "insert into samples values(?,?,?,?,?,?,?,?,?)",
            [(now - offset, offset % 4, 60.0, 16.6, offset, 1, 1024, offset, 0) for offset in range(100)],
        )
        connection.commit()
        connection.close()
        samples = self.module.history_samples(hours=1, limit=10)
        self.assertLessEqual(len(samples), 11)
        self.assertEqual(samples[-1]["timestamp"], now)

    def test_offline_player_history_is_searchable_and_identity_scoped(self):
        database = self.state / "history.sqlite3"
        connection = sqlite3.connect(database)
        connection.execute("create table if not exists player_presence(user_key text primary key,name text not null,first_seen integer not null,last_seen integer not null,sessions integer not null,total_seconds integer not null,online integer not null,joined_at integer)")
        connection.execute("delete from player_presence")
        connection.executemany("insert into player_presence values(?,?,?,?,?,?,?,?)", [
            ("steam_alice", "Alice", 100, 300, 3, 7200, 0, None),
            ("steam_bob", "Bob", 200, 400, 1, 60, 1, 400),
        ])
        connection.commit(); connection.close()
        _, viewer_cookie = self.login("viewer-secret")
        status, _, body = self.request("GET", "/api/v1/players/history?search=ali", headers={"Cookie": viewer_cookie})
        self.assertEqual(status, 200)
        players = json.loads(body)["players"]
        self.assertEqual([player["name"] for player in players], ["Alice"])
        self.assertNotIn("user_key", players[0])
        _, moderator_cookie = self.login("moderator-secret")
        status, _, body = self.request("GET", "/api/v1/players/history", headers={"Cookie": moderator_cookie})
        self.assertEqual(status, 200)
        self.assertIn("user_key", json.loads(body)["players"][0])

    def test_invalid_history_bounds_are_rejected(self):
        _, viewer_cookie = self.login("viewer-secret")
        status, _, body = self.request(
            "GET", "/api/v1/metrics/history?hours=forever", headers={"Cookie": viewer_cookie},
        )
        self.assertEqual(status, 400)
        self.assertIn("must be an integer", json.loads(body)["error"])

    def test_save_intelligence_is_browsable_redacted_and_admin_scanned(self):
        report = self.state / "save-intelligence.json"
        report.write_text(json.dumps({
            "status": "compatible", "world": "WORLD-ID", "level_sav": {"sha256": "secret-hash"},
            "counts": {"players": 1, "pals": 2, "guilds": 1, "bases": 1, "map_objects": 3},
            "schema": {"missing_required": [], "errors": []},
            "players": [{"uid": "platform-id", "instance_id": "character-id", "name": "Alice", "pals": []}],
            "guilds": [{"id": "guild-1", "admin_uid": "platform-id", "name": "Builders", "members": []}],
            "bases": [{"id": "base-1", "guild_id": "guild-1", "position": {"x": 1, "y": 2}}],
            "map_objects": [],
        }))
        original_run = self.module.run

        def fake_run(*args, **kwargs):
            return {"ok": True, "code": 0, "output": json.dumps({
                "status": "compatible", "counts": {"players": 1, "pals": 2},
            })}

        self.module.run = fake_run
        try:
            viewer, viewer_cookie = self.login("viewer-secret")
            status, _, body = self.request("GET", "/api/v1/save/intelligence", headers={"Cookie": viewer_cookie})
            self.assertEqual(status, 200)
            payload = json.loads(body)
            self.assertEqual("Alice", payload["players"][0]["name"])
            self.assertEqual("<redacted>", payload["players"][0]["uid"])
            self.assertEqual("guild-1", payload["guilds"][0]["id"])
            status, _, _ = self.request("POST", "/api/v1/save/intelligence/scan", {}, {
                "Cookie": viewer_cookie, "X-CSRF-Token": viewer["csrf"],
            })
            self.assertEqual(status, 403)
            admin, admin_cookie = self.login("admin-secret")
            status, _, body = self.request("POST", "/api/v1/save/intelligence/scan", {}, {
                "Cookie": admin_cookie, "X-CSRF-Token": admin["csrf"],
            })
            self.assertEqual(status, 200)
            self.assertEqual("compatible", json.loads(body)["status"])
        finally:
            self.module.run = original_run
            report.unlink(missing_ok=True)

    def test_game_data_capability_is_authenticated_and_stale_actors_are_withheld(self):
        (self.state / "game-data.json").write_text(json.dumps({
            "state": "ready", "captured_at": int(time.time()) - 181, "last_attempt_at": int(time.time()),
            "counts": {"base_pals": 1}, "actors": [{"kind": "BaseCampPal", "name": "Worker"}],
        }))
        status, _, _ = self.request("GET", "/api/v1/game-data")
        self.assertEqual(401, status)
        admin, cookie = self.login("admin-secret")
        status, _, body = self.request("GET", "/api/v1/game-data", headers={"Cookie": cookie})
        self.assertEqual(200, status)
        data = json.loads(body)
        self.assertEqual("stale", data["state"])
        self.assertEqual([], data["actors"])
        self.assertEqual(1, data["counts"]["base_pals"])
        (self.state / "game-data.json").unlink(missing_ok=True)

    def test_named_map_locations_are_bounded_and_authenticated(self):
        public = self.root / "public"
        public.mkdir(exist_ok=True)
        (public / "locations.json").write_text(json.dumps([
            {"id": "poi-1", "label": "Named place", "type": "fastTravelPoint",
             "location": {"X": 1.5, "Y": 2.5, "Z": 3.5}},
            {"id": "bad", "label": "Bad", "type": "bad", "location": {"X": "x", "Y": 2, "Z": 3}},
        ]))
        original = self.module.PUBLIC
        self.module.PUBLIC = public
        try:
            status, _, _ = self.request("GET", "/api/v1/map/locations")
            self.assertEqual(401, status)
            _, cookie = self.login("viewer-secret")
            status, _, body = self.request("GET", "/api/v1/map/locations", headers={"Cookie": cookie})
            self.assertEqual(200, status)
            rows = json.loads(body)["locations"]
            self.assertEqual(1, len(rows))
            self.assertEqual({"x": 1.5, "y": 2.5, "z": 3.5}, rows[0]["position"])
        finally:
            self.module.PUBLIC = original

    def test_file_manager_browse_upload_read_and_quarantine_are_admin_only(self):
        _, viewer_cookie = self.login("viewer-secret")
        status, _, _ = self.request("GET", "/api/v1/files?root=config", headers={"Cookie": viewer_cookie})
        self.assertEqual(403, status)
        admin, cookie = self.login("admin-secret")
        body = b"[Section]\nKey=Value\n"
        digest = __import__("hashlib").sha256(body).hexdigest()
        query = urllib.parse.urlencode({"root": "config", "path": "Managed.ini"})
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        connection.request("PUT", f"/api/v1/files/upload?{query}", body=body, headers={
            "Cookie": cookie, "X-CSRF-Token": admin["csrf"], "Content-Length": str(len(body)),
            "X-Content-SHA256": digest, "X-File-Confirmation": "UPLOAD FILE",
        })
        response = connection.getresponse(); uploaded = json.loads(response.read()); connection.close()
        self.assertEqual(200, response.status); self.assertEqual("installed", uploaded["status"])
        status, _, content = self.request("GET", "/api/v1/files?root=config&path=Managed.ini&read=true", headers={"Cookie": cookie})
        self.assertEqual(200, status); self.assertEqual(body.decode(), json.loads(content)["content"])
        status, _, _ = self.request("POST", "/api/v1/files/quarantine", {
            "root": "config", "path": "Managed.ini", "confirm": "QUARANTINE FILE",
        }, {"Cookie": cookie, "X-CSRF-Token": admin["csrf"]})
        self.assertEqual(200, status)
        self.assertFalse((self.install / "Pal/Saved/Config/LinuxServer/Managed.ini").exists())

    def test_logs_are_bounded_literally_filtered_and_redacted(self):
        original_run = self.module.run

        def fake_run(*args, **kwargs):
            self.assertEqual(args[0], "journalctl")
            self.assertIn("-60 minutes", args)
            return {
                "ok": True,
                "code": 0,
                "output": "INFO ready at 127.0.0.1\nERROR token=secret at 192.168.1.9\nINFO done",
            }

        self.module.run = fake_run
        try:
            _, cookie = self.login("viewer-secret")
            status, _, body = self.request(
                "GET", "/api/v1/logs?unit=palworld.service&lines=100&since_minutes=60&filter=error",
                headers={"Cookie": cookie},
            )
            self.assertEqual(status, 200)
            payload = json.loads(body)
            self.assertEqual(payload["matched"], 1)
            self.assertIn("ERROR", payload["output"])
            self.assertNotIn("secret", payload["output"])
            self.assertNotIn("192.168.1.9", payload["output"])
            status, _, _ = self.request(
                "GET", f"/api/v1/logs?filter={'a' * 81}", headers={"Cookie": cookie},
            )
            self.assertEqual(status, 400)
        finally:
            self.module.run = original_run

    def test_diagnostic_bundle_is_generated_ephemerally_and_admin_only(self):
        original_run = self.module.run

        def fake_run(*args, **kwargs):
            destination = pathlib.Path(kwargs["cwd"]) / "palworld-support-fixture.tar.gz"
            destination.write_bytes(b"redacted bundle")
            return {"ok": True, "code": 0, "output": str(destination)}

        self.module.run = fake_run
        try:
            _, viewer_cookie = self.login("viewer-secret")
            status, _, _ = self.request(
                "GET", "/api/v1/diagnostics/bundle", headers={"Cookie": viewer_cookie},
            )
            self.assertEqual(status, 403)
            _, admin_cookie = self.login("admin-secret")
            status, headers, body = self.request(
                "GET", "/api/v1/diagnostics/bundle", headers={"Cookie": admin_cookie},
            )
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], "application/gzip")
            self.assertEqual(body, b"redacted bundle")
        finally:
            self.module.run = original_run

    def test_configuration_health_and_recovery_are_admin_only_and_exactly_confirmed(self):
        original_run = self.module.run
        calls = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            output = {"ok": False, "world": {"corrupted": True}, "engine": {"corrupted": False}}
            if any(str(item).endswith("ops-config-recovery.sh") for item in args):
                output = {"kind": "world", "restart_required": True}
            return {"ok": True, "code": 0, "output": json.dumps(output)}

        self.module.run = fake_run
        try:
            _, viewer_cookie = self.login("viewer-secret")
            status, _, _ = self.request("GET", "/api/v1/diagnostics/config", headers={"Cookie": viewer_cookie})
            self.assertEqual(status, 403)
            admin, cookie = self.login("admin-secret")
            headers = {"Cookie": cookie, "X-CSRF-Token": admin["csrf"]}
            status, _, body = self.request("GET", "/api/v1/diagnostics/config", headers={"Cookie": cookie})
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body)["world"]["corrupted"])
            status, _, _ = self.request("POST", "/api/v1/diagnostics/config/recover", {"kind": "world", "confirm": "yes"}, headers)
            self.assertEqual(status, 400)
            status, _, body = self.request("POST", "/api/v1/diagnostics/config/recover", {"kind": "world", "confirm": "RECOVER CONFIG"}, headers)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["kind"], "world")
            command = next(call for call in calls if any(str(item).endswith("ops-config-recovery.sh") for item in call))
            self.assertIn("RECOVER CONFIG", command)
        finally:
            self.module.run = original_run

    def test_raw_settings_routes_are_admin_only_and_exactly_confirmed(self):
        original_run = self.module.run
        calls = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            if args[1] == "raw-show":
                output = {"content": "OptionSettings=(ExpRate=1.0)", "max_bytes": 49152}
            else:
                output = {"changed": True, "diff": "-ExpRate=1.0\n+ExpRate=2.0"}
            return {"ok": True, "code": 0, "output": json.dumps(output)}

        self.module.run = fake_run
        try:
            _, viewer_cookie = self.login("viewer-secret")
            status, _, _ = self.request(
                "GET", "/api/v1/settings/raw", headers={"Cookie": viewer_cookie},
            )
            self.assertEqual(status, 403)
            admin, admin_cookie = self.login("admin-secret")
            headers = {"Cookie": admin_cookie, "X-CSRF-Token": admin["csrf"]}
            status, _, body = self.request(
                "GET", "/api/v1/settings/raw", headers={"Cookie": admin_cookie},
            )
            self.assertEqual(status, 200)
            self.assertIn("OptionSettings", json.loads(body)["content"])
            status, _, _ = self.request(
                "POST", "/api/v1/settings/raw/apply", {"content": "fixture", "confirm": "yes"}, headers,
            )
            self.assertEqual(status, 400)
            status, _, body = self.request(
                "POST", "/api/v1/settings/raw/apply",
                {"content": "fixture", "confirm": "APPLY RAW SETTINGS"}, headers,
            )
            self.assertEqual(status, 200)
            self.assertTrue(any(call[1] == "raw-apply" for call in calls))
            self.assertTrue(json.loads(body)["changed"])
        finally:
            self.module.run = original_run

    def test_engine_and_launch_configuration_routes_are_admin_only_and_confirmed(self):
        original_run = self.module.run
        calls = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            command = args[1]
            if command == "schema":
                output = {"engine": {"NetServerMaxTickRate": {"type": "integer"}}, "launch": {}}
            elif command == "raw-show":
                output = {"content": "[Engine]\nFixture=True\n", "max_bytes": 65536}
            else:
                output = {"restart_required": True, "command": command}
            return {"ok": True, "code": 0, "output": json.dumps(output)}

        self.module.run = fake_run
        try:
            _, viewer_cookie = self.login("viewer-secret")
            status, _, _ = self.request("GET", "/api/v1/server-config/schema", headers={"Cookie": viewer_cookie})
            self.assertEqual(status, 403)
            admin, cookie = self.login("admin-secret")
            headers = {"Cookie": cookie, "X-CSRF-Token": admin["csrf"]}
            status, _, body = self.request("GET", "/api/v1/server-config/schema", headers={"Cookie": cookie})
            self.assertEqual(status, 200)
            self.assertIn("NetServerMaxTickRate", json.loads(body)["engine"])
            status, _, _ = self.request("POST", "/api/v1/server-config/apply", {"engine": {}, "confirm": "yes"}, headers)
            self.assertEqual(status, 400)
            status, _, body = self.request("POST", "/api/v1/server-config/apply", {
                "engine": {"NetServerMaxTickRate": 60}, "launch": {}, "confirm": "APPLY SERVER CONFIG",
            }, headers)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["command"], "apply")
            status, _, _ = self.request("POST", "/api/v1/server-config/raw/apply", {
                "content": "[Engine]\nFixture=True\n", "confirm": "yes",
            }, headers)
            self.assertEqual(status, 400)
            status, _, body = self.request("POST", "/api/v1/server-config/raw/apply", {
                "content": "[Engine]\nFixture=True\n", "confirm": "APPLY ENGINE INI",
            }, headers)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["command"], "raw-apply")
            self.assertTrue(any(call[1] == "apply" and "APPLY SERVER CONFIG" in call for call in calls))
        finally:
            self.module.run = original_run

    def test_timed_settings_events_are_bounded_confirmed_and_cancel_to_restore(self):
        jobs_file = self.state / "jobs.json"
        jobs_file.unlink(missing_ok=True)
        admin, cookie = self.login("admin-secret")
        headers = {"Cookie": cookie, "X-CSRF-Token": admin["csrf"]}
        payload = {
            "type": "settings_event", "due_at": int(time.time()) + 3600,
            "preset": "double_xp", "duration_seconds": 86400,
        }
        status, _, _ = self.request("POST", "/api/v1/jobs", payload, headers)
        self.assertEqual(status, 400)
        payload["confirm"] = "SCHEDULE TIMED EVENT"
        status, _, body = self.request("POST", "/api/v1/jobs", payload, headers)
        self.assertEqual(status, 201)
        job = json.loads(body)
        self.assertEqual(job["phase"], "pending")
        stored = json.loads(jobs_file.read_text())
        stored[0].update({"phase": "active", "next_run": int(time.time()) + 86400})
        jobs_file.write_text(json.dumps(stored))
        status, _, _ = self.request("POST", f"/api/v1/jobs/{job['id']}/cancel", {}, headers)
        self.assertEqual(status, 200)
        cancelled = json.loads(jobs_file.read_text())[0]
        self.assertTrue(cancelled["enabled"])
        self.assertTrue(cancelled["cancel_requested"])
        self.assertLessEqual(cancelled["next_run"], int(time.time()))
        jobs_file.unlink(missing_ok=True)

    def test_admin_rcon_whitelist_and_scheduled_command_contracts(self):
        jobs_file = self.state / "jobs.json"
        jobs_file.unlink(missing_ok=True)
        original_run = self.module.run
        calls = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            script = str(args[0])
            if script.endswith("rcon-console.py"):
                action = args[1]
                if action == "overview":
                    output = {"configured": True, "saved": [], "history": [], "catalog": []}
                elif action == "saved-list": output = [{"id": 7, "name": "Info", "command": "Info"}]
                elif action == "validate": output = {"ok": True, "command": args[2]}
                else: output = {"ok": True, "command": "Info", "output": "server info"}
                return {"ok": True, "code": 0, "output": json.dumps(output)}
            if script.endswith("whitelist-manager.py"):
                return {"ok": True, "code": 0, "output": json.dumps({"enabled": False, "entries": []})}
            if script == "sudo":
                return {"ok": True, "code": 0, "output": json.dumps({"ok": True, "enabled": True})}
            return {"ok": False, "code": 1, "output": "unexpected command"}

        self.module.run = fake_run
        try:
            viewer, viewer_cookie = self.login("viewer-secret")
            status, _, _ = self.request("GET", "/api/v1/rcon", headers={"Cookie": viewer_cookie})
            self.assertEqual(status, 403)
            admin, cookie = self.login("admin-secret")
            headers = {"Cookie": cookie, "X-CSRF-Token": admin["csrf"]}
            status, _, body = self.request("GET", "/api/v1/rcon", headers={"Cookie": cookie})
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body)["configured"])
            status, _, _ = self.request("POST", "/api/v1/rcon/execute", {"command": "Info"}, headers)
            self.assertEqual(status, 400)
            status, _, body = self.request("POST", "/api/v1/rcon/execute", {
                "command": "Info", "confirm": "EXECUTE RCON",
            }, headers)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["output"], "server info")
            status, _, _ = self.request("POST", "/api/v1/whitelist/replace", {
                "entries": [{"user_id": "steam_alice", "name": "Alice"}],
            }, headers)
            self.assertEqual(status, 400)
            status, _, _ = self.request("POST", "/api/v1/whitelist/replace", {
                "entries": [{"user_id": "steam_alice", "name": "Alice"}],
                "confirm": "REPLACE WHITELIST",
            }, headers)
            self.assertEqual(status, 200)
            status, _, _ = self.request("POST", "/api/v1/jobs", {
                "type": "rcon", "due_at": int(time.time()) + 3600, "command": "Info",
                "interval_seconds": 300, "confirm": "SCHEDULE RCON COMMAND",
            }, headers)
            self.assertEqual(status, 201)
            job = json.loads(jobs_file.read_text())[0]
            self.assertEqual(job["command"], "Info")
            self.assertEqual(job["interval_seconds"], 300)
            status, _, _ = self.request("POST", "/api/v1/rcon/toggle", {
                "enabled": "true", "confirm": "ENABLE PRIVATE RCON",
            }, headers)
            self.assertEqual(status, 400)
            status, _, _ = self.request("POST", "/api/v1/rcon/toggle", {
                "enabled": True, "confirm": "ENABLE PRIVATE RCON",
            }, headers)
            self.assertEqual(status, 200)
            self.assertTrue(any(any(str(item).endswith("ops-rcon-toggle.sh") for item in call) for call in calls))
        finally:
            self.module.run = original_run
            jobs_file.unlink(missing_ok=True)

    def test_mod_configuration_routes_are_admin_only_and_exactly_confirmed(self):
        original_run = self.module.run
        calls = []
        fixture = {
            "paldefender": {"installed": True, "config_exists": True, "sha256": "a" * 64, "schema": {}, "values": {}},
            "ue4ss": {"installed": True, "mods": [{"name": "Fixture", "enabled": False}]},
        }

        def fake_run(*args, **kwargs):
            calls.append(args)
            if args[-1] == "status": output = json.dumps(fixture)
            elif "plan" in args: output = json.dumps({"changed": True, "current_sha256": "a" * 64})
            else: output = json.dumps({"ok": True})
            return {"ok": True, "code": 0, "output": output}

        self.module.run = fake_run
        try:
            _, viewer_cookie = self.login("viewer-secret")
            status, _, _ = self.request("GET", "/api/v1/mod-config", headers={"Cookie": viewer_cookie})
            self.assertEqual(403, status)
            admin, cookie = self.login("admin-secret")
            headers = {"Cookie": cookie, "X-CSRF-Token": admin["csrf"]}
            status, _, body = self.request("GET", "/api/v1/mod-config", headers={"Cookie": cookie})
            self.assertEqual(200, status)
            self.assertTrue(json.loads(body)["paldefender"]["installed"])
            status, _, body = self.request(
                "POST", "/api/v1/mod-config/plan", {"updates": {"logRCON": True}}, headers,
            )
            self.assertEqual(200, status)
            self.assertTrue(json.loads(body)["changed"])
            status, _, _ = self.request(
                "POST", "/api/v1/mod-config/apply",
                {"updates": {}, "expected_sha256": "a" * 64, "confirm": "yes"}, headers,
            )
            self.assertEqual(400, status)
            status, _, _ = self.request(
                "POST", "/api/v1/mod-config/apply",
                {"updates": {"logRCON": True}, "expected_sha256": "a" * 64, "confirm": "APPLY MOD CONFIG"}, headers,
            )
            self.assertEqual(200, status)
            status, _, _ = self.request(
                "POST", "/api/v1/mod-config/lua-state",
                {"name": "Fixture", "enabled": "yes", "confirm": "SET LUA MOD STATE"}, headers,
            )
            self.assertEqual(400, status)
            status, _, _ = self.request(
                "POST", "/api/v1/mod-config/lua-state",
                {"name": "Fixture", "enabled": True, "confirm": "SET LUA MOD STATE"}, headers,
            )
            self.assertEqual(200, status)
            self.assertTrue(any("--expected-sha256" in call for call in calls))
            self.assertTrue(any("lua-set" in call for call in calls))
        finally:
            self.module.run = original_run

    def test_mod_lifecycle_routes_are_admin_only_plan_bound_and_exactly_confirmed(self):
        original_run = self.module.run
        calls = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            if args[-1] == "status":
                output = {"runtime": "wine-windows", "supported": True, "components": {}}
            elif "plan" in args:
                output = {
                    "component": "paldefender", "action": "install",
                    "plan_hash": "c" * 64, "confirmation": "INSTALL PALDEFENDER",
                }
            else:
                output = {"component": "paldefender", "action": "install", "installed": True}
            return {"ok": True, "code": 0, "output": json.dumps(output)}

        self.module.run = fake_run
        try:
            _, viewer_cookie = self.login("viewer-secret")
            status, _, _ = self.request("GET", "/api/v1/mod-lifecycle", headers={"Cookie": viewer_cookie})
            self.assertEqual(status, 403)
            admin, cookie = self.login("admin-secret")
            headers = {"Cookie": cookie, "X-CSRF-Token": admin["csrf"]}
            status, _, body = self.request("GET", "/api/v1/mod-lifecycle", headers={"Cookie": cookie})
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body)["supported"])
            status, _, body = self.request("POST", "/api/v1/mod-lifecycle/plan", {
                "component": "paldefender", "action": "install",
            }, headers)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["plan_hash"], "c" * 64)
            status, _, _ = self.request("POST", "/api/v1/mod-lifecycle/execute", {
                "component": "paldefender", "action": "install",
                "expected_plan_hash": "c" * 64, "confirm": "yes",
            }, headers)
            self.assertEqual(status, 400)
            status, _, _ = self.request("POST", "/api/v1/mod-lifecycle/execute", {
                "component": "paldefender", "action": "install",
                "expected_plan_hash": "c" * 64, "confirm": "INSTALL PALDEFENDER",
            }, headers)
            self.assertEqual(status, 200)
            execute = next(call for call in calls if "--expected-plan-hash" in call)
            self.assertIn("c" * 64, execute)
            self.assertIn("INSTALL PALDEFENDER", execute)
        finally:
            self.module.run = original_run

    def test_typed_player_actions_are_admin_only_and_plan_bound(self):
        original_run = self.module.run
        calls = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            if args[-1] == "capabilities":
                output = {"backend": "paldefender", "actions": {"teleport": {"available": True}}}
            elif "plan" in args:
                output = {"action": "teleport", "reviewed_sha256": "b" * 64, "confirmation": "TELEPORT PLAYER"}
            else:
                output = {"ok": True, "verification": "position-observed"}
            return {"ok": True, "code": 0, "output": json.dumps(output)}

        self.module.run = fake_run
        try:
            _, viewer_cookie = self.login("viewer-secret")
            status, _, _ = self.request("GET", "/api/v1/player-actions", headers={"Cookie": viewer_cookie})
            self.assertEqual(status, 403)
            admin, cookie = self.login("admin-secret")
            headers = {"Cookie": cookie, "X-CSRF-Token": admin["csrf"]}
            status, _, body = self.request("GET", "/api/v1/player-actions", headers={"Cookie": cookie})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["backend"], "paldefender")
            payload = {"action": "teleport", "target": "steam_1", "x": 1, "y": 2, "z": 3}
            status, _, body = self.request("POST", "/api/v1/player-actions/plan", {"payload": payload}, headers)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["reviewed_sha256"], "b" * 64)
            status, _, body = self.request("POST", "/api/v1/player-actions/execute", {
                "payload": payload, "expected_sha256": "b" * 64, "confirm": "TELEPORT PLAYER",
            }, headers)
            self.assertEqual(status, 200)
            execute = next(call for call in calls if "execute" in call)
            self.assertIn("--expected-sha256", execute)
            self.assertIn("--actor", execute)
        finally:
            self.module.run = original_run

    def test_offline_save_repair_is_admin_only_and_uses_privileged_plan_apply(self):
        original_run = self.module.run
        calls = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            action = args[3] if len(args) > 3 else ""
            if action == "diagnose":
                output = {"status": "diagnosed", "diagnostics": {"players": 2}}
            elif action == "plan":
                output = {"status": "ready", "reviewed_sha256": "c" * 64,
                          "confirmation": "RENAME PLAYER"}
            else:
                output = {"status": "applied", "observed": {"effects": {"after_name": "Grace"}}}
            return {"ok": True, "code": 0, "output": json.dumps(output)}

        self.module.run = fake_run
        try:
            _, viewer_cookie = self.login("viewer-secret")
            status, _, _ = self.request("GET", "/api/v1/save/repair", headers={"Cookie": viewer_cookie})
            self.assertEqual(403, status)
            admin, cookie = self.login("admin-secret")
            headers = {"Cookie": cookie, "X-CSRF-Token": admin["csrf"]}
            status, _, body = self.request("GET", "/api/v1/save/repair", headers={"Cookie": cookie})
            self.assertEqual(200, status)
            self.assertIn("rename_player", json.loads(body)["actions"])
            self.assertIn("replace_player", json.loads(body)["actions"])
            self.assertIn("cleanup_duplicates", json.loads(body)["actions"])
            self.assertNotIn("duplicate cleanup", json.loads(body)["unsupported"])
            self.assertIn("cleanup_graph", json.loads(body)["actions"])
            self.assertNotIn("broken-reference cleanup", json.loads(body)["unsupported"])
            self.assertIn("delete_inactive_players", json.loads(body)["actions"])
            self.assertIn("transfer_player", json.loads(body)["actions"])
            self.assertNotIn("character copy across worlds", json.loads(body)["unsupported"])
            self.assertNotIn("base-owning inactive-player deletion", json.loads(body)["unsupported"])
            self.assertIn("edit_inventory_slot", json.loads(body)["actions"])
            self.assertIn("edit_player_progression", json.loads(body)["actions"])
            self.assertIn("edit_owned_pal", json.loads(body)["actions"])
            status, _, body = self.request("POST", "/api/v1/save/repair/diagnose", {}, headers)
            self.assertEqual(200, status)
            payload = {"action": "rename_player", "uid": "a" * 32, "new_name": "Grace"}
            status, _, body = self.request("POST", "/api/v1/save/repair/plan", {"payload": payload}, headers)
            self.assertEqual(200, status)
            self.assertEqual("c" * 64, json.loads(body)["reviewed_sha256"])
            status, _, body = self.request("POST", "/api/v1/save/repair/apply", {
                "payload": payload, "reviewed_sha256": "c" * 64, "confirm": "RENAME PLAYER",
            }, headers)
            self.assertEqual(200, status)
            privileged = [call for call in calls if any(str(item).endswith("ops-save-repair.sh") for item in call)]
            self.assertTrue(all(call[:2] == ("sudo", "-n") for call in privileged))
            self.assertTrue(any("apply" in call and "c" * 64 in call for call in privileged))
        finally:
            self.module.run = original_run

    def test_update_plan_binds_exact_confirmed_install_to_reviewed_build(self):
        original_run = self.module.run
        calls = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            if str(args[0]).endswith("update-control.py"):
                return {"ok": True, "code": 0, "output": json.dumps({
                    "target_build": "12345", "update_available": True, "executable": True,
                })}
            return {"ok": True, "code": 0, "output": "updated"}

        self.module.run = fake_run
        try:
            admin, cookie = self.login("admin-secret")
            headers = {"Cookie": cookie, "X-CSRF-Token": admin["csrf"]}
            status, _, body = self.request("POST", "/api/v1/updates/plan", {}, headers)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["target_build"], "12345")
            status, _, _ = self.request(
                "POST", "/api/v1/updates/install",
                {"target_build": "12345", "confirm": "yes"}, headers,
            )
            self.assertEqual(status, 400)
            status, _, body = self.request(
                "POST", "/api/v1/updates/install",
                {"target_build": "12345", "confirm": "INSTALL UPDATE"}, headers,
            )
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["output"], "updated")
            install = next(call for call in calls if any(str(item).endswith("ops-update.sh") for item in call))
            self.assertIn("12345", install)
            self.assertIn("INSTALL UPDATE", install)
        finally:
            self.module.run = original_run

    def test_build_rollback_requires_plan_numeric_target_and_exact_confirmation(self):
        original_run = self.module.run
        calls = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            if "plan" in args:
                output = json.dumps({"target_build": "100", "save_tree_excluded": True})
            else:
                output = "rollback complete"
            return {"ok": True, "code": 0, "output": output}

        self.module.run = fake_run
        try:
            admin, cookie = self.login("admin-secret")
            headers = {"Cookie": cookie, "X-CSRF-Token": admin["csrf"]}
            status, _, body = self.request(
                "POST", "/api/v1/builds/plan", {"target_build": "100"}, headers,
            )
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body)["save_tree_excluded"])
            status, _, _ = self.request(
                "POST", "/api/v1/builds/rollback",
                {"target_build": "100", "confirm": "yes"}, headers,
            )
            self.assertEqual(status, 400)
            status, _, body = self.request(
                "POST", "/api/v1/builds/rollback",
                {"target_build": "100", "confirm": "ROLLBACK BUILD"}, headers,
            )
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["output"], "rollback complete")
            command = next(call for call in calls if any(str(item).endswith("ops-build-rollback.sh") for item in call))
            self.assertIn("100", command)
            self.assertIn("ROLLBACK BUILD", command)
            status, _, _ = self.request(
                "POST", "/api/v1/builds/plan", {"target_build": "../100"}, headers,
            )
            self.assertEqual(status, 400)
        finally:
            self.module.run = original_run


if __name__ == "__main__":
    unittest.main()
