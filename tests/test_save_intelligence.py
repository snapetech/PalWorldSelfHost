import importlib.util
import argparse
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("save_intelligence", ROOT / "scripts" / "save-intelligence.py")
save_intelligence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(save_intelligence)


def prop(value):
    return {"value": value}


class SaveIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def fixture(self):
        player_raw = {
            "IsPlayer": prop(True), "NickName": prop("Ada"), "Level": prop(prop(12)),
            "Exp": prop(900), "LastJumpedLocation": prop({"x": 4, "y": 5, "z": 6}),
        }
        pal_raw = {
            "NickName": prop("Spark"), "CharacterID": prop("ElecCat"),
            "OwnerPlayerUId": prop("AAAA"), "Level": prop(prop(9)), "Rank": prop(prop(2)),
            "SlotId": prop({"ContainerId": prop({"ID": prop("party-1")})}),
            "PassiveSkillList": prop({"values": ["Runner"]}),
        }
        characters = [
            {"key": {"PlayerUId": prop("AAAA"), "InstanceId": prop("P1")},
             "value": {"RawData": {"value": {"object": {"SaveParameter": prop(player_raw)}}}}},
            {"key": {"PlayerUId": prop("0000"), "InstanceId": prop("PAL1")},
             "value": {"RawData": {"value": {"object": {"SaveParameter": prop(pal_raw)}}}}},
        ]
        guilds = [{"key": "G1", "value": {"RawData": {"value": {
            "group_type": "EPalGroupType::Guild", "group_id": "G1", "guild_name": "Builders",
            "admin_player_uid": "AAAA", "base_camp_level": 3, "base_ids": ["B1"],
            "players": [{"player_uid": "AAAA", "player_name": "Ada", "last_online_real_time": 123}],
        }}}}]
        bases = [{"key": "B1", "value": {"RawData": {"value": {
            "id": "B1", "name": "Hill", "group_id_belong_to": "G1", "state": 1,
            "transform": {"translation": {"x": 10, "y": 20, "z": 30}},
        }}, "WorkerDirector": prop({"RawData": prop({"container_id": "party-1"})})}}]
        items = [{"key": {"ID": prop("bag-1")}, "value": {"Slots": prop({"values": [
            {"RawData": {"value": {"item": {"static_id": "Wood"}, "count": 7}}},
        ]})}}]
        objects = [{"MapObjectId": prop("BuildObject_Wall"), "Model": prop({"RawData": {"value": {
            "base_camp_id_belong_to": "B1", "initital_transform_cache": {"translation": {"x": 11, "y": 21, "z": 31}},
        }}})}]
        sections = {
            "CharacterSaveParameterMap": prop(characters), "GroupSaveDataMap": prop(guilds),
            "BaseCampSaveData": prop(bases), "ItemContainerSaveData": prop(items),
            "MapObjectSaveData": prop(objects),
        }
        path = self.root / "Level.sav.json"
        path.write_text(json.dumps({"properties": {"worldSaveData": prop(sections)}}))
        return path

    def test_streamed_analysis_produces_profiles_guilds_inventory_and_map(self):
        indexes = {
            "containers": {"party1": {"uid": "aaaa", "kind": "party"}},
            "inventory": {"bag1": {"uid": "aaaa", "kind": "common"}},
            "paldeck": {"aaaa": ["ElecCat"]}, "positions": {},
        }
        result = save_intelligence.analyze(self.fixture(), indexes, {"eleccat": [
            {"id": "GenerateElectricity", "name": "Generating Electricity", "level": 1},
        ]})
        self.assertEqual("compatible", result["status"])
        self.assertEqual({"players": 1, "pals": 1, "guilds": 1, "bases": 1, "base_workers": 1, "map_objects": 1}, result["counts"])
        player = result["players"][0]
        self.assertEqual("Ada", player["name"])
        self.assertEqual("Spark", player["pals"][0]["name"])
        self.assertEqual("base", player["pals"][0]["location"])
        self.assertEqual("b1", player["pals"][0]["base_id"])
        self.assertEqual("Generating Electricity", player["pals"][0]["work_suitability"][0]["name"])
        self.assertEqual(1, result["bases"][0]["worker_count"])
        self.assertEqual({"item_id": "Wood", "count": 7}, player["inventory"]["common"][0])
        self.assertEqual(["ElecCat"], player["paldeck"])
        self.assertEqual("Builders", result["guilds"][0]["name"])
        self.assertEqual(10, result["bases"][0]["position"]["x"])

    def test_schema_drift_is_explicit(self):
        path = self.root / "empty.json"
        path.write_text('{"properties":{"worldSaveData":{"value":{}}}}')
        result = save_intelligence.analyze(path, {"containers": {}, "inventory": {}, "paldeck": {}, "positions": {}})
        self.assertEqual("degraded", result["status"])
        self.assertEqual(["BaseCampSaveData", "CharacterSaveParameterMap", "GroupSaveDataMap"], result["schema"]["missing_required"])

    def test_explicit_parser_must_match_pin(self):
        parser = self.root / "parser"
        parser.write_bytes(b"not the pinned parser")
        with self.assertRaisesRegex(RuntimeError, "pinned SHA-256"):
            save_intelligence.ensure_parser(parser)

    def test_parser_failure_replaces_stale_success_with_error_state(self):
        parser = self.root / "bad-parser"
        parser.write_bytes(b"bad")
        level = self.root / "Level.sav"
        level.write_bytes(b"fixture")
        output = self.root / "report.json"
        output.write_text('{"status":"compatible"}')
        args = argparse.Namespace(
            world=str(self.root), level=str(level), parser=str(parser), game_build="123",
            output=str(output), full=False,
        )
        self.assertEqual(1, save_intelligence.scan(args))
        report = json.loads(output.read_text())
        self.assertEqual("error", report["status"])
        self.assertEqual("parser", report["schema"]["errors"][0]["phase"])


if __name__ == "__main__":
    unittest.main()
