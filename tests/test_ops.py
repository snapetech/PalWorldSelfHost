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

if __name__ == '__main__': unittest.main()
