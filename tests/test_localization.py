import html.parser
import json
import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class VisibleStrings(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(); self.strings = []; self.stack = []; self.skip = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs); hidden = tag in {"script", "style"} or "locale-picker" in values.get("class", "").split()
        self.stack.append(hidden)
        if hidden: self.skip += 1
        if not self.skip:
            for name in ("placeholder", "aria-label", "title"):
                if values.get(name): self.strings.append(" ".join(values[name].split()))

    def handle_endtag(self, tag):
        if self.stack and self.stack.pop(): self.skip -= 1

    def handle_data(self, data):
        value = " ".join(data.split())
        if value and not self.skip: self.strings.append(value)


class LocalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads((ROOT / "admin/static/locales.json").read_text())

    def test_complete_chinese_and_japanese_static_operator_catalogs(self):
        self.assertEqual({"zh-Hans", "ja"}, set(self.catalog))
        parser = VisibleStrings(); parser.feed((ROOT / "admin/static/index.html").read_text())
        visible = set(parser.strings)
        for locale, translations in self.catalog.items():
            missing = sorted(visible - set(translations))
            self.assertFalse(missing, f"{locale} is missing: {missing}")

    def test_dynamic_operational_labels_have_both_translations(self):
        required = {
            "World process", "Private REST", "Health monitor", "Backup continuity", "Backup storage",
            "Steam update", "Maintenance", "Auto-pause", "Control-plane bind", "Public game probe",
            "No players are online.", "Action completed.", "Announcement sent.", "Scheduled work created.",
        }
        for locale, translations in self.catalog.items():
            self.assertFalse(required - set(translations), f"{locale} dynamic catalog incomplete")

    def test_cli_accepts_localized_help_and_rejects_unknown_locale(self):
        chinese = subprocess.run(["bash", str(ROOT / "scripts/palworldctl"), "--locale", "zh-Hans", "帮助"], text=True, capture_output=True)
        japanese = subprocess.run(["bash", str(ROOT / "scripts/palworldctl"), "--locale", "ja", "ヘルプ"], text=True, capture_output=True)
        invalid = subprocess.run(["bash", str(ROOT / "scripts/palworldctl"), "--locale", "xx", "help"], text=True, capture_output=True)
        self.assertEqual(0, chinese.returncode); self.assertIn("用法", chinese.stdout)
        self.assertEqual(0, japanese.returncode); self.assertIn("使用法", japanese.stdout)
        self.assertEqual(2, invalid.returncode); self.assertIn("unsupported locale", invalid.stderr)


if __name__ == "__main__": unittest.main()
