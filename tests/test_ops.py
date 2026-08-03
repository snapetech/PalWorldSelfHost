import importlib.util, json, pathlib, tempfile, unittest

ROOT = pathlib.Path(__file__).parents[1]
def load(name):
    spec=importlib.util.spec_from_file_location(name, ROOT/'scripts'/name)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module

class OpsTests(unittest.TestCase):
    def test_atomic_json_round_trip(self):
        ops=load('ops-lib.py')
        with tempfile.TemporaryDirectory() as d:
            path=pathlib.Path(d)/'state.json'; ops.atomic_json(path, {'ok': True})
            self.assertEqual(json.loads(path.read_text()), {'ok': True})
    def test_public_map_locations_are_bounded(self):
        items=json.loads((ROOT/'public/locations.json').read_text())
        self.assertGreater(len(items), 50)
        self.assertTrue(all(i['type'] in {'fastTravelPoint','towerTravelPoint'} for i in items))
    def test_public_player_map_hides_empty_state_and_keeps_refreshes_static(self):
        script=(ROOT/'public/app.js').read_text()
        styles=(ROOT/'public/style.css').read_text()
        page=(ROOT/'public/index.html').read_text()
        self.assertIn('$("#empty").hidden = players.length > 0', script)
        self.assertIn('.empty[hidden] { display: none; }', styles)
        self.assertIn('class: "marker-label"', script)
        self.assertIn('.marker-label rect', styles)
        self.assertNotIn('.marker { animation:', styles)
        self.assertNotIn('@keyframes arrive', styles)
        self.assertIn('/palworld/style.css?v=', page)
        self.assertIn('/palworld/app.js?v=', page)

    def test_public_status_defaults_match_the_live_static_origin(self):
        expected = "/srv/static/palworld"
        paths = [
            ROOT / "config/palworld-server.env.example",
            ROOT / "scripts/install.sh",
            ROOT / "scripts/export-public-status.py",
            ROOT / ".github/workflows/deploy-public.yml",
            ROOT / "scripts/deploy-public.sh",
        ]
        for path in paths:
            self.assertIn(expected, path.read_text(), str(path))
        self.assertNotIn("/var/www/palworld", "\n".join(path.read_text() for path in paths))

    def test_public_status_timer_recovers_after_reboot(self):
        timer = (ROOT / "systemd/palworld-public-status.timer").read_text()
        self.assertIn("OnCalendar=*-*-* *:*:00/15", timer)
        self.assertNotIn("OnBootSec=", timer)
        self.assertNotIn("OnUnitActiveSec=", timer)

if __name__ == '__main__': unittest.main()
