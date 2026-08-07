import copy
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("save_repair", ROOT / "scripts" / "save-repair.py")
save_repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(save_repair)


def prop(value):
    return {"value": value}


def player(uid, instance, name):
    raw = {"IsPlayer": prop(True), "NickName": prop(name), "OwnerPlayerUId": prop(uid)}
    return {
        "key": {"PlayerUId": prop(uid), "InstanceId": prop(instance)},
        "value": {"RawData": {"value": {"object": {"SaveParameter": prop(raw)}}}},
    }


def pal(owner_uid, instance, name="Lamball"):
    raw = {"CharacterID": prop(name), "OwnerPlayerUId": prop(owner_uid)}
    return {
        "key": {"PlayerUId": prop("00000000-0000-0000-0000-000000000000"),
                "InstanceId": prop(instance)},
        "value": {"RawData": {"value": {"object": {"SaveParameter": prop(raw)}}}},
    }


def fixture(players=None, guild_players=None):
    players = players or [player("aaaaaaaa-0000-0000-0000-000000000000",
                                 "11111111-0000-0000-0000-000000000000", "Ada")]
    guild_players = guild_players if guild_players is not None else [
        {"player_uid": "aaaaaaaa-0000-0000-0000-000000000000", "player_name": "Ada",
         "last_online_real_time": 123},
    ]
    sections = {
        "CharacterSaveParameterMap": prop(players),
        "GroupSaveDataMap": prop([{"value": {"RawData": {"value": {
            "group_type": "EPalGroupType::Guild", "players": guild_players,
        }}}}]),
        "BaseCampSaveData": prop([]),
    }
    return {
        "header": {"save_game_version": 3, "engine_version_major": 5,
                   "engine_version_minor": 1, "engine_version_patch": 1,
                   "custom_versions": [["00000000-0000-0000-0000-000000000001", 1]]},
        "properties": {"worldSaveData": prop(sections)}, "trailer": "AAAAAA==",
    }


def character_container(identity, instances):
    return {
        "key": {"ID": prop(identity)},
        "value": {"Slots": prop({"values": [
            {"RawData": prop({"player_uid": "00000000-0000-0000-0000-000000000000",
                               "instance_id": instance, "permission_tribe_id": 1})}
            for instance in instances
        ]})},
    }


def item_container(identity, dynamic_ids, group_id=None):
    value = {"Slots": prop({"values": [
        {"RawData": prop({"item": {"dynamic_id": {
            "local_id_in_created_world": dynamic_id}}})} for dynamic_id in dynamic_ids
    ]})}
    if group_id:
        value["BelongInfo"] = prop({"GroupID": prop(group_id)})
    return {"key": {"ID": prop(identity)}, "value": value}


def dynamic_item(identity):
    return {"RawData": prop({"id": {"local_id_in_created_world": identity}})}


def editable_item_container(identity, slot_index, item_id="None", stack_count=0,
                            dynamic_id="00000000-0000-0000-0000-000000000000"):
    return {"key": {"ID": prop(identity)}, "value": {"Slots": prop({"values": [{
        "SlotIndex": prop(slot_index),
        "ItemId": prop({"StaticId": prop(item_id), "DynamicId": prop({
            "LocalIdInCreatedWorld": prop(dynamic_id),
        })}),
        "StackCount": prop(stack_count),
    }]})}}


def transfer_player_save(uid, instance, group_id, character_ids, item_ids):
    return {"properties": {"SaveData": prop({
        "PlayerUId": prop(uid),
        "IndividualId": prop({"PlayerUId": prop(uid), "InstanceId": prop(instance)}),
        "GroupId": prop(group_id),
        "OtomoCharacterContainerId": prop({"ID": prop(character_ids[0])}),
        "PalStorageContainerId": prop({"ID": prop(character_ids[1])}),
        "InventoryInfo": prop({"CommonContainerId": prop({"ID": prop(item_ids[0])})}),
    })}}


def cross_world_fixture():
    identity = lambda number: f"{number:032x}"
    source_uid, target_uid = identity(101), identity(102)
    source_instance, target_instance, pal_instance = identity(201), identity(202), identity(203)
    source_group, target_group = identity(301), identity(302)
    source_chars = [identity(401), identity(402)]
    source_items = [identity(501), identity(502)]
    source_dynamic = [identity(601), identity(602)]
    target_chars = [identity(701), identity(702)]
    target_items = [identity(801)]
    target_dynamic = identity(901)

    source = fixture(players=[player(source_uid, source_instance, "Source")], guild_players=[
        {"player_uid": source_uid, "player_name": "Source"}])
    source_world = save_repair.world_data(source)
    source_entry = save_repair.player_entries(source)[0][0]
    source_entry["value"]["RawData"]["value"]["group_id"] = source_group
    owned_pal = pal(source_uid, pal_instance)
    owned_raw = save_repair.character_raw(owned_pal)
    owned_raw["SlotID"] = prop({"ContainerId": prop({"ID": prop(source_chars[0])})})
    owned_raw["ItemContainerId"] = prop({"ID": prop(source_items[1])})
    owned_pal["value"]["RawData"]["value"]["group_id"] = source_group
    save_repair.section_rows(source, "CharacterSaveParameterMap").append(owned_pal)
    source_world.update({
        "CharacterContainerSaveData": prop([
            character_container(source_chars[0], [pal_instance]),
            character_container(source_chars[1], []),
        ]),
        "ItemContainerSaveData": prop([
            item_container(source_items[0], [source_dynamic[0]]),
            item_container(source_items[1], [source_dynamic[1]]),
        ]),
        "DynamicItemSaveData": prop({"values": [dynamic_item(value) for value in source_dynamic]}),
    })
    source_group_raw = save_repair.section_rows(source, "GroupSaveDataMap")[0]["value"]["RawData"]["value"]
    source_group_raw.update({"group_id": source_group, "admin_player_uid": source_uid,
                             "individual_character_handle_ids": []})
    source_save = transfer_player_save(
        source_uid, source_instance, source_group, source_chars, source_items)

    target = fixture(players=[player(target_uid, target_instance, "Placeholder")], guild_players=[
        {"player_uid": target_uid, "player_name": "Placeholder",
         "player_info": {"player_name": "Placeholder"}}])
    target_world = save_repair.world_data(target)
    target_entry = save_repair.player_entries(target)[0][0]
    target_entry["value"]["RawData"]["value"]["group_id"] = target_group
    target_world.update({
        "CharacterContainerSaveData": prop([
            character_container(target_chars[0], []), character_container(target_chars[1], []),
        ]),
        "ItemContainerSaveData": prop([item_container(target_items[0], [target_dynamic])]),
        "DynamicItemSaveData": prop({"values": [dynamic_item(target_dynamic)]}),
    })
    target_group_raw = save_repair.section_rows(target, "GroupSaveDataMap")[0]["value"]["RawData"]["value"]
    target_group_raw.update({
        "group_id": target_group, "admin_player_uid": target_uid,
        "individual_character_handle_ids": [{"guid": target_uid, "instance_id": target_instance}],
    })
    target_save = transfer_player_save(
        target_uid, target_instance, target_group, target_chars, target_items)
    return {
        "source_level": source, "target_level": target,
        "source_player": source_save, "target_player": target_save,
        "source_uid": source_uid, "target_uid": target_uid,
        "source_instance": source_instance, "target_instance": target_instance,
        "pal_instance": pal_instance, "source_group": source_group, "target_group": target_group,
        "source_chars": source_chars, "source_items": source_items,
        "source_dynamic": source_dynamic,
    }


class SaveRepairTransformTests(unittest.TestCase):
    def test_rename_updates_exact_player_and_guild_membership(self):
        document = fixture()
        result = save_repair.rename_player(document, "aaaaaaaa000000000000000000000000", "Grace")
        self.assertEqual("Ada", result["before_name"])
        self.assertEqual("Grace", result["after_name"])
        self.assertEqual(1, result["guild_memberships_updated"])
        self.assertEqual("Grace", save_repair.player_entries(document)[0][3]["NickName"]["value"])
        members = save_repair.section_rows(document, "GroupSaveDataMap")[0]["value"]["RawData"]["value"]["players"]
        self.assertEqual("Grace", members[0]["player_name"])

    def test_rename_updates_current_nested_guild_name_without_creating_legacy_field(self):
        document = fixture(guild_players=[{
            "player_uid": "aaaaaaaa-0000-0000-0000-000000000000",
            "player_info": {"player_name": "Ada", "last_online_real_time": 123},
        }])
        result = save_repair.rename_player(document, "aaaaaaaa000000000000000000000000", "Grace")
        member = save_repair.section_rows(document, "GroupSaveDataMap")[0]["value"]["RawData"]["value"]["players"][0]
        self.assertEqual(1, result["guild_memberships_updated"])
        self.assertEqual("Grace", member["player_info"]["player_name"])
        self.assertNotIn("player_name", member)

    def test_rename_refuses_ambiguous_duplicate_and_bad_name(self):
        duplicate = fixture(players=[
            player("aaaaaaaa-0000-0000-0000-000000000000", "11111111-0000-0000-0000-000000000000", "Ada"),
            player("aaaaaaaa-0000-0000-0000-000000000000", "22222222-0000-0000-0000-000000000000", "Old"),
        ])
        with self.assertRaisesRegex(ValueError, "exactly one"):
            save_repair.rename_player(duplicate, "aaaaaaaa000000000000000000000000", "Grace")
        with self.assertRaisesRegex(ValueError, "surrounding whitespace"):
            save_repair.rename_player(fixture(), "aaaaaaaa000000000000000000000000", " Grace")

    def test_migrate_replaces_whole_guids_in_level_and_player_save(self):
        level = fixture()
        level["properties"]["worldSaveData"]["value"]["Reference"] = prop(
            "AAAAAAAA000000000000000000000000")
        player_save = {"properties": {"SaveData": prop({
            "PlayerUId": prop("aaaaaaaa-0000-0000-0000-000000000000"),
            "note": "prefix-aaaaaaaa000000000000000000000000-must-not-change",
        })}}
        result = save_repair.migrate_player(
            level, player_save, "aaaaaaaa000000000000000000000000",
            "bbbbbbbb000000000000000000000000")
        self.assertGreaterEqual(result["level_replacements"], 3)
        self.assertEqual("bbbbbbbb000000000000000000000000", save_repair.player_entries(level)[0][1])
        self.assertEqual("bbbbbbbb-0000-0000-0000-000000000000",
                         player_save["properties"]["SaveData"]["value"]["PlayerUId"]["value"])
        self.assertIn("aaaaaaaa", player_save["properties"]["SaveData"]["value"]["note"])

    def test_migrate_refuses_existing_target(self):
        level = fixture(players=[
            player("aaaaaaaa-0000-0000-0000-000000000000", "11111111-0000-0000-0000-000000000000", "Ada"),
            player("bbbbbbbb-0000-0000-0000-000000000000", "22222222-0000-0000-0000-000000000000", "Bob"),
        ])
        with self.assertRaisesRegex(ValueError, "already exists"):
            save_repair.migrate_player(level, {"uid": "aaaaaaaa000000000000000000000000"},
                                       "aaaaaaaa000000000000000000000000",
                                       "bbbbbbbb000000000000000000000000")

    def test_existing_identity_replacement_discards_placeholder_and_reuses_target_instance(self):
        old_uid = "aaaaaaaa-0000-0000-0000-000000000000"
        target_uid = "bbbbbbbb-0000-0000-0000-000000000000"
        old_instance = "11111111-0000-0000-0000-000000000000"
        target_instance = "22222222-0000-0000-0000-000000000000"
        document = fixture(players=[player(old_uid, old_instance, "Ada"),
                                    player(target_uid, target_instance, "Placeholder")], guild_players=[
            {"player_uid": old_uid, "player_name": "Ada"},
            {"player_uid": target_uid, "player_name": "Placeholder"},
        ])
        group = save_repair.section_rows(document, "GroupSaveDataMap")[0]["value"]["RawData"]["value"]
        group.update({"admin_player_uid": old_uid, "base_ids": [], "individual_character_handle_ids": [
            {"guid": old_uid, "instance_id": old_instance},
            {"guid": target_uid, "instance_id": target_instance},
        ]})
        source_save = {"properties": {"SaveData": prop({"PlayerUId": prop(old_uid),
                         "IndividualId": prop({"PlayerUId": prop(old_uid), "InstanceId": prop(old_instance)})})}}
        target_save = {"properties": {"SaveData": prop({"PlayerUId": prop(target_uid),
                         "IndividualId": prop({"PlayerUId": prop(target_uid), "InstanceId": prop(target_instance)})})}}
        result = save_repair.replace_player_identity(document, source_save, target_save, old_uid, target_uid)
        remaining = save_repair.player_entries(document)
        self.assertEqual(1, len(remaining))
        self.assertEqual("bbbbbbbb000000000000000000000000", remaining[0][1])
        self.assertEqual("22222222000000000000000000000000", remaining[0][2])
        self.assertEqual("Ada", save_repair.player_name(remaining[0][3]))
        self.assertEqual(1, result["removed_target_memberships"])
        self.assertEqual(0, save_repair.count_guid_tree(document, old_uid))
        self.assertEqual(0, save_repair.count_guid_tree(document, old_instance))
        self.assertGreater(save_repair.count_guid_tree(source_save, target_uid), 0)
        self.assertGreater(save_repair.count_guid_tree(source_save, target_instance), 0)

    def test_existing_identity_replacement_removes_empty_target_guild_but_refuses_owned_data(self):
        old_uid = "aaaaaaaa-0000-0000-0000-000000000000"; target_uid = "bbbbbbbb-0000-0000-0000-000000000000"
        old_instance = "11111111-0000-0000-0000-000000000000"; target_instance = "22222222-0000-0000-0000-000000000000"
        source_save = {"uid": old_uid, "instance": old_instance}
        target_save = {"uid": target_uid, "instance": target_instance}
        document = fixture(players=[player(old_uid, old_instance, "Ada"), player(target_uid, target_instance, "New")],
                           guild_players=[])
        groups = save_repair.section_rows(document, "GroupSaveDataMap")
        groups[0]["value"]["RawData"]["value"].update({
            "admin_player_uid": old_uid, "players": [{"player_uid": old_uid}], "base_ids": []})
        groups.append({"value": {"RawData": {"value": {
            "admin_player_uid": target_uid, "players": [{"player_uid": target_uid}],
            "individual_character_handle_ids": [{"guid": target_uid, "instance_id": target_instance}],
            "base_ids": [],
        }}}})
        clean = copy.deepcopy(document)
        result = save_repair.replace_player_identity(clean, copy.deepcopy(source_save), target_save,
                                                      old_uid, target_uid)
        self.assertEqual(1, result["removed_empty_target_guilds"])
        dirty = copy.deepcopy(document)
        dirty["properties"]["worldSaveData"]["value"]["OwnedReference"] = prop(target_uid)
        with self.assertRaisesRegex(ValueError, "not disposable"):
            save_repair.replace_player_identity(dirty, copy.deepcopy(source_save), target_save,
                                                old_uid, target_uid)

    def test_duplicate_cleanup_keeps_player_save_and_guild_backed_instance(self):
        uid = "aaaaaaaa-0000-0000-0000-000000000000"
        keep = "11111111-0000-0000-0000-000000000000"
        remove = "22222222-0000-0000-0000-000000000000"
        document = fixture(players=[player(uid, keep, "Ada"), player(uid, remove, "Stale")])
        raw = save_repair.section_rows(document, "GroupSaveDataMap")[0]["value"]["RawData"]["value"]
        raw["individual_character_handle_ids"] = [{"guid": uid, "instance_id": keep}]
        player_save = {"properties": {"SaveData": prop({"PlayerUId": prop(uid),
                       "IndividualId": prop({"PlayerUId": prop(uid), "InstanceId": prop(keep)})})}}
        result = save_repair.cleanup_duplicate_players(
            document, {"aaaaaaaa000000000000000000000000": player_save})
        self.assertEqual(1, result["players_repaired"])
        self.assertEqual(1, result["records_removed"])
        self.assertEqual(["11111111000000000000000000000000"],
                         [row[2] for row in save_repair.player_entries(document)])
        self.assertEqual({}, save_repair.diagnose(document)["duplicate_player_uids"])

    def test_duplicate_cleanup_refuses_conflicting_or_referenced_instances(self):
        uid = "aaaaaaaa-0000-0000-0000-000000000000"
        one = "11111111-0000-0000-0000-000000000000"; two = "22222222-0000-0000-0000-000000000000"
        document = fixture(players=[player(uid, one, "Ada"), player(uid, two, "Stale")])
        raw = save_repair.section_rows(document, "GroupSaveDataMap")[0]["value"]["RawData"]["value"]
        raw["individual_character_handle_ids"] = [{"guid": uid, "instance_id": two}]
        player_save = {"properties": {"SaveData": prop({"PlayerUId": prop(uid),
                       "IndividualId": prop({"PlayerUId": prop(uid), "InstanceId": prop(one)})})}}
        with self.assertRaisesRegex(ValueError, "no single"):
            save_repair.cleanup_duplicate_players(copy.deepcopy(document),
                {"aaaaaaaa000000000000000000000000": player_save})
        raw["individual_character_handle_ids"] = [{"guid": uid, "instance_id": one}]
        document["properties"]["worldSaveData"]["value"]["ExternalReference"] = prop(two)
        with self.assertRaisesRegex(ValueError, "non-canonical references"):
            save_repair.cleanup_duplicate_players(document,
                {"aaaaaaaa000000000000000000000000": player_save})

    def test_duplicate_cleanup_deduplicates_identical_canonical_records_only(self):
        uid = "aaaaaaaa-0000-0000-0000-000000000000"
        instance = "11111111-0000-0000-0000-000000000000"
        record = player(uid, instance, "Ada")
        document = fixture(players=[record, copy.deepcopy(record)])
        raw = save_repair.section_rows(document, "GroupSaveDataMap")[0]["value"]["RawData"]["value"]
        raw["individual_character_handle_ids"] = [{"guid": uid, "instance_id": instance}]
        player_save = {"properties": {"SaveData": prop({"PlayerUId": prop(uid),
                       "IndividualId": prop({"PlayerUId": prop(uid), "InstanceId": prop(instance)})})}}
        result = save_repair.cleanup_duplicate_players(
            document, {"aaaaaaaa000000000000000000000000": player_save})
        self.assertEqual(1, result["records_removed"])
        self.assertEqual(1, len(save_repair.player_entries(document)))

        divergent = fixture(players=[record, player(uid, instance, "Other")])
        group = save_repair.section_rows(divergent, "GroupSaveDataMap")[0]["value"]["RawData"]["value"]
        group["individual_character_handle_ids"] = [{"guid": uid, "instance_id": instance}]
        with self.assertRaisesRegex(ValueError, "non-identical"):
            save_repair.cleanup_duplicate_players(
                divergent, {"aaaaaaaa000000000000000000000000": player_save})

    def test_graph_cleanup_repairs_broken_slots_and_removes_only_unreferenced_graph(self):
        live_character = "33333333-0000-0000-0000-000000000000"
        orphan_character = "44444444-0000-0000-0000-000000000000"
        live_item = "55555555-0000-0000-0000-000000000000"
        orphan_item = "66666666-0000-0000-0000-000000000000"
        missing_instance = "77777777-0000-0000-0000-000000000000"
        missing_dynamic = "88888888-0000-0000-0000-000000000000"
        cascading_dynamic = "99999999-0000-0000-0000-000000000000"
        document = fixture()
        world = save_repair.world_data(document)
        world["CharacterContainerSaveData"] = prop([
            character_container(live_character, [missing_instance]),
            character_container(orphan_character, []),
        ])
        world["ItemContainerSaveData"] = prop([
            item_container(live_item, [missing_dynamic]),
            item_container(orphan_item, [cascading_dynamic]),
        ])
        world["DynamicItemSaveData"] = prop({"values": [dynamic_item(cascading_dynamic)]})
        player_document = {"character_container": live_character, "item_container": live_item}
        result = save_repair.cleanup_save_graph(document, {"a" * 32: player_document})
        self.assertEqual(1, result["broken_character_slots_repaired"])
        self.assertEqual(1, result["broken_item_slots_repaired"])
        self.assertEqual(1, result["character_containers_removed"])
        self.assertEqual(1, result["item_containers_removed"])
        self.assertEqual(1, result["dynamic_items_removed"])
        slot = save_repair.section_rows(document, "CharacterContainerSaveData")[0]["value"]["Slots"]["value"]["values"][0]["RawData"]["value"]
        self.assertEqual("00000000000000000000000000000000", save_repair.guid_value(slot["instance_id"]))
        self.assertEqual(0, slot["permission_tribe_id"])
        item_slot = save_repair.section_rows(document, "ItemContainerSaveData")[0]["value"]["Slots"]["value"]["values"][0]
        self.assertEqual(save_repair.ZERO_GUID, save_repair.item_slot_dynamic_handle(item_slot)[2])
        self.assertFalse(any(save_repair.graph_diagnostics(document, {"a" * 32: player_document}).values()))

    def test_graph_cleanup_preserves_group_owned_item_container_and_refuses_duplicate_ids(self):
        group_id = "aaaaaaaa-1111-1111-1111-111111111111"
        container_id = "bbbbbbbb-1111-1111-1111-111111111111"
        document = fixture()
        group = save_repair.section_rows(document, "GroupSaveDataMap")[0]
        group["key"] = prop(group_id)
        group["value"]["RawData"]["value"]["group_id"] = group_id
        world = save_repair.world_data(document)
        world["CharacterContainerSaveData"] = prop([])
        owned = item_container(container_id, [], group_id)
        world["ItemContainerSaveData"] = prop([owned])
        world["DynamicItemSaveData"] = prop({"values": []})
        self.assertFalse(any(save_repair.graph_diagnostics(document, {}).values()))
        world["ItemContainerSaveData"] = prop([owned, copy.deepcopy(owned)])
        with self.assertRaisesRegex(ValueError, "duplicate container"):
            save_repair.graph_diagnostics(document, {})

    def test_inactive_deletion_removes_profile_pals_empty_guild_and_owned_containers(self):
        uid = "aaaaaaaa-0000-0000-0000-000000000000"
        player_instance = "11111111-0000-0000-0000-000000000000"
        pal_instance = "22222222-0000-0000-0000-000000000000"
        char_container = "33333333-0000-0000-0000-000000000000"
        item_id = "44444444-0000-0000-0000-000000000000"
        dynamic_id = "55555555-0000-0000-0000-000000000000"
        now = 100 * 86400 * 10_000_000
        document = fixture(players=[player(uid, player_instance, "Ada"), pal(uid, pal_instance)],
                           guild_players=[{"player_uid": uid,
                                           "player_info": {"player_name": "Ada", "last_online_real_time": 0}}])
        world = save_repair.world_data(document)
        world["GameTimeSaveData"] = prop({"RealDateTimeTicks": prop(now)})
        world["CharacterContainerSaveData"] = prop([
            character_container(char_container, [player_instance, pal_instance])])
        world["ItemContainerSaveData"] = prop([item_container(item_id, [dynamic_id])])
        world["DynamicItemSaveData"] = prop({"values": [dynamic_item(dynamic_id)]})
        group = save_repair.section_rows(document, "GroupSaveDataMap")[0]["value"]["RawData"]["value"]
        group.update({"admin_player_uid": uid, "base_ids": [],
                      "map_object_instance_ids_base_camp_points": [],
                      "individual_character_handle_ids": [
                          {"guid": uid, "instance_id": player_instance},
                          {"guid": uid, "instance_id": pal_instance},
                      ]})
        player_save = {"properties": {"SaveData": prop({
            "PlayerUId": prop(uid),
            "IndividualId": prop({"PlayerUId": prop(uid), "InstanceId": prop(player_instance)}),
            "OtomoCharacterContainerId": prop({"ID": prop(char_container)}),
            "InventoryInfo": prop({"CommonContainerId": prop({"ID": prop(item_id)})}),
        })}}
        documents = {"aaaaaaaa000000000000000000000000": player_save}
        result = save_repair.delete_inactive_players(document, documents, 30)
        self.assertEqual(["aaaaaaaa000000000000000000000000"], result["deleted_uids"])
        self.assertEqual(2, result["characters_removed"])
        self.assertEqual(1, result["empty_guilds_removed"])
        self.assertEqual({}, documents)
        self.assertEqual([], save_repair.section_rows(document, "CharacterSaveParameterMap"))
        self.assertEqual([], save_repair.section_rows(document, "GroupSaveDataMap"))
        self.assertEqual([], save_repair.section_rows(document, "CharacterContainerSaveData"))
        self.assertEqual([], save_repair.section_rows(document, "ItemContainerSaveData"))
        self.assertEqual([], save_repair.dynamic_item_rows(document))

    def test_inactive_deletion_refuses_retained_guild_members_bases_and_bad_threshold(self):
        uid = "aaaaaaaa-0000-0000-0000-000000000000"
        other = "bbbbbbbb-0000-0000-0000-000000000000"
        document = fixture(players=[player(uid, "1" * 32, "Ada"), player(other, "2" * 32, "Bob")],
                           guild_players=[
                               {"player_uid": uid, "last_online_real_time": 0},
                               {"player_uid": other, "last_online_real_time": 99 * 86400 * 10_000_000},
                           ])
        world = save_repair.world_data(document)
        world["GameTimeSaveData"] = prop({"RealDateTimeTicks": prop(100 * 86400 * 10_000_000)})
        world["CharacterContainerSaveData"] = prop([])
        world["ItemContainerSaveData"] = prop([])
        world["DynamicItemSaveData"] = prop({"values": []})
        group = save_repair.section_rows(document, "GroupSaveDataMap")[0]["value"]["RawData"]["value"]
        group["admin_player_uid"] = uid
        player_save = {"properties": {"SaveData": prop({"PlayerUId": prop(uid),
                       "IndividualId": prop({"PlayerUId": prop(uid), "InstanceId": prop("1" * 32)})})}}
        with self.assertRaisesRegex(ValueError, "retained members"):
            save_repair.delete_inactive_players(copy.deepcopy(document),
                                                {"aaaaaaaa000000000000000000000000": player_save}, 30)
        with self.assertRaisesRegex(ValueError, "1 through 3650"):
            save_repair.delete_inactive_players(copy.deepcopy(document),
                                                {"aaaaaaaa000000000000000000000000": player_save}, 0)

        lone = fixture(players=[player(uid, "1" * 32, "Ada")],
                       guild_players=[{"player_uid": uid, "last_online_real_time": 0}])
        lone_world = save_repair.world_data(lone)
        lone_world["GameTimeSaveData"] = prop({"RealDateTimeTicks": prop(100 * 86400 * 10_000_000)})
        lone_world["CharacterContainerSaveData"] = prop([])
        lone_world["ItemContainerSaveData"] = prop([])
        lone_world["DynamicItemSaveData"] = prop({"values": []})
        lone_group = save_repair.section_rows(lone, "GroupSaveDataMap")[0]["value"]["RawData"]["value"]
        lone_group.update({"admin_player_uid": uid, "base_ids": ["c" * 32]})
        with self.assertRaisesRegex(ValueError, "no usable group ID"):
            save_repair.delete_inactive_players(
                lone, {"aaaaaaaa000000000000000000000000": player_save}, 30)

    def test_inactive_deletion_cascades_empty_guild_base_build_worker_and_item_graph(self):
        identity = lambda number: "f" * 24 + f"{number:08x}"
        uid, player_instance, group_id = identity(1), identity(2), identity(3)
        base_id, palbox_id, worker_container = identity(4), identity(5), identity(6)
        worker_instance, work_id, building_id = identity(7), identity(8), identity(9)
        item_id, dynamic_id, spawner_id = identity(10), identity(11), identity(12)
        document = fixture(players=[player(uid, player_instance, "Ada")], guild_players=[
            {"player_uid": uid, "last_online_real_time": 0}])
        world = save_repair.world_data(document)
        world["GameTimeSaveData"] = prop({"RealDateTimeTicks": prop(100 * 86400 * 10_000_000)})
        worker = pal(save_repair.ZERO_GUID, worker_instance, "Worker")
        raw = save_repair.character_raw(worker)
        raw["SlotID"] = prop({"ContainerId": prop({"ID": prop(worker_container)})})
        raw["ItemContainerId"] = prop({"ID": prop(item_id)})
        save_repair.section_rows(document, "CharacterSaveParameterMap").append(worker)
        world["CharacterContainerSaveData"] = prop([character_container(worker_container, [worker_instance])])
        world["ItemContainerSaveData"] = prop([item_container(item_id, [dynamic_id])])
        world["DynamicItemSaveData"] = prop({"values": [dynamic_item(dynamic_id)]})
        world["BaseCampSaveData"] = prop([{"key": base_id, "value": {
            "RawData": prop({"id": base_id, "group_id_belong_to": group_id,
                             "owner_map_object_instance_id": palbox_id}),
            "WorkerDirector": prop({"RawData": prop({"container_id": worker_container})}),
            "WorkCollection": prop({"RawData": prop({"work_ids": [work_id]})}),
            "ModuleMap": prop([]),
        }}])
        world["WorkSaveData"] = prop({"values": [{"RawData": prop({
            "id": work_id, "base_camp_id_belong_to": base_id,
            "owner_map_object_model_id": building_id,
        })}]})
        world["MapObjectSaveData"] = prop({"values": [
            {"MapObjectInstanceId": prop(palbox_id), "Model": prop({"RawData": prop({
                "base_camp_id_belong_to": base_id,
                "owner_spawner_level_object_instance_id": spawner_id,
            })})},
            {"MapObjectInstanceId": prop(building_id), "Model": prop({"RawData": prop({
                "base_camp_id_belong_to": base_id, "build_player_uid": uid,
            })}), "ConcreteModel": prop({"ModuleMap": prop([{"value": {"RawData": prop({
                "target_container_id": item_id,
            })}}])})},
        ]})
        world["MapObjectSpawnerInStageSaveData"] = prop([{"value": {
            "SpawnerDataMapByLevelObjectInstanceId": prop([{"key": spawner_id, "value": {
                "MapObjectInstanceId": prop(palbox_id),
            }}])}}])
        group = save_repair.section_rows(document, "GroupSaveDataMap")[0]
        group["key"] = group_id
        group_raw = group["value"]["RawData"]["value"]
        group_raw.update({"group_id": group_id, "admin_player_uid": uid,
                          "base_ids": [base_id],
                          "map_object_instance_ids_base_camp_points": [palbox_id],
                          "individual_character_handle_ids": [
                              {"guid": uid, "instance_id": player_instance},
                              {"guid": save_repair.ZERO_GUID, "instance_id": worker_instance},
                          ]})
        player_save = {"properties": {"SaveData": prop({
            "PlayerUId": prop(uid),
            "IndividualId": prop({"PlayerUId": prop(uid), "InstanceId": prop(player_instance)}),
        })}}
        result = save_repair.delete_inactive_players(document, {uid: player_save}, 30)
        self.assertEqual(1, result["bases_removed"])
        self.assertEqual(2, result["map_objects_removed"])
        self.assertEqual(1, result["work_records_removed"])
        self.assertEqual(1, result["spawners_removed"])
        self.assertEqual(1, result["base_characters_removed"])
        self.assertEqual(1, result["base_character_containers_removed"])
        self.assertEqual(1, result["base_item_containers_removed"])
        self.assertEqual(1, result["base_dynamic_items_removed"])
        for section in ("BaseCampSaveData", "WorkSaveData", "MapObjectSaveData",
                        "CharacterSaveParameterMap", "CharacterContainerSaveData",
                        "ItemContainerSaveData"):
            self.assertEqual([], save_repair.section_rows(document, section))
        self.assertEqual([], save_repair.nested_spawner_rows(document))
        self.assertEqual([], save_repair.dynamic_item_rows(document))

    def test_inactive_base_cascade_refuses_reference_from_retained_world_data(self):
        # The comprehensive cascade fixture above proves success; a retained reverse
        # reference must turn the same graph into a fail-closed operation.
        identity = lambda number: "e" * 24 + f"{number:08x}"
        base_id = identity(4)
        document = fixture()
        world = save_repair.world_data(document)
        world["BaseCampSaveData"] = prop([{"key": base_id, "value": {}}])
        world["WorkSaveData"] = prop({"values": []})
        world["MapObjectSaveData"] = prop({"values": []})
        world["MapObjectSpawnerInStageSaveData"] = prop([{"value": {
            "SpawnerDataMapByLevelObjectInstanceId": prop([])}}])
        world["CharacterContainerSaveData"] = prop([])
        world["ItemContainerSaveData"] = prop([])
        world["DynamicItemSaveData"] = prop({"values": []})
        group = save_repair.section_rows(document, "GroupSaveDataMap")[0]
        raw = group["value"]["RawData"]["value"]
        group["key"] = identity(3); raw.update({"group_id": identity(3), "base_ids": [base_id],
                                               "map_object_instance_ids_base_camp_points": []})
        world["RetainedReference"] = prop(base_id)
        with self.assertRaisesRegex(ValueError, "still referenced"):
            groups = save_repair.section_rows(document, "GroupSaveDataMap")
            groups.remove(group)
            save_repair.delete_empty_guild_owned_graph(document, group, raw, set(), {})

    def test_offline_inventory_edit_supports_currency_and_refuses_dynamic_item_replacement(self):
        uid = "a" * 32; instance = "b" * 32; container_id = "c" * 32
        char_ids = ["d" * 32, "e" * 32]
        document = fixture(players=[player(uid, instance, "Ada")])
        save_repair.world_data(document)["ItemContainerSaveData"] = prop([
            editable_item_container(container_id, 7)])
        player_save = transfer_player_save(uid, instance, "f" * 32, char_ids,
                                           [container_id, "1" * 32])
        result = save_repair.edit_inventory_slot(document, player_save, {
            "uid": uid, "container": "common", "slot_index": 7,
            "item_id": "Money", "stack_count": 500,
        })
        self.assertTrue(result["currency_slot"])
        slot = save_repair.section_rows(document, "ItemContainerSaveData")[0]["value"]["Slots"]["value"]["values"][0]
        self.assertEqual("Money", slot["ItemId"]["value"]["StaticId"]["value"])
        self.assertEqual(500, slot["StackCount"]["value"])
        with self.assertRaisesRegex(ValueError, "empty slot"):
            save_repair.edit_inventory_slot(copy.deepcopy(document), player_save, {
                "uid": uid, "container": "common", "slot_index": 7,
                "item_id": "Money", "stack_count": 0,
            })
        dynamic = copy.deepcopy(document)
        dynamic_slot = save_repair.section_rows(dynamic, "ItemContainerSaveData")[0]["value"]["Slots"]["value"]["values"][0]
        dynamic_slot["ItemId"]["value"]["DynamicId"]["value"]["LocalIdInCreatedWorld"]["value"] = "2" * 32
        with self.assertRaisesRegex(ValueError, "dynamic item metadata"):
            save_repair.edit_inventory_slot(dynamic, player_save, {
                "uid": uid, "container": "common", "slot_index": 7,
                "item_id": "Wood", "stack_count": 1,
            })

    def test_offline_player_progression_updates_nested_numeric_properties(self):
        uid = "a" * 32; instance = "b" * 32
        document = fixture(players=[player(uid, instance, "Ada")])
        raw = save_repair.player_entries(document)[0][3]
        raw["Level"] = prop({"type": "None", "value": 12})
        raw["Exp"] = prop(345)
        player_save = {"properties": {"SaveData": prop({
            "PlayerUId": prop(uid),
            "IndividualId": prop({"PlayerUId": prop(uid), "InstanceId": prop(instance)}),
        })}}
        result = save_repair.edit_player_progression(document, player_save, {
            "uid": uid, "level": 20, "experience": 9999,
        })
        self.assertEqual({"before": 12, "after": 20}, result["changes"]["level"])
        self.assertEqual(20, raw["Level"]["value"]["value"])
        self.assertEqual(9999, raw["Exp"]["value"])
        with self.assertRaisesRegex(ValueError, "at least one"):
            save_repair.edit_player_progression(document, player_save, {"uid": uid})

    def test_offline_owned_pal_edit_updates_bounded_stats_nickname_and_passives(self):
        uid = "a" * 32; player_instance = "b" * 32; pal_instance = "c" * 32
        char_ids = ["d" * 32, "e" * 32]
        document = fixture(players=[player(uid, player_instance, "Ada")])
        owned = pal(uid, pal_instance, "Lamball")
        raw = save_repair.character_raw(owned)
        raw.update({
            "SlotId": prop({"ContainerId": prop({"ID": prop(char_ids[0])})}),
            "NickName": prop("Lamball"),
            "Level": prop({"type": "None", "value": 10}),
            "Rank": prop({"type": "None", "value": 1}),
            "Talent_HP": prop({"type": "None", "value": 25}),
            "Talent_Shot": prop({"type": "None", "value": 30}),
            "Talent_Defense": prop({"type": "None", "value": 35}),
            "PassiveSkillList": prop({"values": ["PassiveA"]}),
        })
        save_repair.section_rows(document, "CharacterSaveParameterMap").append(owned)
        player_save = transfer_player_save(uid, player_instance, "f" * 32, char_ids,
                                           ["1" * 32, "2" * 32])
        result = save_repair.edit_owned_pal(document, player_save, {
            "uid": uid, "pal_instance_id": pal_instance, "nickname": "Cloud",
            "level": 40, "rank": 5, "talent_hp": 100,
            "talent_attack": 99, "talent_defense": 98,
            "passives": ["PassiveA", "PassiveB"],
        })
        self.assertEqual("Cloud", raw["NickName"]["value"])
        self.assertEqual(40, raw["Level"]["value"]["value"])
        self.assertEqual(["PassiveA", "PassiveB"], raw["PassiveSkillList"]["value"]["values"])
        self.assertIn("Talent_HP", result["changes"])
        other = copy.deepcopy(document)
        save_repair.character_raw(save_repair.section_rows(other, "CharacterSaveParameterMap")[-1])["OwnerPlayerUId"] = prop("9" * 32)
        with self.assertRaisesRegex(ValueError, "not owned"):
            save_repair.edit_owned_pal(other, player_save, {
                "uid": uid, "pal_instance_id": pal_instance, "level": 41,
            })

    def test_diagnostics_expose_duplicates_missing_orphans_and_guild_breakage(self):
        with tempfile.TemporaryDirectory() as name:
            players_dir = pathlib.Path(name)
            (players_dir / "cccccccc000000000000000000000000.sav").write_bytes(b"orphan")
            document = fixture(players=[
                player("aaaaaaaa-0000-0000-0000-000000000000", "11111111-0000-0000-0000-000000000000", "Ada"),
                player("aaaaaaaa-0000-0000-0000-000000000000", "22222222-0000-0000-0000-000000000000", "Duplicate"),
            ], guild_players=[{"player_uid": "bbbbbbbb-0000-0000-0000-000000000000",
                               "player_name": "Missing", "last_online_real_time": 50}])
            result = save_repair.diagnose(document, players_dir)
        self.assertIn("aaaaaaaa000000000000000000000000", result["duplicate_player_uids"])
        self.assertEqual(["aaaaaaaa000000000000000000000000"], result["missing_player_saves"])
        self.assertEqual(["cccccccc000000000000000000000000"], result["orphan_player_saves"])
        self.assertEqual(["bbbbbbbb000000000000000000000000"], result["guild_members_without_character"])

    def test_schema_signature_detects_section_drift_but_not_values(self):
        document = fixture()
        before = save_repair.schema_signature(document)
        renamed = copy.deepcopy(document)
        save_repair.rename_player(renamed, "aaaaaaaa000000000000000000000000", "Grace")
        self.assertEqual(before, save_repair.schema_signature(renamed))
        del renamed["properties"]["worldSaveData"]["value"]["BaseCampSaveData"]
        self.assertNotEqual(before, save_repair.schema_signature(renamed))

    def test_player_save_lookup_is_case_insensitive_and_refuses_case_collisions(self):
        with tempfile.TemporaryDirectory() as name:
            players = pathlib.Path(name)
            upper = players / "AAAAAAAA000000000000000000000000.sav"
            upper.write_bytes(b"player")
            found = save_repair.find_player_save(players, "aaaaaaaa000000000000000000000000", required=True)
            self.assertEqual(upper, found)
            self.assertEqual("BBBBBBBB000000000000000000000000.sav",
                             save_repair.migrated_player_path(found, "bbbbbbbb000000000000000000000000").name)
            (players / "aaaaaaaa000000000000000000000000.sav").write_bytes(b"collision")
            with self.assertRaisesRegex(ValueError, "case-variant"):
                save_repair.find_player_save(players, "aaaaaaaa000000000000000000000000", required=True)


    def test_cross_world_transfer_copies_player_inventory_and_pals_but_keeps_target_identity_guild(self):
        data = cross_world_fixture()
        untouched_source = copy.deepcopy(data["source_level"])
        target_documents = {data["target_uid"]: data["target_player"]}
        result = save_repair.transfer_player_cross_world(
            data["target_level"], data["source_level"], data["source_player"],
            data["target_player"], target_documents, data["source_uid"], data["target_uid"])
        self.assertEqual(1, result["pals_transferred"])
        self.assertEqual(2, result["character_containers_transferred"])
        self.assertEqual(2, result["item_containers_transferred"])
        self.assertEqual(2, result["dynamic_items_transferred"])
        self.assertEqual(untouched_source, data["source_level"])
        transferred = save_repair.player_entries(data["target_level"])
        self.assertEqual([(data["target_uid"], data["target_instance"])],
                         [(uid, instance) for _entry, uid, instance, _raw in transferred])
        self.assertEqual((data["target_uid"], data["target_instance"]),
                         save_repair.player_save_identity(data["target_player"]))
        self.assertEqual(0, save_repair.count_guid_tree(data["target_level"], data["source_uid"]))
        self.assertFalse(any(save_repair.graph_diagnostics(
            data["target_level"], target_documents).values()))
        group = save_repair.section_rows(data["target_level"], "GroupSaveDataMap")[0]["value"]["RawData"]["value"]
        self.assertEqual("Source", group["players"][0]["player_name"])
        self.assertEqual(data["target_group"], save_repair.guid_value(
            save_repair.save_data(data["target_player"])["GroupId"]))

    def test_cross_world_transfer_refuses_external_target_ownership_and_missing_items(self):
        dirty_target = cross_world_fixture()
        save_repair.world_data(dirty_target["target_level"])["ExternalOwner"] = prop(
            dirty_target["target_uid"])
        with self.assertRaisesRegex(ValueError, "not disposable"):
            save_repair.transfer_player_cross_world(
                dirty_target["target_level"], dirty_target["source_level"],
                dirty_target["source_player"], dirty_target["target_player"],
                {dirty_target["target_uid"]: dirty_target["target_player"]},
                dirty_target["source_uid"], dirty_target["target_uid"])

        missing_item = cross_world_fixture()
        save_repair.dynamic_item_rows(missing_item["source_level"]).pop()
        with self.assertRaisesRegex(ValueError, "missing dynamic item"):
            save_repair.transfer_player_cross_world(
                missing_item["target_level"], missing_item["source_level"],
                missing_item["source_player"], missing_item["target_player"],
                {missing_item["target_uid"]: missing_item["target_player"]},
                missing_item["source_uid"], missing_item["target_uid"])


class SaveRepairPlanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.world = self.root / "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        (self.world / "Players").mkdir(parents=True)
        (self.world / "Level.sav").write_bytes(b"level")
        self.parser = self.root / "parser"
        self.parser.write_bytes(b"parser")

    def tearDown(self):
        self.temporary.cleanup()

    def test_plan_binds_every_world_byte_build_parser_and_schema(self):
        payload = {"action": "rename_player", "uid": "aaaaaaaa000000000000000000000000",
                   "new_name": "Grace"}
        with mock.patch.object(save_repair.SAVE_INTELLIGENCE, "ensure_parser", return_value=self.parser), \
             mock.patch.object(save_repair, "decode", return_value=(fixture(), self.root / "decoded.json")):
            first = save_repair.plan(payload, self.world, self.parser, "24181105")
            (self.world / "Level.sav").write_bytes(b"changed")
            second = save_repair.plan(payload, self.world, self.parser, "24181105")
        self.assertEqual("ready", first["status"])
        self.assertEqual("RENAME PLAYER", first["confirmation"])
        self.assertNotEqual(first["reviewed_sha256"], second["reviewed_sha256"])

    def test_browser_contract_exposes_repairs_and_keeps_apply_plan_gated(self):
        html = (ROOT / "admin/static/index.html").read_text()
        javascript = (ROOT / "admin/static/app.js").read_text()
        self.assertIn('value="replace_player"', html)
        self.assertIn('value="cleanup_duplicates"', html)
        self.assertIn('value="cleanup_graph"', html)
        self.assertIn('value="delete_inactive_players"', html)
        self.assertIn('value="transfer_player"', html)
        self.assertIn('value="edit_inventory_slot"', html)
        self.assertIn('value="edit_player_progression"', html)
        self.assertIn('value="edit_owned_pal"', html)
        self.assertIn('id="saveRepairWorld"', html)
        self.assertIn('id="applySaveRepair" type="button" class="danger" disabled', html)
        self.assertIn('["rename_player", "migrate_player", "replace_player", "transfer_player", "edit_inventory_slot", "edit_player_progression", "edit_owned_pal"].includes(action)', javascript)
        self.assertIn('["cleanup_duplicates", "cleanup_graph"].includes(action)', javascript)
        self.assertIn('inactive_days: $("#saveRepairDays").value', javascript)
        self.assertIn('source_world_id: $("#saveRepairWorld").value.trim()', javascript)
        self.assertIn('source_uid: $("#saveRepairSource").value.trim(), target_uid: target', javascript)
        self.assertIn('slot_index: $("#saveRepairSlot").value', javascript)
        self.assertIn('pal_instance_id: $("#saveRepairPalInstance").value.trim()', javascript)
        self.assertIn('/api/v1/save/repair/plan', javascript)
        self.assertIn('reviewed_sha256: saveRepairPlan.reviewed_sha256', javascript)

    def test_plan_requires_numeric_build_and_supported_action(self):
        with mock.patch.object(save_repair.SAVE_INTELLIGENCE, "ensure_parser", return_value=self.parser):
            with self.assertRaisesRegex(ValueError, "numeric installed game build"):
                save_repair.plan({"action": "rename_player"}, self.world, self.parser, "unknown")
            with mock.patch.object(save_repair, "decode", return_value=(fixture(), self.root / "decoded.json")):
                with self.assertRaisesRegex(ValueError, "unsupported"):
                    save_repair.plan({"action": "delete_player"}, self.world, self.parser, "24181105")

    def test_plan_refuses_unvalidated_numeric_game_build(self):
        with mock.patch.object(save_repair.SAVE_INTELLIGENCE, "ensure_parser", return_value=self.parser):
            with self.assertRaisesRegex(ValueError, "no validated writable save schema"):
                save_repair.plan({"action": "rename_player", "uid": "a" * 32, "new_name": "Grace"},
                                 self.world, self.parser, "24199999")

    def test_transfer_plan_binds_read_only_source_world_bytes(self):
        data = cross_world_fixture()
        source_world = self.root / "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
        (source_world / "Players").mkdir(parents=True)
        (source_world / "Level.sav").write_bytes(b"source-level")
        source_path = source_world / "Players" / f'{data["source_uid"]}.sav'
        target_path = self.world / "Players" / f'{data["target_uid"]}.sav'
        source_path.write_bytes(b"source-player")
        target_path.write_bytes(b"target-player")
        payload = {"action": "transfer_player", "source_world_id": source_world.name,
                   "source_uid": data["source_uid"], "target_uid": data["target_uid"]}

        def fake_decode(_parser, _path, _temporary, label):
            if label == "level": return copy.deepcopy(data["target_level"]), self.root / "target.json"
            if label == "source-level": return copy.deepcopy(data["source_level"]), self.root / "source.json"
            if label == "source-player": return copy.deepcopy(data["source_player"]), self.root / "source-player.json"
            if label in {"target-player", "target-graph-player-0"}:
                return copy.deepcopy(data["target_player"]), self.root / "target-player.json"
            raise AssertionError(label)

        with mock.patch.object(save_repair.SAVE_INTELLIGENCE, "ensure_parser", return_value=self.parser), \
             mock.patch.object(save_repair, "decode", side_effect=fake_decode):
            first = save_repair.plan(payload, self.world, self.parser, "24181105")
            (source_world / "Level.sav").write_bytes(b"source-level-changed")
            second = save_repair.plan(payload, self.world, self.parser, "24181105")
        self.assertEqual("TRANSFER PLAYER", first["confirmation"])
        self.assertEqual(source_world.name, first["source_world"])
        self.assertNotEqual(first["reviewed_sha256"], second["reviewed_sha256"])
        with self.assertRaisesRegex(ValueError, "exactly 32 hexadecimal"):
            save_repair.source_world({"source_world_id": "../escape"}, self.world)

    def test_apply_staged_transfer_changes_only_target_world(self):
        data = cross_world_fixture()
        source_world = self.root / "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
        (source_world / "Players").mkdir(parents=True)
        (source_world / "Level.sav").write_bytes(b"source-level")
        source_path = source_world / "Players" / f'{data["source_uid"]}.sav'
        target_path = self.world / "Players" / f'{data["target_uid"]}.sav'
        source_path.write_bytes(b"source-player-untouched")
        target_path.write_bytes(b"target-player")
        payload = {"action": "transfer_player", "source_world_id": source_world.name,
                   "source_uid": data["source_uid"], "target_uid": data["target_uid"]}
        encoded = {}

        def fake_decode(_parser, _path, _temporary, label):
            if label == "level": return copy.deepcopy(data["target_level"]), self.root / "target.json"
            if label == "source-level": return copy.deepcopy(data["source_level"]), self.root / "source.json"
            if label == "source-player": return copy.deepcopy(data["source_player"]), self.root / "source-player.json"
            if label in {"target-player", "target-graph-player-0"}:
                return copy.deepcopy(data["target_player"]), self.root / "target-player.json"
            if label == "final-level": return copy.deepcopy(encoded["level"]), self.root / "final-level.json"
            if label == "final-target-player":
                return copy.deepcopy(encoded["target-player"]), self.root / "final-player.json"
            raise AssertionError(label)

        def fake_encode(_parser, document, _target, _temporary, label):
            encoded[label] = copy.deepcopy(document)

        with mock.patch.object(save_repair, "decode", side_effect=fake_decode), \
             mock.patch.object(save_repair, "encode_verified", side_effect=fake_encode):
            result = save_repair.apply_staged(payload, self.world, self.parser, source_world)
        self.assertEqual(data["source_uid"], result["effects"]["source_uid"])
        self.assertEqual(b"source-level", (source_world / "Level.sav").read_bytes())
        self.assertEqual(b"source-player-untouched", source_path.read_bytes())
        self.assertEqual((data["target_uid"], data["target_instance"]),
                         save_repair.player_save_identity(encoded["target-player"]))

    def test_apply_staged_inactive_deletion_removes_player_file_and_verifies_level(self):
        uid = "aaaaaaaa-0000-0000-0000-000000000000"
        normalized = "aaaaaaaa000000000000000000000000"
        source = self.world / "Players" / f"{normalized}.sav"
        source.write_bytes(b"player")
        level = fixture(players=[player(uid, "1" * 32, "Ada")],
                        guild_players=[{"player_uid": uid, "last_online_real_time": 0}])
        world = save_repair.world_data(level)
        world["GameTimeSaveData"] = prop({"RealDateTimeTicks": prop(100 * 86400 * 10_000_000)})
        world["CharacterContainerSaveData"] = prop([])
        world["ItemContainerSaveData"] = prop([])
        world["DynamicItemSaveData"] = prop({"values": []})
        group = save_repair.section_rows(level, "GroupSaveDataMap")[0]["value"]["RawData"]["value"]
        group.update({"admin_player_uid": uid, "base_ids": []})
        player_save = {"properties": {"SaveData": prop({"PlayerUId": prop(uid),
                       "IndividualId": prop({"PlayerUId": prop(uid), "InstanceId": prop("1" * 32)})})}}
        encoded = {}

        def fake_decode(_parser, path, _temporary, label):
            if label == "level":
                return copy.deepcopy(level), self.root / "level.json"
            if label.startswith("graph-player"):
                return copy.deepcopy(player_save), self.root / "player.json"
            if label == "final-level":
                return copy.deepcopy(encoded["level"]), self.root / "final.json"
            raise AssertionError(label)

        def fake_encode(_parser, document, _target, _temporary, label):
            encoded[label] = copy.deepcopy(document)

        with mock.patch.object(save_repair, "decode", side_effect=fake_decode), \
             mock.patch.object(save_repair, "encode_verified", side_effect=fake_encode):
            result = save_repair.apply_staged(
                {"action": "delete_inactive_players", "inactive_days": 30}, self.world, self.parser)
        self.assertEqual([normalized], result["effects"]["deleted_uids"])
        self.assertFalse(source.exists())
        self.assertEqual([], save_repair.player_entries(encoded["level"]))

    def test_apply_staged_inventory_edit_observes_encoded_slot_without_rewriting_player_save(self):
        uid = "a" * 32; instance = "b" * 32; container_id = "c" * 32
        player_path = self.world / "Players" / f"{uid}.sav"
        player_path.write_bytes(b"player-unchanged")
        level = fixture(players=[player(uid, instance, "Ada")])
        save_repair.world_data(level)["ItemContainerSaveData"] = prop([
            editable_item_container(container_id, 3)])
        player_save = transfer_player_save(uid, instance, "f" * 32,
                                           ["d" * 32, "e" * 32], [container_id, "1" * 32])
        encoded = {}

        def fake_decode(_parser, _path, _temporary, label):
            if label == "level": return copy.deepcopy(level), self.root / "level.json"
            if label == "player": return copy.deepcopy(player_save), self.root / "player.json"
            if label == "final-level": return copy.deepcopy(encoded["level"]), self.root / "final.json"
            raise AssertionError(label)

        def fake_encode(_parser, document, _target, _temporary, label):
            encoded[label] = copy.deepcopy(document)

        payload = {"action": "edit_inventory_slot", "uid": uid, "container": "common",
                   "slot_index": 3, "item_id": "Money", "stack_count": 12345}
        with mock.patch.object(save_repair, "decode", side_effect=fake_decode), \
             mock.patch.object(save_repair, "encode_verified", side_effect=fake_encode):
            result = save_repair.apply_staged(payload, self.world, self.parser)
        self.assertEqual(12345, result["effects"]["after_stack_count"])
        self.assertEqual(b"player-unchanged", player_path.read_bytes())
        slot = save_repair.section_rows(encoded["level"], "ItemContainerSaveData")[0]["value"]["Slots"]["value"]["values"][0]
        self.assertEqual("Money", slot["ItemId"]["value"]["StaticId"]["value"])

    def _installed_fixture(self):
        install = self.root / "install"
        (install / "steamapps").mkdir(parents=True)
        (install / "steamapps" / "appmanifest_2394010.acf").write_text('"buildid" "24181105"\n')
        world = install / "Pal/Saved/SaveGames/0/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        (world / "Players").mkdir(parents=True)
        (world / "Level.sav").write_bytes(b"original-level")
        return install, world

    def test_execute_restores_original_world_when_startup_verification_fails(self):
        install, world = self._installed_fixture()
        state = self.root / "state"
        reviewed_sha = "a" * 64
        calls = []

        def runner(argv, **_kwargs):
            calls.append([str(item) for item in argv])
            if str(argv[0]).endswith("backup.sh"):
                self.assertEqual("true", _kwargs["env"]["PALWORLD_LOCK_HELD"])
                self.assertEqual("true", _kwargs["env"]["PALWORLD_BACKUP_ALREADY_SAVED"])
            if argv[:4] == ["systemctl", "is-active", "--quiet", "palworld.service"]:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if str(argv[0]).endswith("rest-client.py") and argv[-1] == "players":
                return SimpleNamespace(returncode=0, stdout='{"players":[]}', stderr="")
            if str(argv[0]).endswith("rest-client.py") and argv[-1] == "info":
                return SimpleNamespace(returncode=1, stdout="", stderr="not ready")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        def mutate(_payload, staged, _parser):
            (staged / "Level.sav").write_bytes(b"mutated-level")
            return {"schema": {"fixture": True}, "effects": {}, "diagnostics": {}}

        reviewed = {"reviewed_sha256": reviewed_sha, "schema": {"fixture": True},
                    "action": "rename_player", "status": "ready"}
        payload = {"action": "rename_player", "uid": "a" * 32, "new_name": "Grace"}
        with mock.patch.object(save_repair.SAVE_INTELLIGENCE, "ensure_parser", return_value=self.parser), \
             mock.patch.object(save_repair, "plan", return_value=reviewed), \
             mock.patch.object(save_repair, "apply_staged", side_effect=mutate), \
             mock.patch.object(save_repair.time, "sleep"):
            with self.assertRaisesRegex(ValueError, "REST startup"):
                save_repair.execute(payload, world, install, state, self.parser, reviewed_sha,
                                    "RENAME PLAYER", runner=runner, require_root=False, ready_attempts=1)
        self.assertEqual(b"original-level", (world / "Level.sav").read_bytes())
        backup_index = next(i for i, call in enumerate(calls) if call[0].endswith("backup.sh"))
        stop_index = next(i for i, call in enumerate(calls) if call == ["systemctl", "stop", "palworld.service"])
        self.assertLess(backup_index, stop_index)
        self.assertFalse(any(world.parent.glob(".repair-*")))
        self.assertFalse(any(world.parent.glob(".palworldselfhost-before-repair-*")))

    def test_execute_inactive_world_preserves_prior_and_does_not_start_service(self):
        install, world = self._installed_fixture()
        state = self.root / "state"
        reviewed_sha = "b" * 64
        calls = []

        def runner(argv, **_kwargs):
            calls.append([str(item) for item in argv])
            if str(argv[0]).endswith("backup.sh"):
                self.assertEqual("true", _kwargs["env"]["PALWORLD_LOCK_HELD"])
                self.assertEqual("true", _kwargs["env"]["PALWORLD_BACKUP_ALREADY_SAVED"])
            rc = 3 if argv[:4] == ["systemctl", "is-active", "--quiet", "palworld.service"] else 0
            return SimpleNamespace(returncode=rc, stdout="", stderr="")

        def mutate(_payload, staged, _parser):
            (staged / "Level.sav").write_bytes(b"mutated-level")
            return {"schema": {"fixture": True}, "effects": {"after_name": "Grace"}, "diagnostics": {}}

        reviewed = {"reviewed_sha256": reviewed_sha, "schema": {"fixture": True},
                    "action": "rename_player", "status": "ready"}
        payload = {"action": "rename_player", "uid": "a" * 32, "new_name": "Grace"}
        with mock.patch.object(save_repair.SAVE_INTELLIGENCE, "ensure_parser", return_value=self.parser), \
             mock.patch.object(save_repair, "plan", return_value=reviewed), \
             mock.patch.object(save_repair, "apply_staged", side_effect=mutate):
            result = save_repair.execute(payload, world, install, state, self.parser, reviewed_sha,
                                         "RENAME PLAYER", runner=runner, require_root=False)
        self.assertEqual("applied", result["status"])
        self.assertEqual(b"mutated-level", (world / "Level.sav").read_bytes())
        preserved = pathlib.Path(result["preserved_world"])
        self.assertEqual(b"original-level", (preserved / "Level.sav").read_bytes())
        self.assertFalse(any(call[:2] == ["systemctl", "start"] for call in calls))


if __name__ == "__main__":
    unittest.main()
