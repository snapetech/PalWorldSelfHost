import importlib.util
import os
import pathlib
import socket
import struct
import tempfile
import threading
import unittest


ROOT = pathlib.Path(__file__).parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def receive_packet(connection):
    size = struct.unpack("<i", connection.recv(4))[0]
    payload = b""
    while len(payload) < size:
        payload += connection.recv(size - len(payload))
    identifier, kind = struct.unpack("<ii", payload[:8])
    return identifier, kind, payload[8:-2].decode()


class FakeRcon:
    def __init__(self, password="secret", response_id=2):
        self.password = password
        self.response_id = response_id
        self.commands = []
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen()
        self.port = self.socket.getsockname()[1]
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def send(self, connection, identifier, kind, body=""):
        payload = struct.pack("<ii", identifier, kind) + body.encode() + b"\0\0"
        connection.sendall(struct.pack("<i", len(payload)) + payload)

    def serve(self):
        connection, _ = self.socket.accept()
        with connection:
            identifier, _, password = receive_packet(connection)
            self.send(connection, identifier if password == self.password else -1, 2)
            if password != self.password:
                return
            identifier, _, command = receive_packet(connection)
            self.commands.append(command)
            self.send(connection, self.response_id if self.response_id is not None else identifier, 0, f"ran:{command}")

    def close(self):
        self.thread.join(timeout=2)
        self.socket.close()


class RconTests(unittest.TestCase):
    def setUp(self):
        self.client = load(f"rcon_client_{id(self)}", ROOT / "scripts/rcon-client.py")

    def test_loopback_protocol_authenticates_and_executes(self):
        server = FakeRcon(response_id=0)
        try:
            output = self.client.execute("/Info", port=server.port, password="secret")
            self.assertEqual(output, "ran:Info")
            self.assertEqual(server.commands, ["Info"])
        finally:
            server.close()

    def test_auth_failure_and_input_boundaries(self):
        server = FakeRcon()
        try:
            with self.assertRaises(PermissionError):
                self.client.execute("Info", port=server.port, password="wrong")
        finally:
            server.close()
        for command in ("", "Info\nSave", "AdminPassword leaked"):
            with self.assertRaises(ValueError): self.client.validate_command(command)
        with self.assertRaises(ValueError):
            self.client.execute("Info", host="192.0.2.1", password="secret")

    def test_console_persists_saved_commands_and_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            os.environ["PALWORLD_STATE_DIR"] = temporary
            console = load(f"rcon_console_{id(self)}", ROOT / "scripts/rcon-console.py")
            console.DATABASE = pathlib.Path(temporary) / "rcon.sqlite3"
            console.ops.STATE = pathlib.Path(temporary)
            console.rcon.execute = lambda command: f"output:{command}"
            with self.assertRaises(ValueError): console.execute("Info")
            result = console.execute("Info", actor="admin", confirm="EXECUTE RCON")
            self.assertTrue(result["ok"])
            saved = console.save_command("Server info", "Info")
            self.assertEqual(console.saved_by_id(saved["id"])["command"], "Info")
            self.assertEqual(console.history(1)[0]["actor"], "admin")
            self.assertTrue(console.delete_saved(saved["id"])["ok"])

    def test_paldefender_catalog_uses_live_discovery_and_unknowns_fail_critical(self):
        with tempfile.TemporaryDirectory() as temporary:
            os.environ["PALWORLD_STATE_DIR"] = temporary
            console = load(f"rcon_discovery_{id(self)}", ROOT / "scripts/rcon-console.py")
            console.ops.STATE = pathlib.Path(temporary)
            console.rcon.execute = lambda command: "/version, tp getrconcmds FutureMutation FutureMutation bad!"
            catalog, discovered = console.discovered_catalog()
            self.assertTrue(discovered)
            self.assertEqual(4, len(catalog))
            indexed = {item["command"]: item for item in catalog}
            self.assertEqual("read", indexed["version"]["risk"])
            self.assertEqual("critical", indexed["tp"]["risk"])
            self.assertEqual("critical", indexed["FutureMutation"]["risk"])
            self.assertTrue(all(item["source"] == "paldefender" for item in catalog))

            console.rcon.execute = lambda command: "getrconcmds:0;version:0;tp:1;give:2;spawnpal:1;"
            current, discovered = console.discovered_catalog()
            self.assertTrue(discovered)
            self.assertEqual(
                ["getrconcmds", "version", "tp", "give", "spawnpal"],
                [item["command"] for item in current],
            )

            console.rcon.execute = lambda command: (_ for _ in ()).throw(ConnectionError("offline"))
            self.assertEqual(([], False), console.discovered_catalog())


if __name__ == "__main__": unittest.main()
