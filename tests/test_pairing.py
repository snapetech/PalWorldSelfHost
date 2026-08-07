import importlib.util
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pairing_test", ROOT / "scripts" / "pairing.py")
pairing = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(pairing)


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.temporary.name) / "pairing.json"

    def tearDown(self): self.temporary.cleanup()

    def test_code_is_role_scoped_single_use_and_not_stored_in_plaintext(self):
        result = pairing.create("moderator", 120, self.path, now=100)
        self.assertEqual(12, len(result["code"]))
        self.assertNotIn(result["code"], self.path.read_text())
        self.assertEqual("moderator", pairing.consume(result["code"].lower(), self.path, now=110))
        self.assertIsNone(pairing.consume(result["code"], self.path, now=111))

    def test_expired_invalid_and_unbounded_codes_are_refused(self):
        result = pairing.create("viewer", 60, self.path, now=100)
        self.assertIsNone(pairing.consume(result["code"], self.path, now=161))
        self.assertIsNone(pairing.consume("bad", self.path, now=110))
        with self.assertRaisesRegex(ValueError, "60-600"):
            pairing.create("viewer", 30, self.path)


if __name__ == "__main__": unittest.main()
