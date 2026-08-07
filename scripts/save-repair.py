#!/usr/bin/env python3
"""Plan and execute build-bound, transactional offline Palworld save mutations.

Only transformations independently implemented and verified here are exposed.
The external GPL codec remains a separately executed, immutable-hash-pinned
program.  Its advertised backup commands are intentionally not used.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import time


GUID_RE = re.compile(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
PLAYER_FILE_RE = re.compile(r"[0-9a-fA-F]{32}\.sav")
MAX_WORLD_FILES = 100_000
MAX_WORLD_BYTES = 128 * 1024**3
SUPPORTED_ACTIONS = {"rename_player", "migrate_player", "replace_player", "cleanup_duplicates",
                     "cleanup_graph", "delete_inactive_players", "transfer_player",
                     "edit_inventory_slot", "edit_player_progression", "edit_owned_pal"}
SUPPORTED_GAME_BUILDS = {"24181105"}
CONFIRMATIONS = {"rename_player": "RENAME PLAYER", "migrate_player": "MIGRATE PLAYER",
                 "replace_player": "REPLACE PLAYER IDENTITY",
                 "cleanup_duplicates": "CLEAN DUPLICATE PLAYERS",
                 "cleanup_graph": "CLEAN SAVE GRAPH",
                 "delete_inactive_players": "DELETE INACTIVE PLAYERS",
                 "transfer_player": "TRANSFER PLAYER",
                 "edit_inventory_slot": "EDIT INVENTORY SLOT",
                 "edit_player_progression": "EDIT PLAYER PROGRESSION",
                 "edit_owned_pal": "EDIT OWNED PAL"}
ZERO_GUID = "00000000000000000000000000000000"
INVENTORY_CONTAINERS = {
    "common": "CommonContainerId", "drop": "DropSlotContainerId",
    "essential": "EssentialContainerId", "food": "FoodEquipContainerId",
    "armor": "PlayerEquipArmorContainerId", "weapons": "WeaponLoadOutContainerId",
}


def _load_save_intelligence():
    path = pathlib.Path(__file__).resolve().with_name("save-intelligence.py")
    spec = importlib.util.spec_from_file_location("palworld_save_intelligence", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load the pinned save codec adapter")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SAVE_INTELLIGENCE = _load_save_intelligence()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_guid(value: str, field: str = "UID") -> str:
    text = str(value or "").strip()
    if not GUID_RE.fullmatch(text):
        raise ValueError(f"{field} must be a 32-hex or canonical UUID value")
    return text.replace("-", "").lower()


def display_guid(value: str) -> str:
    value = normalize_guid(value)
    return f"{value[:8]}-{value[8:12]}-{value[12:16]}-{value[16:20]}-{value[20:]}"


def render_replacement(original: str, replacement: str) -> str:
    replacement = normalize_guid(replacement)
    rendered = display_guid(replacement) if "-" in original else replacement
    return rendered.upper() if original.isupper() else rendered.lower()


def guid_value(value) -> str | None:
    if isinstance(value, dict) and "value" in value:
        return guid_value(value["value"])
    if isinstance(value, str) and GUID_RE.fullmatch(value):
        return normalize_guid(value)
    return None


def world_data(document: dict) -> dict:
    try:
        value = document["properties"]["worldSaveData"]["value"]
    except (KeyError, TypeError) as exc:
        raise ValueError("decoded save is missing properties.worldSaveData.value") from exc
    if not isinstance(value, dict):
        raise ValueError("decoded worldSaveData.value is not an object")
    return value


def section_rows(document: dict, name: str, *, required: bool = True) -> list:
    section = world_data(document).get(name)
    if not isinstance(section, dict) or "value" not in section:
        if required:
            raise ValueError(f"decoded save is missing required section {name}")
        return []
    rows = section["value"]
    if isinstance(rows, dict):
        rows = rows.get("values")
    if not isinstance(rows, list):
        raise ValueError(f"decoded section {name} is not an array")
    return rows


def player_record(entry: dict) -> tuple[str | None, str | None, dict | None]:
    try:
        uid = guid_value(entry["key"]["PlayerUId"])
        instance = guid_value(entry["key"]["InstanceId"])
        raw = entry["value"]["RawData"]["value"]["object"]["SaveParameter"]["value"]
        is_player = raw.get("IsPlayer", {}).get("value") is True
        return (uid, instance, raw if is_player else None)
    except (KeyError, TypeError, AttributeError):
        return (None, None, None)


def player_entries(document: dict) -> list[tuple[dict, str, str, dict]]:
    result = []
    for entry in section_rows(document, "CharacterSaveParameterMap"):
        uid, instance, raw = player_record(entry)
        if uid and instance and raw is not None:
            result.append((entry, uid, instance, raw))
    return result


def player_name(raw: dict) -> str:
    value = raw.get("NickName", {}).get("value", "")
    return value if isinstance(value, str) else ""


def schema_signature(document: dict) -> dict:
    header = document.get("header", {})
    if not isinstance(header, dict):
        raise ValueError("decoded save header is not an object")
    sections = sorted(world_data(document))
    return {
        "save_game_version": header.get("save_game_version"),
        "engine": [header.get("engine_version_major"), header.get("engine_version_minor"),
                   header.get("engine_version_patch")],
        "custom_versions_sha256": hashlib.sha256(canonical(header.get("custom_versions", []))).hexdigest(),
        "sections_sha256": hashlib.sha256(canonical(sections)).hexdigest(),
    }


def replace_guid_tree(value, old_uid: str, new_uid: str) -> int:
    """Replace whole GUID values/keys while preserving their original formatting."""
    changed = 0
    if isinstance(value, dict):
        for key in list(value):
            replacement_key = key
            if isinstance(key, str) and GUID_RE.fullmatch(key) and normalize_guid(key) == old_uid:
                replacement_key = render_replacement(key, new_uid)
                if replacement_key in value and replacement_key != key:
                    raise ValueError("UID migration would collide with an existing object key")
                value[replacement_key] = value.pop(key)
                changed += 1
            child = value[replacement_key]
            if isinstance(child, str) and GUID_RE.fullmatch(child) and normalize_guid(child) == old_uid:
                value[replacement_key] = render_replacement(child, new_uid)
                changed += 1
            else:
                changed += replace_guid_tree(child, old_uid, new_uid)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, str) and GUID_RE.fullmatch(item) and normalize_guid(item) == old_uid:
                value[index] = render_replacement(item, new_uid)
                changed += 1
            else:
                changed += replace_guid_tree(item, old_uid, new_uid)
    return changed


def count_guid_tree(value, target: str) -> int:
    target = normalize_guid(target)

    def walk(node) -> int:
        found = 0
        if isinstance(node, dict):
            for key, child in node.items():
                if isinstance(key, str) and GUID_RE.fullmatch(key) and key.replace("-", "").lower() == target:
                    found += 1
                if isinstance(child, str) and GUID_RE.fullmatch(child) and child.replace("-", "").lower() == target:
                    found += 1
                else:
                    found += walk(child)
        elif isinstance(node, list):
            for child in node:
                if isinstance(child, str) and GUID_RE.fullmatch(child) and child.replace("-", "").lower() == target:
                    found += 1
                else:
                    found += walk(child)
        return found

    return walk(value)


def guid_set_tree(value) -> set[str]:
    found = set()
    if isinstance(value, dict):
        for key, child in value.items():
            candidate = guid_value(key)
            if candidate:
                found.add(candidate)
            candidate = guid_value(child)
            if candidate:
                found.add(candidate)
            else:
                found.update(guid_set_tree(child))
    elif isinstance(value, list):
        for child in value:
            candidate = guid_value(child)
            if candidate:
                found.add(candidate)
            else:
                found.update(guid_set_tree(child))
    return found


def row_guid(entry: dict, label: str) -> str:
    try:
        value = guid_value(entry["key"]["ID"])
    except (KeyError, TypeError):
        value = None
    if not value:
        raise ValueError(f"{label} row has no GUID key.ID")
    return value


def player_document_inventory(players_dir: pathlib.Path, parser: pathlib.Path,
                              temporary: pathlib.Path, prefix: str) -> dict[str, dict]:
    documents = {}
    seen = set()
    for index, source in enumerate(sorted(players_dir.iterdir(), key=lambda path: path.name.lower())):
        if not source.is_file() or source.is_symlink() or not PLAYER_FILE_RE.fullmatch(source.name):
            continue
        uid = source.stem.lower()
        if uid in seen:
            raise ValueError("multiple case-variant player saves match the same UID")
        seen.add(uid)
        document, _ = decode(parser, source, temporary, f"{prefix}-{index}")
        documents[uid] = document
    return documents


def dynamic_item_rows(document: dict) -> list:
    section = world_data(document).get("DynamicItemSaveData")
    if not isinstance(section, dict):
        raise ValueError("decoded save is missing required section DynamicItemSaveData")
    value = section.get("value")
    rows = value.get("values") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        raise ValueError("decoded section DynamicItemSaveData is not an array")
    return rows


def dynamic_item_guid(entry: dict) -> str:
    try:
        raw = entry["RawData"]["value"]
        identity = raw["id"]
        value = guid_value(identity.get("local_id_in_created_world"))
    except (KeyError, TypeError, AttributeError):
        value = None
    if not value:
        raise ValueError("DynamicItemSaveData row has no local item GUID")
    return value


def character_slot_raw(slot: dict) -> dict | None:
    try:
        raw = slot["RawData"]["value"]
    except (KeyError, TypeError):
        return None
    return raw if isinstance(raw, dict) else None


def item_slot_dynamic_handle(slot: dict) -> tuple[dict, str, str] | None:
    """Return the writable mapping/key/current GUID for known codec slot shapes."""
    try:
        raw = slot["RawData"]["value"]
        dynamic = raw["item"]["dynamic_id"]
        current = guid_value(dynamic.get("local_id_in_created_world"))
        if current:
            return dynamic, "local_id_in_created_world", current
    except (KeyError, TypeError, AttributeError):
        pass
    try:
        dynamic = slot["ItemId"]["value"]["DynamicId"]["value"]["LocalIdInCreatedWorld"]
        current = guid_value(dynamic)
        if current:
            return dynamic, "value", current
    except (KeyError, TypeError):
        pass
    return None


def group_ids(document: dict) -> set[str]:
    result = set()
    for entry in section_rows(document, "GroupSaveDataMap", required=False):
        try:
            candidate = guid_value(entry.get("key")) or guid_value(
                entry["value"]["RawData"]["value"].get("group_id"))
        except (KeyError, TypeError, AttributeError):
            candidate = None
        if candidate:
            result.add(candidate)
    return result


def item_container_belongs_to_group(entry: dict, known_groups: set[str]) -> bool:
    try:
        belong = entry["value"]["BelongInfo"]["value"]
        candidate = guid_value(belong.get("GroupID")) or guid_value(belong.get("group_id"))
    except (KeyError, TypeError, AttributeError):
        return False
    return bool(candidate and candidate != ZERO_GUID and candidate in known_groups)


def graph_diagnostics(document: dict, player_documents: dict[str, dict]) -> dict:
    characters = {instance for _entry, _uid, instance, _raw in player_entries(document)}
    for entry in section_rows(document, "CharacterSaveParameterMap"):
        try:
            instance = guid_value(entry["key"]["InstanceId"])
        except (KeyError, TypeError):
            instance = None
        if instance:
            characters.add(instance)
    broken_character_slots = []
    for container in section_rows(document, "CharacterContainerSaveData"):
        container_id = row_guid(container, "CharacterContainerSaveData")
        try:
            slots = container["value"]["Slots"]["value"]["values"]
        except (KeyError, TypeError):
            raise ValueError("CharacterContainerSaveData row has no writable Slots array") from None
        if not isinstance(slots, list):
            raise ValueError("CharacterContainerSaveData Slots is not an array")
        for index, slot in enumerate(slots):
            raw = character_slot_raw(slot)
            instance = guid_value(raw.get("instance_id")) if raw else None
            if instance and instance != ZERO_GUID and instance not in characters:
                broken_character_slots.append({"container_id": container_id, "slot": index,
                                               "instance_id": instance})

    dynamic_rows = dynamic_item_rows(document)
    dynamic_ids = [dynamic_item_guid(row) for row in dynamic_rows]
    if len(dynamic_ids) != len(set(dynamic_ids)):
        raise ValueError("DynamicItemSaveData contains duplicate local item GUIDs")
    dynamic_set = set(dynamic_ids)
    broken_item_slots = []
    for container in section_rows(document, "ItemContainerSaveData"):
        container_id = row_guid(container, "ItemContainerSaveData")
        try:
            slots = container["value"]["Slots"]["value"]["values"]
        except (KeyError, TypeError):
            raise ValueError("ItemContainerSaveData row has no writable Slots array") from None
        if not isinstance(slots, list):
            raise ValueError("ItemContainerSaveData Slots is not an array")
        for index, slot in enumerate(slots):
            handle = item_slot_dynamic_handle(slot)
            if handle and handle[2] != ZERO_GUID and handle[2] not in dynamic_set:
                broken_item_slots.append({"container_id": container_id, "slot": index,
                                          "dynamic_item_id": handle[2]})

    known_groups = group_ids(document)
    unreferenced = {"character_containers": [], "item_containers": [], "dynamic_items": []}
    for section_name, key in (("CharacterContainerSaveData", "character_containers"),
                              ("ItemContainerSaveData", "item_containers")):
        rows = section_rows(document, section_name)
        ids = [row_guid(row, section_name) for row in rows]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{section_name} contains duplicate container GUIDs")
        for row, identity in zip(rows, ids):
            references = count_guid_tree(document, identity) - count_guid_tree(row, identity)
            references += sum(count_guid_tree(player, identity) for player in player_documents.values())
            if section_name == "ItemContainerSaveData" and item_container_belongs_to_group(row, known_groups):
                references += 1
            if references == 0:
                unreferenced[key].append(identity)
            elif references < 0:
                raise ValueError(f"invalid reference count for {section_name} {identity}")
    for row, identity in zip(dynamic_rows, dynamic_ids):
        references = count_guid_tree(document, identity) - count_guid_tree(row, identity)
        references += sum(count_guid_tree(player, identity) for player in player_documents.values())
        if references == 0:
            unreferenced["dynamic_items"].append(identity)
        elif references < 0:
            raise ValueError(f"invalid reference count for DynamicItemSaveData {identity}")
    return {"broken_character_slots": broken_character_slots,
            "broken_item_slots": broken_item_slots, **unreferenced}


def cleanup_save_graph(document: dict, player_documents: dict[str, dict]) -> dict:
    before = graph_diagnostics(document, player_documents)
    if not any(before.values()):
        raise ValueError("no broken references or unreferenced containers were found")

    character_ids = set(before["character_containers"])
    item_ids = set(before["item_containers"])
    dynamic_ids = set(before["dynamic_items"])
    character_rows = section_rows(document, "CharacterContainerSaveData")
    item_rows = section_rows(document, "ItemContainerSaveData")
    dynamic_rows = dynamic_item_rows(document)

    character_rows[:] = [row for row in character_rows
                         if row_guid(row, "CharacterContainerSaveData") not in character_ids]
    item_rows[:] = [row for row in item_rows if row_guid(row, "ItemContainerSaveData") not in item_ids]
    dynamic_rows[:] = [row for row in dynamic_rows if dynamic_item_guid(row) not in dynamic_ids]

    broken_characters = {(row["container_id"], row["slot"]) for row in before["broken_character_slots"]}
    for container in character_rows:
        container_id = row_guid(container, "CharacterContainerSaveData")
        slots = container["value"]["Slots"]["value"]["values"]
        for index, slot in enumerate(slots):
            if (container_id, index) not in broken_characters:
                continue
            raw = character_slot_raw(slot)
            current = raw.get("instance_id")
            if not isinstance(current, str):
                raise ValueError("broken character slot instance ID is not writable")
            raw["instance_id"] = render_replacement(current, ZERO_GUID)
            if isinstance(raw.get("player_uid"), str) and guid_value(raw["player_uid"]):
                raw["player_uid"] = render_replacement(raw["player_uid"], ZERO_GUID)
            if "permission_tribe_id" in raw:
                raw["permission_tribe_id"] = 0
            elif isinstance(slot.get("PermissionTribeID"), dict):
                permission = slot["PermissionTribeID"].get("value")
                if isinstance(permission, dict) and "value" in permission:
                    permission["value"] = "EPalTribeID::None"
                else:
                    raise ValueError("broken character slot permission is not writable")
            else:
                raise ValueError("broken character slot has no writable permission field")

    broken_items = {(row["container_id"], row["slot"]) for row in before["broken_item_slots"]}
    for container in item_rows:
        container_id = row_guid(container, "ItemContainerSaveData")
        slots = container["value"]["Slots"]["value"]["values"]
        for index, slot in enumerate(slots):
            if (container_id, index) not in broken_items:
                continue
            handle = item_slot_dynamic_handle(slot)
            if not handle:
                raise ValueError("broken item slot dynamic ID is not writable")
            mapping, key, current = handle
            mapping[key] = render_replacement(current if isinstance(mapping[key], str) else mapping[key]["value"],
                                              ZERO_GUID)

    intermediate = graph_diagnostics(document, player_documents)
    cascading_dynamic = set(intermediate["dynamic_items"])
    if cascading_dynamic:
        dynamic_rows[:] = [row for row in dynamic_rows
                           if dynamic_item_guid(row) not in cascading_dynamic]
        dynamic_ids.update(cascading_dynamic)
    after = graph_diagnostics(document, player_documents)
    if any(after.values()):
        raise ValueError("save graph cleanup did not eliminate every reviewed defect")
    return {"broken_character_slots_repaired": len(before["broken_character_slots"]),
            "broken_item_slots_repaired": len(before["broken_item_slots"]),
            "character_containers_removed": len(character_ids),
            "item_containers_removed": len(item_ids),
            "dynamic_items_removed": len(dynamic_ids)}


def bounded_days(value) -> int:
    if isinstance(value, bool):
        raise ValueError("inactive_days must be an integer from 1 through 3650")
    try:
        days = int(value)
    except (TypeError, ValueError):
        raise ValueError("inactive_days must be an integer from 1 through 3650") from None
    if str(value).strip() != str(days) or not 1 <= days <= 3650:
        raise ValueError("inactive_days must be an integer from 1 through 3650")
    return days


def world_real_ticks(document: dict) -> int:
    try:
        value = world_data(document)["GameTimeSaveData"]["value"]["RealDateTimeTicks"]["value"]
    except (KeyError, TypeError):
        raise ValueError("save has no writable GameTimeSaveData.RealDateTimeTicks") from None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("save real-time ticks are invalid")
    return value


def guild_member_uid(member: dict) -> str | None:
    return guid_value(member.get("player_uid")) if isinstance(member, dict) else None


def guild_member_last_ticks(member: dict) -> int | None:
    if not isinstance(member, dict):
        return None
    value = member.get("last_online_real_time")
    if not isinstance(value, int) or isinstance(value, bool):
        info = member.get("player_info")
        value = info.get("last_online_real_time") if isinstance(info, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def character_raw(entry: dict) -> dict | None:
    try:
        raw = entry["value"]["RawData"]["value"]["object"]["SaveParameter"]["value"]
    except (KeyError, TypeError):
        return None
    return raw if isinstance(raw, dict) else None


def nested_spawner_rows(document: dict) -> list:
    section = world_data(document).get("MapObjectSpawnerInStageSaveData")
    try:
        values = section["value"]
        if not isinstance(values, list) or len(values) != 1:
            raise TypeError
        rows = values[0]["value"]["SpawnerDataMapByLevelObjectInstanceId"]["value"]
    except (KeyError, TypeError, IndexError):
        raise ValueError("decoded MapObjectSpawnerInStageSaveData has an unknown shape") from None
    if not isinstance(rows, list):
        raise ValueError("decoded MapObjectSpawnerInStageSaveData rows is not an array")
    return rows


def entity_guid(entry: dict, label: str) -> str:
    candidate = None
    try:
        if label == "BaseCampSaveData":
            candidate = guid_value(entry["key"])
        elif label == "WorkSaveData":
            candidate = guid_value(entry["RawData"]["value"]["id"])
        elif label == "MapObjectSaveData":
            candidate = guid_value(entry["MapObjectInstanceId"])
        elif label == "MapObjectSpawnerInStageSaveData":
            candidate = guid_value(entry["key"])
        elif label == "CharacterSaveParameterMap":
            candidate = guid_value(entry["key"]["InstanceId"])
        elif label in {"CharacterContainerSaveData", "ItemContainerSaveData"}:
            candidate = row_guid(entry, label)
        elif label == "DynamicItemSaveData":
            candidate = dynamic_item_guid(entry)
    except (KeyError, TypeError):
        candidate = None
    if not candidate:
        raise ValueError(f"{label} row has no recognized entity GUID")
    return candidate


def delete_empty_guild_owned_graph(document: dict, group: dict, group_raw: dict,
                                   candidate_uids: set[str], player_documents: dict[str, dict]) -> dict:
    """Delete a complete empty-guild base/build graph, refusing shared ownership."""
    group_id = guid_value(group_raw.get("group_id")) or guid_value(group.get("key"))
    if not group_id:
        raise ValueError("empty inactive guild has no usable group ID")
    base_values = group_raw.get("base_ids", [])
    point_values = group_raw.get("map_object_instance_ids_base_camp_points", [])
    if not isinstance(base_values, list) or not isinstance(point_values, list):
        raise ValueError("empty inactive guild base IDs or Palbox map IDs is not an array")
    base_ids = {guid_value(value) for value in base_values}
    point_ids = {guid_value(value) for value in point_values}
    if None in base_ids or None in point_ids:
        raise ValueError("empty inactive guild has a malformed base or Palbox map ID")
    if not base_ids and not point_ids:
        return {"bases_removed": 0, "map_objects_removed": 0, "work_records_removed": 0,
                "spawners_removed": 0, "base_characters_removed": 0,
                "base_character_containers_removed": 0,
                "base_item_containers_removed": 0, "base_dynamic_items_removed": 0,
                "owned_entity_ids": []}

    owners = {
        "BaseCampSaveData": section_rows(document, "BaseCampSaveData"),
        "WorkSaveData": section_rows(document, "WorkSaveData"),
        "MapObjectSaveData": section_rows(document, "MapObjectSaveData"),
        "MapObjectSpawnerInStageSaveData": nested_spawner_rows(document),
        "CharacterSaveParameterMap": section_rows(document, "CharacterSaveParameterMap"),
        "CharacterContainerSaveData": section_rows(document, "CharacterContainerSaveData"),
        "ItemContainerSaveData": section_rows(document, "ItemContainerSaveData"),
        "DynamicItemSaveData": dynamic_item_rows(document),
    }
    registry: dict[str, tuple[str, dict, list]] = {}
    for label, rows in owners.items():
        for row in rows:
            if label == "CharacterSaveParameterMap" and player_record(row)[2] is not None:
                continue
            identity = entity_guid(row, label)
            if identity in registry:
                raise ValueError(f"duplicate save entity GUID {display_guid(identity)}")
            registry[identity] = (label, row, rows)
    if not base_ids <= {identity for identity, item in registry.items()
                        if item[0] == "BaseCampSaveData"}:
        raise ValueError("empty inactive guild references a missing base camp")
    if not point_ids <= {identity for identity, item in registry.items()
                         if item[0] == "MapObjectSaveData"}:
        raise ValueError("empty inactive guild references a missing Palbox map object")

    anchors = {group_id, *candidate_uids, *base_ids, *point_ids}
    owned = set(base_ids | point_ids)
    seed_labels = {"BaseCampSaveData", "WorkSaveData", "MapObjectSaveData",
                   "MapObjectSpawnerInStageSaveData"}
    for identity, (label, row, _rows) in registry.items():
        if label in seed_labels and guid_set_tree(row) & anchors:
            owned.add(identity)
    pending = list(owned)
    while pending:
        identity = pending.pop()
        linked = guid_set_tree(registry[identity][1]) & registry.keys()
        for child in linked - owned:
            owned.add(child)
            pending.append(child)

    other_bases = {identity for identity in owned
                   if registry[identity][0] == "BaseCampSaveData" and identity not in base_ids}
    if other_bases:
        raise ValueError("inactive guild ownership graph reaches a base outside its declared base list")

    counts = {label: 0 for label in owners}
    removed_instances = []
    for identity in owned:
        label, row, rows = registry[identity]
        rows.remove(row)
        counts[label] += 1
        if label == "CharacterSaveParameterMap":
            removed_instances.append(identity)
    for identity in owned:
        if count_guid_tree(document, identity):
            raise ValueError("inactive guild entity is still referenced outside its owned deletion graph")
        if count_guid_tree(player_documents, identity):
            raise ValueError("inactive guild entity is still referenced by a retained player save")
    if count_guid_tree(document, group_id) or count_guid_tree(player_documents, group_id):
        raise ValueError("inactive guild ID is still referenced outside its owned deletion graph")
    return {"bases_removed": counts["BaseCampSaveData"],
            "map_objects_removed": counts["MapObjectSaveData"],
            "work_records_removed": counts["WorkSaveData"],
            "spawners_removed": counts["MapObjectSpawnerInStageSaveData"],
            "base_characters_removed": counts["CharacterSaveParameterMap"],
            "base_character_containers_removed": counts["CharacterContainerSaveData"],
            "base_item_containers_removed": counts["ItemContainerSaveData"],
            "base_dynamic_items_removed": counts["DynamicItemSaveData"],
            "owned_entity_ids": sorted(owned), "removed_instances": removed_instances}


def delete_inactive_players(document: dict, player_documents: dict[str, dict], inactive_days) -> dict:
    days = bounded_days(inactive_days)
    now_ticks = world_real_ticks(document)
    threshold = days * 86400 * 10_000_000
    memberships: dict[str, list[tuple[dict, dict]]] = {}
    for group in section_rows(document, "GroupSaveDataMap", required=False):
        try:
            raw = group["value"]["RawData"]["value"]
        except (KeyError, TypeError):
            continue
        members = raw.get("players", []) if isinstance(raw, dict) else []
        if not isinstance(members, list):
            raise ValueError("guild players is not an array")
        for member in members:
            uid = guild_member_uid(member)
            last = guild_member_last_ticks(member)
            if uid and last is not None and 0 <= last <= now_ticks and now_ticks - last > threshold:
                memberships.setdefault(uid, []).append((group, member))
    if not memberships:
        raise ValueError(f"no players have been inactive for more than {days} days")
    if any(len(rows) != 1 for rows in memberships.values()):
        raise ValueError("an inactive player has ambiguous membership in multiple guilds")

    entries_by_uid: dict[str, list[tuple[dict, str]]] = {}
    for entry, uid, instance, _raw in player_entries(document):
        entries_by_uid.setdefault(uid, []).append((entry, instance))
    candidates = sorted(memberships)
    for uid in candidates:
        if len(entries_by_uid.get(uid, [])) != 1:
            raise ValueError("inactive deletion requires exactly one player character per candidate")
        player = player_documents.get(uid)
        if player is None:
            raise ValueError("inactive player's save file is missing")
        saved_uid, _saved_instance = player_save_identity(player)
        if saved_uid != uid:
            raise ValueError("inactive player's save UID does not match its filename/Level identity")

    character_rows = section_rows(document, "CharacterSaveParameterMap")
    removed_instances = set()
    removed_characters = 0
    for entry in list(character_rows):
        try:
            key_uid = guid_value(entry["key"]["PlayerUId"])
            instance = guid_value(entry["key"]["InstanceId"])
        except (KeyError, TypeError):
            key_uid = instance = None
        raw = character_raw(entry)
        owner = guid_value(raw.get("OwnerPlayerUId")) if raw else None
        if key_uid in memberships or owner in memberships:
            if not instance:
                raise ValueError("owned character selected for deletion has no instance ID")
            character_rows.remove(entry)
            removed_instances.add(instance)
            removed_characters += 1

    groups = section_rows(document, "GroupSaveDataMap", required=False)
    removed_guilds = 0
    base_effects = {"bases_removed": 0, "map_objects_removed": 0,
                    "work_records_removed": 0, "spawners_removed": 0,
                    "base_characters_removed": 0,
                    "base_character_containers_removed": 0,
                    "base_item_containers_removed": 0,
                    "base_dynamic_items_removed": 0}
    for uid in candidates:
        player_documents.pop(uid, None)
    for group in list(groups):
        try:
            raw = group["value"]["RawData"]["value"]
        except (KeyError, TypeError):
            continue
        if not isinstance(raw, dict):
            continue
        members = raw.get("players", [])
        if not isinstance(members, list):
            raise ValueError("guild players is not an array")
        removed_members = [member for member in members if guild_member_uid(member) in memberships]
        if not removed_members:
            continue
        remaining = [member for member in members if guild_member_uid(member) not in memberships]
        admin = guid_value(raw.get("admin_player_uid"))
        if admin in memberships and remaining:
            raise ValueError("inactive player administers a guild with retained members")
        raw["players"] = remaining
        handles = raw.get("individual_character_handle_ids", [])
        if isinstance(handles, list):
            raw["individual_character_handle_ids"] = [
                handle for handle in handles
                if guid_value(handle.get("guid")) not in memberships
                and guid_value(handle.get("instance_id")) not in removed_instances
            ]
        if not remaining:
            base_ids = raw.get("base_ids", [])
            map_ids = raw.get("map_object_instance_ids_base_camp_points", [])
            if (isinstance(base_ids, list) and base_ids) or (isinstance(map_ids, list) and map_ids):
                groups.remove(group)
                cascade = delete_empty_guild_owned_graph(
                    document, group, raw, set(candidates), player_documents)
                for key in base_effects:
                    base_effects[key] += cascade[key]
                removed_instances.update(cascade["removed_instances"])
                removed_characters += cascade["base_characters_removed"]
            else:
                groups.remove(group)
            removed_guilds += 1

    graph_before = graph_diagnostics(document, player_documents)
    graph_effects = cleanup_save_graph(document, player_documents) if any(graph_before.values()) else {
        "broken_character_slots_repaired": 0, "broken_item_slots_repaired": 0,
        "character_containers_removed": 0, "item_containers_removed": 0,
        "dynamic_items_removed": 0,
    }
    for uid in candidates:
        references = count_guid_tree(document, uid)
        if references:
            raise ValueError(f"inactive player still has {references} owned world references")
    for instance in removed_instances:
        references = count_guid_tree(document, instance)
        if references:
            raise ValueError(f"deleted character instance still has {references} world references")
    return {"inactive_days": days, "world_ticks": now_ticks, "deleted_uids": candidates,
            "player_files_removed": len(candidates), "characters_removed": removed_characters,
            "empty_guilds_removed": removed_guilds, **base_effects,
            "graph_cleanup": graph_effects}


def save_data(document: dict) -> dict:
    try:
        value = document["properties"]["SaveData"]["value"]
    except (KeyError, TypeError):
        raise ValueError("player save is missing properties.SaveData.value") from None
    if not isinstance(value, dict):
        raise ValueError("player SaveData.value is not an object")
    return value


def transfer_player_cross_world(target_level: dict, source_level: dict, source_player: dict,
                                target_player: dict, target_player_documents: dict[str, dict],
                                source_uid, target_uid) -> dict:
    source_uid = normalize_guid(source_uid, "source_uid")
    target_uid = normalize_guid(target_uid, "target_uid")
    source_matches = [row for row in player_entries(source_level) if row[1] == source_uid]
    target_matches = [row for row in player_entries(target_level) if row[1] == target_uid]
    if len(source_matches) != 1 or len(target_matches) != 1:
        raise ValueError("transfer requires exactly one source player and one joined target player")
    source_entry, _uid, source_instance, source_raw = source_matches[0]
    target_entry, _uid, target_instance, _target_raw = target_matches[0]
    if player_save_identity(source_player) != (source_uid, source_instance):
        raise ValueError("source player save identity does not match the source Level record")
    if player_save_identity(target_player) != (target_uid, target_instance):
        raise ValueError("target player save identity does not match the joined target Level record")

    def set_guid_field(mapping: dict, key: str, replacement: str, label: str) -> None:
        current = mapping.get(key)
        original = guid_value(current)
        if not original:
            raise ValueError(f"{label} has no writable GUID value")
        if isinstance(current, str):
            mapping[key] = render_replacement(current, replacement)
        elif replace_guid_tree(current, original, replacement) < 1:
            raise ValueError(f"{label} GUID could not be replaced")

    validation = copy.deepcopy(target_level)
    validation_rows = section_rows(validation, "CharacterSaveParameterMap")
    for entry, uid, _instance, _raw in player_entries(validation):
        if uid == target_uid:
            validation_rows.remove(entry)
    for group in section_rows(validation, "GroupSaveDataMap", required=False):
        try:
            raw = group["value"]["RawData"]["value"]
        except (KeyError, TypeError):
            continue
        if not isinstance(raw, dict):
            continue
        if guid_value(raw.get("admin_player_uid")) == target_uid:
            set_guid_field(raw, "admin_player_uid", ZERO_GUID, "target guild admin_player_uid")
        members = raw.get("players", [])
        if isinstance(members, list):
            raw["players"] = [member for member in members if guild_member_uid(member) != target_uid]
        handles = raw.get("individual_character_handle_ids", [])
        if isinstance(handles, list):
            raw["individual_character_handle_ids"] = [
                handle for handle in handles
                if guid_value(handle.get("guid")) != target_uid
                and guid_value(handle.get("instance_id")) != target_instance
            ]
    target_uid_refs = count_guid_tree(validation, target_uid)
    target_instance_refs = count_guid_tree(validation, target_instance)
    if target_uid_refs or target_instance_refs:
        raise ValueError("joined target identity is not disposable outside its guild membership")

    source_guids = guid_set_tree(source_player)
    source_char_rows = section_rows(source_level, "CharacterContainerSaveData")
    source_item_rows = section_rows(source_level, "ItemContainerSaveData")
    char_ids = {row_guid(row, "CharacterContainerSaveData") for row in source_char_rows}
    item_ids = {row_guid(row, "ItemContainerSaveData") for row in source_item_rows}
    selected_char_ids = char_ids & source_guids
    selected_item_ids = item_ids & source_guids
    if not selected_char_ids or not selected_item_ids:
        raise ValueError("source player save does not bind both character and item containers")

    owned_pals = []
    pal_instances = set()
    for entry in section_rows(source_level, "CharacterSaveParameterMap"):
        raw = character_raw(entry)
        if not raw or guid_value(raw.get("OwnerPlayerUId")) != source_uid:
            continue
        slot_id = None
        for slot_key in ("SlotID", "SlotId"):
            try:
                slot_id = guid_value(raw[slot_key]["value"]["ContainerId"]["value"]["ID"])
            except (KeyError, TypeError):
                continue
            if slot_id:
                break
        if slot_id not in selected_char_ids:
            continue
        try:
            instance = guid_value(entry["key"]["InstanceId"])
        except (KeyError, TypeError):
            instance = None
        if not instance:
            raise ValueError("source owned Pal has no instance ID")
        owned_pals.append(entry)
        pal_instances.add(instance)

    pal_guids = set().union(*(guid_set_tree(entry) for entry in owned_pals))
    selected_item_ids |= item_ids & pal_guids

    selected_char_rows = [row for row in source_char_rows
                          if row_guid(row, "CharacterContainerSaveData") in selected_char_ids]
    for container in selected_char_rows:
        slots = container.get("value", {}).get("Slots", {}).get("value", {}).get("values")
        if not isinstance(slots, list):
            raise ValueError("source character container has no writable Slots array")
        for slot in slots:
            raw = character_slot_raw(slot)
            instance = guid_value(raw.get("instance_id")) if raw else None
            if instance and instance != ZERO_GUID and instance not in pal_instances:
                raise ValueError("source character container includes a character outside the player's party/Palbox graph")

    selected_item_rows = [row for row in source_item_rows
                          if row_guid(row, "ItemContainerSaveData") in selected_item_ids]
    selected_item_guids = set().union(*(guid_set_tree(row) for row in selected_item_rows))
    source_dynamic_rows = dynamic_item_rows(source_level)
    source_dynamic_ids = [dynamic_item_guid(row) for row in source_dynamic_rows]
    if len(source_dynamic_ids) != len(set(source_dynamic_ids)):
        raise ValueError("source world has duplicate dynamic-item GUIDs")
    selected_dynamic_rows = [row for row in source_dynamic_rows
                             if dynamic_item_guid(row) in selected_item_guids]
    referenced_dynamic_ids = set()
    for container in selected_item_rows:
        slots = container.get("value", {}).get("Slots", {}).get("value", {}).get("values")
        if not isinstance(slots, list):
            raise ValueError("source item container has no writable Slots array")
        for slot in slots:
            handle = item_slot_dynamic_handle(slot)
            if handle and handle[2] != ZERO_GUID:
                referenced_dynamic_ids.add(handle[2])
    if referenced_dynamic_ids - set(source_dynamic_ids):
        raise ValueError("source player inventory references a missing dynamic item")

    target_char_rows = section_rows(target_level, "CharacterContainerSaveData")
    target_item_rows = section_rows(target_level, "ItemContainerSaveData")
    target_dynamic_rows = dynamic_item_rows(target_level)
    target_char_ids = {row_guid(row, "CharacterContainerSaveData") for row in target_char_rows}
    target_item_ids = {row_guid(row, "ItemContainerSaveData") for row in target_item_rows}
    target_dynamic_ids = {dynamic_item_guid(row) for row in target_dynamic_rows}
    if selected_char_ids & target_char_ids or selected_item_ids & target_item_ids:
        raise ValueError("source container GUID collides with an existing target container")
    copied_dynamic_ids = {dynamic_item_guid(row) for row in selected_dynamic_rows}
    if copied_dynamic_ids & target_dynamic_ids:
        raise ValueError("source dynamic-item GUID collides with an existing target item")
    target_instances = {guid_value(entry.get("key", {}).get("InstanceId"))
                        for entry in section_rows(target_level, "CharacterSaveParameterMap")}
    if pal_instances & target_instances:
        raise ValueError("source Pal instance GUID collides with an existing target character")
    if source_uid != target_uid and any(uid == source_uid for _e, uid, _i, _r in player_entries(target_level)):
        raise ValueError("source player UID already exists in the target world")

    target_group = None
    target_group_id = None
    target_groups = []
    for group in section_rows(target_level, "GroupSaveDataMap", required=False):
        try:
            raw = group["value"]["RawData"]["value"]
        except (KeyError, TypeError):
            continue
        if any(guild_member_uid(member) == target_uid for member in raw.get("players", [])):
            target_groups.append((group, raw))
    if len(target_groups) != 1:
        raise ValueError("joined target identity must have exactly one target-world guild membership")
    target_group_entry, target_group = target_groups[0]
    target_group_id = guid_value(target_group.get("group_id")) or guid_value(target_group_entry.get("key"))
    if not target_group_id:
        raise ValueError("target guild has no usable group ID")

    copied_player_entry = copy.deepcopy(source_entry)
    copied_pals = copy.deepcopy(owned_pals)
    copied_char_containers = copy.deepcopy(selected_char_rows)
    copied_item_containers = copy.deepcopy(selected_item_rows)
    copied_dynamic = copy.deepcopy(selected_dynamic_rows)
    for node in [copied_player_entry, copied_pals, copied_char_containers,
                 copied_item_containers, copied_dynamic]:
        replace_guid_tree(node, source_uid, target_uid)
    replace_guid_tree(copied_player_entry, source_instance, target_instance)
    new_player = copy.deepcopy(source_player)
    replace_guid_tree(new_player, source_uid, target_uid)
    replace_guid_tree(new_player, source_instance, target_instance)

    target_save = save_data(target_player)
    new_save = save_data(new_player)
    if "GroupId" in target_save:
        new_save["GroupId"] = copy.deepcopy(target_save["GroupId"])
    elif "GroupId" in new_save:
        set_guid_field(new_save, "GroupId", ZERO_GUID, "source player GroupId")

    for entry in [copied_player_entry, *copied_pals]:
        try:
            entry_raw = entry["value"]["RawData"]["value"]
            if "group_id" in entry_raw:
                set_guid_field(entry_raw, "group_id", target_group_id,
                               "transferred character group_id")
        except (KeyError, TypeError):
            pass

    target_character_rows = section_rows(target_level, "CharacterSaveParameterMap")
    target_character_rows.remove(target_entry)
    target_character_rows.extend([copied_player_entry, *copied_pals])
    target_char_rows.extend(copied_char_containers)
    target_item_rows.extend(copied_item_containers)
    target_dynamic_rows.extend(copied_dynamic)
    target_player.clear()
    target_player.update(new_player)
    target_player_documents[target_uid] = target_player

    if target_group is not None:
        for member in target_group.get("players", []):
            if guild_member_uid(member) != target_uid:
                continue
            source_name = player_name(source_raw)
            if "player_name" in member:
                member["player_name"] = source_name
            info = member.get("player_info")
            if isinstance(info, dict):
                info["player_name"] = source_name
        handles = target_group.setdefault("individual_character_handle_ids", [])
        if not isinstance(handles, list):
            raise ValueError("target guild character handles is not an array")
        existing_handles = {guid_value(handle.get("instance_id")) for handle in handles}
        for instance in sorted(pal_instances):
            if instance not in existing_handles:
                handles.append({"guid": display_guid(ZERO_GUID), "instance_id": display_guid(instance)})

    graph_before = graph_diagnostics(target_level, target_player_documents)
    graph_effects = cleanup_save_graph(target_level, target_player_documents) if any(graph_before.values()) else {
        "broken_character_slots_repaired": 0, "broken_item_slots_repaired": 0,
        "character_containers_removed": 0, "item_containers_removed": 0,
        "dynamic_items_removed": 0,
    }
    final = [row for row in player_entries(target_level) if row[1] == target_uid]
    if len(final) != 1 or final[0][2] != target_instance:
        raise ValueError("transferred player identity was not observed in the target Level")
    if player_save_identity(target_player) != (target_uid, target_instance):
        raise ValueError("transferred target player save identity verification failed")
    return {"source_uid": source_uid, "target_uid": target_uid,
            "source_instance_id": source_instance, "target_instance_id": target_instance,
            "source_name": player_name(source_raw), "pals_transferred": len(copied_pals),
            "character_containers_transferred": len(copied_char_containers),
            "item_containers_transferred": len(copied_item_containers),
            "dynamic_items_transferred": len(copied_dynamic), "graph_cleanup": graph_effects}


def bound_player(level: dict, player_document: dict, uid_value) -> tuple[str, str, dict]:
    uid = normalize_guid(uid_value, "uid")
    matches = [row for row in player_entries(level) if row[1] == uid]
    if len(matches) != 1:
        raise ValueError("offline player edit requires exactly one matching Level player")
    _entry, _uid, instance, raw = matches[0]
    if player_save_identity(player_document) != (uid, instance):
        raise ValueError("player save identity does not match the Level player selected for editing")
    return uid, instance, raw


def numeric_property_handle(prop, label: str) -> tuple[dict, str, int]:
    if not isinstance(prop, dict) or "value" not in prop:
        raise ValueError(f"{label} is not a writable property")
    value = prop["value"]
    if isinstance(value, int) and not isinstance(value, bool):
        return prop, "value", value
    if isinstance(value, dict):
        nested = value.get("value")
        if isinstance(nested, int) and not isinstance(nested, bool):
            return value, "value", nested
    raise ValueError(f"{label} does not contain a writable integer")


def bounded_payload_integer(payload: dict, key: str, minimum: int, maximum: int,
                            *, required: bool = False) -> int | None:
    value = payload.get(key)
    if value is None or value == "":
        if required:
            raise ValueError(f"{key} is required")
        return None
    if isinstance(value, bool) or not re.fullmatch(r"-?[0-9]+", str(value)):
        raise ValueError(f"{key} must be an integer from {minimum} through {maximum}")
    result = int(value)
    if not minimum <= result <= maximum:
        raise ValueError(f"{key} must be an integer from {minimum} through {maximum}")
    return result


def player_character_container_ids(player_document: dict) -> set[str]:
    save = save_data(player_document)
    result = set()
    for field in ("OtomoCharacterContainerId", "PalStorageContainerId"):
        try:
            identity = guid_value(save[field]["value"]["ID"])
        except (KeyError, TypeError):
            identity = None
        if not identity:
            raise ValueError(f"player save has no usable {field}")
        result.add(identity)
    return result


def character_slot_container(raw: dict) -> str | None:
    for key in ("SlotID", "SlotId"):
        try:
            identity = guid_value(raw[key]["value"]["ContainerId"]["value"]["ID"])
        except (KeyError, TypeError):
            continue
        if identity:
            return identity
    return None


def edit_inventory_slot(level: dict, player_document: dict, payload: dict) -> dict:
    uid, _instance, _raw = bound_player(level, player_document, payload.get("uid", ""))
    area = str(payload.get("container", "")).strip().lower()
    if area not in INVENTORY_CONTAINERS:
        raise ValueError("container must be common, drop, essential, food, armor, or weapons")
    slot_index = bounded_payload_integer(payload, "slot_index", 0, 999, required=True)
    stack_count = bounded_payload_integer(payload, "stack_count", 0, 999_999, required=True)
    item_id = str(payload.get("item_id", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_:-]{1,128}", item_id):
        raise ValueError("item_id must be a 1-128 character internal item identifier")
    if (stack_count == 0) != (item_id == "None"):
        raise ValueError("an empty slot must use item_id None and stack_count 0")
    save = save_data(player_document)
    try:
        inventory = save["InventoryInfo"]["value"]
        container_id = guid_value(inventory[INVENTORY_CONTAINERS[area]]["value"]["ID"])
    except (KeyError, TypeError):
        container_id = None
    if not container_id:
        raise ValueError("player save does not expose the selected inventory container")
    rows = [row for row in section_rows(level, "ItemContainerSaveData")
            if row_guid(row, "ItemContainerSaveData") == container_id]
    if len(rows) != 1:
        raise ValueError("selected player inventory container is missing or ambiguous")
    try:
        slots = rows[0]["value"]["Slots"]["value"]["values"]
    except (KeyError, TypeError):
        slots = None
    if not isinstance(slots, list):
        raise ValueError("selected inventory container has no writable Slots array")
    matches = []
    for slot in slots:
        try:
            _mapping, _key, observed = numeric_property_handle(slot["SlotIndex"], "SlotIndex")
        except (KeyError, TypeError):
            raise ValueError("inventory slot has no writable SlotIndex") from None
        if observed == slot_index:
            matches.append(slot)
    if len(matches) != 1:
        raise ValueError("selected inventory slot is missing or ambiguous")
    slot = matches[0]
    try:
        static = slot["ItemId"]["value"]["StaticId"]
        if not isinstance(static, dict) or not isinstance(static.get("value"), str):
            raise TypeError
        stack_map, stack_key, before_count = numeric_property_handle(slot["StackCount"], "StackCount")
    except (KeyError, TypeError):
        raise ValueError("inventory slot has no writable item ID and stack count") from None
    before_item = static["value"]
    dynamic = item_slot_dynamic_handle(slot)
    if before_item != item_id and dynamic and dynamic[2] != ZERO_GUID:
        raise ValueError("item ID replacement is blocked for a slot with dynamic item metadata")
    static["value"] = item_id
    stack_map[stack_key] = stack_count
    return {"uid": uid, "container": area, "container_id": container_id,
            "slot_index": slot_index, "before_item_id": before_item,
            "after_item_id": item_id, "before_stack_count": before_count,
            "after_stack_count": stack_count, "currency_slot": item_id == "Money"}


def edit_player_progression(level: dict, player_document: dict, payload: dict) -> dict:
    uid, instance, raw = bound_player(level, player_document, payload.get("uid", ""))
    requested = {"Level": bounded_payload_integer(payload, "level", 1, 65),
                 "Exp": bounded_payload_integer(payload, "experience", 0, 2**63 - 1)}
    if all(value is None for value in requested.values()):
        raise ValueError("at least one of level or experience is required")
    changes = {}
    for field, value in requested.items():
        if value is None:
            continue
        mapping, key, before = numeric_property_handle(raw.get(field), f"player {field}")
        mapping[key] = value
        changes[field.lower()] = {"before": before, "after": value}
    return {"uid": uid, "instance_id": instance, "changes": changes}


def edit_owned_pal(level: dict, player_document: dict, payload: dict) -> dict:
    uid, _player_instance, _player_raw = bound_player(level, player_document, payload.get("uid", ""))
    instance = normalize_guid(payload.get("pal_instance_id", ""), "pal_instance_id")
    matches = []
    for entry in section_rows(level, "CharacterSaveParameterMap"):
        try:
            found = guid_value(entry["key"]["InstanceId"])
        except (KeyError, TypeError):
            found = None
        raw = character_raw(entry)
        if found == instance and raw is not None and player_record(entry)[2] is None:
            matches.append(raw)
    if len(matches) != 1:
        raise ValueError("owned-Pal edit requires exactly one non-player character instance")
    raw = matches[0]
    owner = guid_value(raw.get("OwnerPlayerUId"))
    container = character_slot_container(raw)
    player_containers = player_character_container_ids(player_document)
    if owner not in {uid, ZERO_GUID} or (owner == ZERO_GUID and container not in player_containers):
        raise ValueError("selected Pal is not owned by the selected player")
    requested = {
        "Level": bounded_payload_integer(payload, "level", 1, 65),
        "Rank": bounded_payload_integer(payload, "rank", 1, 5),
        "Talent_HP": bounded_payload_integer(payload, "talent_hp", 0, 100),
        "Talent_Shot": bounded_payload_integer(payload, "talent_attack", 0, 100),
        "Talent_Defense": bounded_payload_integer(payload, "talent_defense", 0, 100),
    }
    nickname = payload.get("nickname")
    passives = payload.get("passives")
    if nickname is not None:
        nickname = str(nickname).strip()
        if len(nickname) > 24 or any(ord(char) < 32 for char in nickname):
            raise ValueError("nickname must be at most 24 printable characters")
    if passives is not None:
        if not isinstance(passives, list) or len(passives) > 4:
            raise ValueError("passives must be a unique array of at most four internal IDs")
        if any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_:-]{1,128}", value)
               for value in passives):
            raise ValueError("each passive must be a 1-128 character internal ID")
        if len(passives) != len(set(passives)):
            raise ValueError("passives must be a unique array of at most four internal IDs")
    if all(value is None for value in requested.values()) and nickname is None and passives is None:
        raise ValueError("at least one owned-Pal field is required")
    changes = {}
    for field, value in requested.items():
        if value is None:
            continue
        mapping, key, before = numeric_property_handle(raw.get(field), f"Pal {field}")
        mapping[key] = value
        changes[field] = {"before": before, "after": value}
    if nickname is not None:
        prop = raw.get("NickName")
        if not isinstance(prop, dict) or not isinstance(prop.get("value"), str):
            raise ValueError("Pal NickName is not a writable string property")
        changes["NickName"] = {"before": prop["value"], "after": nickname}
        prop["value"] = nickname
    if passives is not None:
        try:
            values = raw["PassiveSkillList"]["value"]["values"]
        except (KeyError, TypeError):
            values = None
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError("Pal PassiveSkillList is not a writable name array")
        changes["PassiveSkillList"] = {"before": list(values), "after": list(passives)}
        values[:] = passives
    return {"uid": uid, "pal_instance_id": instance,
            "character_id": str(raw.get("CharacterID", {}).get("value", "")),
            "changes": changes}


def rename_player(document: dict, uid: str, new_name: str) -> dict:
    uid = normalize_guid(uid)
    if not isinstance(new_name, str) or not new_name.strip() or new_name != new_name.strip():
        raise ValueError("new_name must be non-empty and may not have surrounding whitespace")
    if len(new_name) > 24 or any(ord(char) < 32 for char in new_name):
        raise ValueError("new_name must be at most 24 printable characters")
    matches = [(raw, instance) for _entry, found, instance, raw in player_entries(document) if found == uid]
    if len(matches) != 1:
        raise ValueError(f"rename requires exactly one matching player record; found {len(matches)}")
    raw, instance = matches[0]
    before = player_name(raw)
    nickname = raw.get("NickName")
    if not isinstance(nickname, dict) or not isinstance(nickname.get("value"), str):
        raise ValueError("matching player record has no writable NickName string")
    if before == new_name:
        raise ValueError("new_name already matches the save")
    nickname["value"] = new_name
    guild_changes = 0
    for group in section_rows(document, "GroupSaveDataMap"):
        try:
            raw_group = group["value"]["RawData"]["value"]
            members = raw_group.get("players", [])
        except (KeyError, TypeError, AttributeError):
            continue
        for member in members if isinstance(members, list) else ():
            if guid_value(member.get("player_uid")) != uid:
                continue
            changed = False
            recognized = False
            if isinstance(member.get("player_name"), str):
                recognized = True
                if member["player_name"] != new_name:
                    member["player_name"] = new_name; changed = True
            info = member.get("player_info")
            if isinstance(info, dict) and isinstance(info.get("player_name"), str):
                recognized = True
                if info["player_name"] != new_name:
                    info["player_name"] = new_name; changed = True
            if not recognized:
                raise ValueError("matching guild membership has no recognized player-name field")
            if changed:
                guild_changes += 1
    return {"uid": uid, "instance_id": instance, "before_name": before,
            "after_name": new_name, "guild_memberships_updated": guild_changes}


def migrate_player(level_document: dict, player_document: dict, old_uid: str, new_uid: str) -> dict:
    old_uid = normalize_guid(old_uid, "old_uid")
    new_uid = normalize_guid(new_uid, "new_uid")
    if old_uid == new_uid:
        raise ValueError("old_uid and new_uid must differ")
    entries = player_entries(level_document)
    old = [(raw, instance) for _entry, uid, instance, raw in entries if uid == old_uid]
    target = [row for row in entries if row[1] == new_uid]
    if len(old) != 1:
        raise ValueError(f"migration requires exactly one source player record; found {len(old)}")
    if target:
        raise ValueError("new_uid already exists in the target world; host replacement is not yet supported")
    level_replacements = replace_guid_tree(level_document, old_uid, new_uid)
    player_replacements = replace_guid_tree(player_document, old_uid, new_uid)
    if level_replacements < 1 or player_replacements < 1:
        raise ValueError("migration did not find the UID in both Level.sav and the player save")
    remaining = [uid for _entry, uid, _instance, _raw in player_entries(level_document) if uid == old_uid]
    created = [row for row in player_entries(level_document) if row[1] == new_uid]
    if remaining or len(created) != 1:
        raise ValueError("post-migration player identity verification failed")
    return {"old_uid": old_uid, "new_uid": new_uid, "instance_id": old[0][1],
            "player_name": player_name(old[0][0]), "level_replacements": level_replacements,
            "player_save_replacements": player_replacements}


def replace_player_identity(level_document: dict, player_document: dict, target_document: dict,
                            old_uid: str, target_uid: str) -> dict:
    """Replace a disposable joined identity with an existing player's progress."""
    old_uid = normalize_guid(old_uid, "old_uid")
    target_uid = normalize_guid(target_uid, "target_uid")
    if old_uid == target_uid:
        raise ValueError("old_uid and target_uid must differ")
    entries = player_entries(level_document)
    source = [(entry, instance, raw) for entry, uid, instance, raw in entries if uid == old_uid]
    target = [(entry, instance, raw) for entry, uid, instance, raw in entries if uid == target_uid]
    if len(source) != 1 or len(target) != 1:
        raise ValueError(f"replacement requires exactly one source and target player record; found {len(source)} and {len(target)}")
    source_entry, source_instance, source_raw = source[0]
    target_entry, target_instance, target_raw = target[0]
    if source_instance == target_instance:
        raise ValueError("source and target player records share an instance ID")
    if count_guid_tree(player_document, old_uid) < 1 or count_guid_tree(player_document, source_instance) < 1:
        raise ValueError("source player save does not contain its reviewed UID and instance ID")
    if count_guid_tree(target_document, target_uid) < 1 or count_guid_tree(target_document, target_instance) < 1:
        raise ValueError("target player save does not contain its reviewed UID and instance ID")

    character_rows = section_rows(level_document, "CharacterSaveParameterMap")
    character_rows.remove(target_entry)
    removed_members = 0
    removed_guilds = 0
    groups = section_rows(level_document, "GroupSaveDataMap")
    for group_entry in list(groups):
        try:
            raw = group_entry["value"]["RawData"]["value"]
        except (KeyError, TypeError):
            continue
        if not isinstance(raw, dict):
            continue
        members = raw.get("players", [])
        members = members if isinstance(members, list) else []
        target_members = [member for member in members if guid_value(member.get("player_uid")) == target_uid]
        admin = guid_value(raw.get("admin_player_uid"))
        base_ids = raw.get("base_ids", [])
        base_ids = base_ids if isinstance(base_ids, list) else []
        non_target_members = [member for member in members if guid_value(member.get("player_uid")) != target_uid]
        if admin == target_uid:
            if non_target_members or base_ids:
                raise ValueError("target identity administers a non-empty guild and is not disposable")
            groups.remove(group_entry)
            removed_guilds += 1
            removed_members += len(target_members)
            continue
        if target_members:
            raw["players"] = non_target_members
            removed_members += len(target_members)
        handles = raw.get("individual_character_handle_ids", [])
        if isinstance(handles, list):
            raw["individual_character_handle_ids"] = [
                handle for handle in handles
                if guid_value(handle.get("guid")) != target_uid
                and guid_value(handle.get("instance_id")) != target_instance
            ]

    remaining_uid = count_guid_tree(level_document, target_uid)
    remaining_instance = count_guid_tree(level_document, target_instance)
    if remaining_uid or remaining_instance:
        raise ValueError(
            f"target identity is not disposable; {remaining_uid} UID and {remaining_instance} instance references remain"
        )
    uid_replacements = replace_guid_tree(level_document, old_uid, target_uid)
    instance_replacements = replace_guid_tree(level_document, source_instance, target_instance)
    player_uid_replacements = replace_guid_tree(player_document, old_uid, target_uid)
    player_instance_replacements = replace_guid_tree(player_document, source_instance, target_instance)
    if min(uid_replacements, instance_replacements, player_uid_replacements, player_instance_replacements) < 1:
        raise ValueError("identity replacement did not update every required source identity surface")
    final = [row for row in player_entries(level_document) if row[1] == target_uid]
    if len(final) != 1 or final[0][2] != target_instance or any(row[1] == old_uid for row in player_entries(level_document)):
        raise ValueError("post-replacement identity verification failed")
    return {
        "old_uid": old_uid, "target_uid": target_uid,
        "source_instance_id": source_instance, "target_instance_id": target_instance,
        "source_name": player_name(source_raw), "discarded_target_name": player_name(target_raw),
        "removed_target_memberships": removed_members, "removed_empty_target_guilds": removed_guilds,
        "level_uid_replacements": uid_replacements, "level_instance_replacements": instance_replacements,
        "player_uid_replacements": player_uid_replacements,
        "player_instance_replacements": player_instance_replacements,
    }


def player_save_identity(document: dict) -> tuple[str | None, str | None]:
    try:
        save = document["properties"]["SaveData"]["value"]
        uid = guid_value(save.get("PlayerUId"))
        individual = save.get("IndividualId", {}).get("value", {})
        instance = guid_value(individual.get("InstanceId"))
        individual_uid = guid_value(individual.get("PlayerUId"))
    except (KeyError, TypeError, AttributeError):
        return None, None
    if uid and individual_uid and uid != individual_uid:
        raise ValueError("player save has conflicting PlayerUId and IndividualId.PlayerUId")
    return uid or individual_uid, instance


def cleanup_duplicate_players(level_document: dict, player_documents: dict[str, dict]) -> dict:
    entries = player_entries(level_document)
    by_uid: dict[str, list[tuple[dict, str]]] = {}
    for entry, uid, instance, _raw in entries:
        by_uid.setdefault(uid, []).append((entry, instance))
    duplicates = {uid: rows for uid, rows in by_uid.items() if len(rows) > 1}
    if not duplicates:
        raise ValueError("no duplicate player records were found")
    groups = section_rows(level_document, "GroupSaveDataMap", required=False)
    decisions = {}
    removal_entries: dict[str, list[tuple[dict, str]]] = {}
    for uid, rows in duplicates.items():
        candidates = {instance for _entry, instance in rows}
        signals = set()
        document = player_documents.get(uid)
        if document is not None:
            saved_uid, saved_instance = player_save_identity(document)
            if saved_uid != uid:
                raise ValueError("duplicate player's save UID does not match its filename/Level identity")
            if saved_instance in candidates:
                signals.add(saved_instance)
            elif saved_instance:
                raise ValueError("duplicate player's save instance does not match any Level record")
        guild_instances = set()
        for group in groups:
            try:
                raw = group["value"]["RawData"]["value"]
            except (KeyError, TypeError):
                continue
            handles = raw.get("individual_character_handle_ids", []) if isinstance(raw, dict) else []
            for handle in handles if isinstance(handles, list) else ():
                if guid_value(handle.get("guid")) == uid:
                    instance = guid_value(handle.get("instance_id"))
                    if instance in candidates:
                        guild_instances.add(instance)
        if len(guild_instances) == 1:
            signals.update(guild_instances)
        elif len(guild_instances) > 1:
            raise ValueError("duplicate player has conflicting guild instance bindings")
        if len(signals) != 1:
            raise ValueError("duplicate player has no single player-save/guild-backed instance to keep")
        keep = next(iter(signals))
        stale = [(entry, instance) for entry, instance in rows if instance != keep]
        canonical_rows = [(entry, instance) for entry, instance in rows if instance == keep]
        if len(canonical_rows) > 1:
            if len({canonical(entry) for entry, _instance in canonical_rows}) != 1:
                raise ValueError("multiple non-identical duplicate records share the canonical instance")
            stale.extend(canonical_rows[1:])
        removal_entries[uid] = stale
        decisions[uid] = {"keep": keep, "remove": sorted(candidates - {keep})}

    character_rows = section_rows(level_document, "CharacterSaveParameterMap")
    removed = []
    for uid, decision in decisions.items():
        for entry, instance in removal_entries[uid]:
            character_rows.remove(entry)
            removed.append({"uid": uid, "instance_id": instance})
        for group in groups:
            try:
                raw = group["value"]["RawData"]["value"]
            except (KeyError, TypeError):
                continue
            handles = raw.get("individual_character_handle_ids", []) if isinstance(raw, dict) else []
            if isinstance(handles, list):
                raw["individual_character_handle_ids"] = [
                    handle for handle in handles
                    if not (guid_value(handle.get("guid")) == uid
                            and guid_value(handle.get("instance_id")) in decision["remove"])
                ]
    for decision in decisions.values():
        for instance in decision["remove"]:
            references = count_guid_tree(level_document, instance)
            if references:
                raise ValueError(f"duplicate instance still has {references} non-canonical references")
    remaining = diagnose(level_document, None)["duplicate_player_uids"]
    if remaining:
        raise ValueError("duplicate cleanup did not produce unique player UIDs")
    return {"players_repaired": len(decisions), "records_removed": len(removed),
            "decisions": decisions}


def diagnose(document: dict, players_dir: pathlib.Path | None = None) -> dict:
    entries = player_entries(document)
    by_uid: dict[str, list[str]] = {}
    by_instance: dict[str, list[str]] = {}
    for _entry, uid, instance, _raw in entries:
        by_uid.setdefault(uid, []).append(instance)
        by_instance.setdefault(instance, []).append(uid)
    files = set()
    if players_dir and players_dir.is_dir():
        files = {path.stem.lower() for path in players_dir.iterdir()
                 if path.is_file() and not path.is_symlink() and PLAYER_FILE_RE.fullmatch(path.name)}
    uids = set(by_uid)
    guild_missing = []
    inactive = []
    for group in section_rows(document, "GroupSaveDataMap", required=False):
        try:
            raw = group["value"]["RawData"]["value"]
        except (KeyError, TypeError):
            continue
        for member in raw.get("players", []) if isinstance(raw, dict) else ():
            uid = guild_member_uid(member)
            if uid and uid not in uids:
                guild_missing.append(uid)
            ticks = guild_member_last_ticks(member)
            if uid and ticks is not None:
                inactive.append({"uid": uid, "last_online_real_time": ticks})
    return {
        "players": len(entries),
        "duplicate_player_uids": {uid: ids for uid, ids in by_uid.items() if len(ids) > 1},
        "duplicate_instance_ids": {instance: ids for instance, ids in by_instance.items() if len(ids) > 1},
        "missing_player_saves": sorted(uids - files) if players_dir else [],
        "orphan_player_saves": sorted(files - uids) if players_dir else [],
        "guild_members_without_character": sorted(set(guild_missing)),
        "inactive_candidates": sorted(inactive, key=lambda row: row["last_online_real_time"]),
    }


def inventory(world: pathlib.Path) -> tuple[list[dict], int]:
    world = world.resolve()
    if not world.is_dir() or world.is_symlink() or not (world / "Level.sav").is_file():
        raise ValueError("world must be an absolute, non-symlinked Palworld world directory")
    files, total = [], 0
    for path in sorted(world.rglob("*")):
        if path.is_symlink():
            raise ValueError("world may not contain symlinks")
        if path.is_dir():
            if path.relative_to(world).as_posix() != "Players":
                raise ValueError("world contains an unexpected directory")
            continue
        if not path.is_file():
            raise ValueError("world may contain only regular files")
        relative = path.relative_to(world).as_posix()
        allowed_root = relative in {"Level.sav", "LevelMeta.sav", "LocalData.sav", "WorldOption.sav", "WorldOptions.sav"}
        allowed_player = relative.startswith("Players/") and PLAYER_FILE_RE.fullmatch(path.name)
        if not allowed_root and not allowed_player:
            raise ValueError(f"world contains unexpected file {relative}")
        size = path.stat().st_size
        total += size
        files.append({"path": relative, "bytes": size, "sha256": sha256(path)})
        if len(files) > MAX_WORLD_FILES or total > MAX_WORLD_BYTES:
            raise ValueError("world exceeds the 100,000-file or 128-GiB safety bound")
    return files, total


def source_world(payload: dict, target_world: pathlib.Path) -> pathlib.Path:
    """Resolve a transfer source as a validated sibling world ID, never a caller path."""
    identity = str(payload.get("source_world_id", "")).strip()
    if not re.fullmatch(r"[0-9a-fA-F]{32}", identity):
        raise ValueError("source_world_id must be exactly 32 hexadecimal characters")
    target_world = pathlib.Path(target_world).resolve()
    matches = [candidate for candidate in target_world.parent.iterdir()
               if candidate.name.lower() == identity.lower()]
    if len(matches) != 1:
        raise ValueError("source world ID does not resolve to exactly one sibling world")
    resolved = matches[0].resolve()
    if resolved == target_world or resolved.name.lower() == target_world.name.lower():
        raise ValueError("source and target worlds must be different")
    inventory(resolved)
    return resolved


def find_player_save(players_dir: pathlib.Path, uid: str, *, required: bool) -> pathlib.Path | None:
    uid = normalize_guid(uid)
    matches = [path for path in players_dir.iterdir() if path.is_file() and not path.is_symlink()
               and PLAYER_FILE_RE.fullmatch(path.name) and path.stem.lower() == uid]
    if len(matches) > 1:
        raise ValueError("multiple case-variant player saves match the same UID")
    if required and not matches:
        raise ValueError("source player save is missing or not a regular file")
    return matches[0] if matches else None


def migrated_player_path(source: pathlib.Path, new_uid: str) -> pathlib.Path:
    uid = normalize_guid(new_uid, "new_uid")
    stem = uid.upper() if source.stem == source.stem.upper() else uid.lower()
    return source.parent / f"{stem}.sav"


def run_codec(parser: pathlib.Path, source: pathlib.Path, output: pathlib.Path, *, from_json=False) -> None:
    command = [str(parser), "convert", str(source), "--from-json" if from_json else "--to-json"]
    if not from_json:
        command.append("--minify-json")
    else:
        command.extend(["--library", "zlib"])
    command.extend(["-f", "-o", str(output)])
    process = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.PIPE, text=True, timeout=1800,
                             env={**os.environ, "PYTHONHASHSEED": "0"})
    if process.returncode or not output.is_file():
        raise ValueError(f"pinned save conversion failed (exit {process.returncode}): {process.stderr[-500:]}")


def decode(parser: pathlib.Path, source: pathlib.Path, directory: pathlib.Path, label: str) -> tuple[dict, pathlib.Path]:
    output = directory / f"{label}.json"
    run_codec(parser, source, output)
    try:
        value = json.loads(output.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("pinned codec produced invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("decoded save is not a JSON object")
    return value, output


def encode_verified(parser: pathlib.Path, document: dict, target: pathlib.Path,
                    directory: pathlib.Path, label: str) -> None:
    source_json = directory / f"{label}-mutated.json"
    encoded = directory / f"{label}-encoded.sav"
    decoded_json = directory / f"{label}-verified.json"
    source_json.write_bytes(canonical(document))
    run_codec(parser, source_json, encoded, from_json=True)
    run_codec(parser, encoded, decoded_json)
    try:
        observed = json.loads(decoded_json.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("encoded save could not be decoded for verification") from exc
    if observed != document:
        raise ValueError("encoded save failed decoded structural equality verification")
    existing = target.stat() if target.exists() else None
    if existing is not None:
        os.chmod(encoded, existing.st_mode & 0o7777)
        if os.geteuid() == 0:
            os.chown(encoded, existing.st_uid, existing.st_gid)
    os.replace(encoded, target)


def restore_tree_ownership(source: pathlib.Path, target: pathlib.Path) -> None:
    """Give a copytree staging tree the ownership of each corresponding source."""
    if os.geteuid() != 0:
        return
    for staged in [target, *target.rglob("*")]:
        relative = staged.relative_to(target)
        original = source / relative
        metadata = original.lstat()
        os.chown(staged, metadata.st_uid, metadata.st_gid, follow_symlinks=False)


def game_build(install: pathlib.Path) -> str | None:
    manifest = install / "steamapps/appmanifest_2394010.acf"
    try:
        match = re.search(r'"buildid"\s+"([0-9]+)"', manifest.read_text())
    except OSError:
        return None
    return match.group(1) if match else None


def transform(payload: dict, level: dict, player: dict | None = None,
              target_player: dict | None = None,
              player_documents: dict[str, dict] | None = None,
              source_level: dict | None = None) -> dict:
    action = payload.get("action")
    if action not in SUPPORTED_ACTIONS:
        raise ValueError("unsupported save mutation action")
    if action == "rename_player":
        return rename_player(level, payload.get("uid", ""), payload.get("new_name", ""))
    if action == "cleanup_duplicates":
        return cleanup_duplicate_players(level, player_documents or {})
    if action == "cleanup_graph":
        return cleanup_save_graph(level, player_documents or {})
    if action == "delete_inactive_players":
        return delete_inactive_players(level, player_documents or {}, payload.get("inactive_days"))
    if action == "transfer_player":
        if source_level is None or player is None or target_player is None:
            raise ValueError("cross-world transfer requires decoded source and target saves")
        return transfer_player_cross_world(
            level, source_level, player, target_player, player_documents or {},
            payload.get("source_uid", ""), payload.get("target_uid", ""))
    if player is None:
        raise ValueError("migration requires a decoded source player save")
    if action == "edit_inventory_slot":
        return edit_inventory_slot(level, player, payload)
    if action == "edit_player_progression":
        return edit_player_progression(level, player, payload)
    if action == "edit_owned_pal":
        return edit_owned_pal(level, player, payload)
    if action == "migrate_player":
        return migrate_player(level, player, payload.get("old_uid", ""), payload.get("new_uid", ""))
    if target_player is None:
        raise ValueError("identity replacement requires a decoded target player save")
    return replace_player_identity(level, player, target_player, payload.get("old_uid", ""),
                                   payload.get("target_uid", ""))


def plan(payload: dict, world: pathlib.Path, parser: pathlib.Path, build: str) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    if payload.get("action") not in SUPPORTED_ACTIONS:
        raise ValueError("unsupported save mutation action")
    if not re.fullmatch(r"[0-9]+", str(build or "")):
        raise ValueError("a numeric installed game build is required to plan save writes")
    if str(build) not in SUPPORTED_GAME_BUILDS:
        raise ValueError(f"installed game build {build} has no validated writable save schema")
    parser = SAVE_INTELLIGENCE.ensure_parser(parser)
    world = pathlib.Path(world).resolve()
    files, total = inventory(world)
    transfer_world = source_world(payload, world) if payload.get("action") == "transfer_player" else None
    transfer_files, transfer_total = inventory(transfer_world) if transfer_world is not None else ([], 0)
    with tempfile.TemporaryDirectory(prefix="palworld-save-repair-plan-") as name:
        temporary = pathlib.Path(name)
        level, _ = decode(parser, world / "Level.sav", temporary, "level")
        signature = schema_signature(level)
        before = diagnose(level, world / "Players")
        mutated_level = copy.deepcopy(level)
        player = target_player = source_level_document = None
        player_documents = {}
        if payload.get("action") == "transfer_player":
            source_uid = normalize_guid(payload.get("source_uid", ""), "source_uid")
            target_uid = normalize_guid(payload.get("target_uid", ""), "target_uid")
            source_path = find_player_save(transfer_world / "Players", source_uid, required=True)
            target_path = find_player_save(world / "Players", target_uid, required=True)
            source_level_document, _ = decode(
                parser, transfer_world / "Level.sav", temporary, "source-level")
            if schema_signature(source_level_document) != signature:
                raise ValueError("source and target worlds do not share the validated save schema")
            player, _ = decode(parser, source_path, temporary, "source-player")
            target_player, _ = decode(parser, target_path, temporary, "target-player")
            player = copy.deepcopy(player)
            target_player = copy.deepcopy(target_player)
            player_documents = player_document_inventory(
                world / "Players", parser, temporary, "target-graph-player")
            player_documents[target_uid] = target_player
        elif payload.get("action") in {"edit_inventory_slot", "edit_player_progression", "edit_owned_pal"}:
            uid = normalize_guid(payload.get("uid", ""), "uid")
            source = find_player_save(world / "Players", uid, required=True)
            player, _ = decode(parser, source, temporary, "player")
            player = copy.deepcopy(player)
        elif payload.get("action") in {"migrate_player", "replace_player"}:
            old_uid = normalize_guid(payload.get("old_uid", ""), "old_uid")
            players_dir = world / "Players"
            source = find_player_save(players_dir, old_uid, required=True)
            target_field = "new_uid" if payload["action"] == "migrate_player" else "target_uid"
            new_uid = normalize_guid(payload.get(target_field, ""), target_field)
            target_path = find_player_save(players_dir, new_uid, required=False)
            if payload["action"] == "migrate_player" and target_path is not None:
                raise ValueError("target player save already exists")
            if payload["action"] == "replace_player" and target_path is None:
                raise ValueError("target player save is missing; the replacement identity must join first")
            player, _ = decode(parser, source, temporary, "player")
            player = copy.deepcopy(player)
            if target_path is not None:
                target_player, _ = decode(parser, target_path, temporary, "target-player")
                target_player = copy.deepcopy(target_player)
        if payload.get("action") == "cleanup_duplicates":
            duplicate_uids = diagnose(level, world / "Players")["duplicate_player_uids"]
            for index, uid in enumerate(sorted(duplicate_uids)):
                source = find_player_save(world / "Players", uid, required=False)
                if source is not None:
                    document, _ = decode(parser, source, temporary, f"duplicate-player-{index}")
                    player_documents[uid] = copy.deepcopy(document)
        elif payload.get("action") in {"cleanup_graph", "delete_inactive_players"}:
            player_documents = player_document_inventory(
                world / "Players", parser, temporary, "graph-player")
        effects = transform(payload, mutated_level, player, target_player, player_documents,
                            source_level_document)
        if schema_signature(mutated_level) != signature:
            raise ValueError("mutation changed the save schema signature")
        after = diagnose(mutated_level, None)
    binding = {
        "version": 1, "action": payload.get("action"), "payload": payload,
        "world": str(world), "game_build": str(build),
        "parser_sha256": SAVE_INTELLIGENCE.PARSER_SHA256,
        "schema": signature, "files": files,
    }
    if transfer_world is not None:
        binding["source_world"] = str(transfer_world)
        binding["source_files"] = transfer_files
    reviewed_sha = hashlib.sha256(canonical(binding)).hexdigest()
    result = {
        "status": "ready", "action": payload["action"], "confirmation": CONFIRMATIONS[payload["action"]],
        "world": world.name, "game_build": str(build), "files": len(files), "bytes": total,
        "reviewed_sha256": reviewed_sha, "schema": signature, "effects": effects,
        "diagnostics_before": before, "diagnostics_after": after,
        "controls": [
            "revalidate every world byte, parser hash, schema signature and installed game build",
            "require a verified zero roster, save, and protected backup before stopping the world",
            "mutate a private staged world and require encode/decode structural equality",
            "atomically swap the world, verify REST startup, and restore the original on any failure",
        ],
    }
    if transfer_world is not None:
        result["source_world"] = transfer_world.name
        result["source_files"] = len(transfer_files)
        result["source_bytes"] = transfer_total
    return result


def run_checked(argv, *, runner=subprocess.run, timeout=1800, allow_failure=False, env=None):
    process = runner(argv, text=True, capture_output=True, timeout=timeout, env=env or os.environ.copy())
    if process.returncode and not allow_failure:
        detail = (process.stdout + process.stderr).strip()[-2000:]
        raise ValueError(f"command failed: {' '.join(map(str, argv))}: {detail}")
    return process


def roster_count(here: pathlib.Path, runner) -> int:
    process = run_checked([str(here / "rest-client.py"), "players"], runner=runner, timeout=20)
    try:
        players = json.loads(process.stdout).get("players", [])
    except (json.JSONDecodeError, AttributeError) as exc:
        raise ValueError("player roster was not valid JSON") from exc
    if not isinstance(players, list):
        raise ValueError("player roster did not contain a players array")
    return len(players)


def wait_ready(here: pathlib.Path, runner, attempts=24) -> bool:
    for _ in range(attempts):
        process = run_checked([str(here / "rest-client.py"), "info"], runner=runner,
                              timeout=15, allow_failure=True)
        if process.returncode == 0:
            return True
        time.sleep(5)
    return False


def apply_staged(payload: dict, world: pathlib.Path, parser: pathlib.Path,
                 transfer_world: pathlib.Path | None = None) -> dict:
    with tempfile.TemporaryDirectory(prefix="palworld-save-repair-write-") as name:
        temporary = pathlib.Path(name)
        level, _ = decode(parser, world / "Level.sav", temporary, "level")
        signature = schema_signature(level)
        player = target_player = source_level_document = None
        player_documents = {}
        old_path = new_path = None
        transfer_target_path = None
        if payload.get("action") == "transfer_player":
            transfer_world = pathlib.Path(transfer_world).resolve() if transfer_world else source_world(payload, world)
            inventory(transfer_world)
            source_uid = normalize_guid(payload.get("source_uid", ""), "source_uid")
            target_uid = normalize_guid(payload.get("target_uid", ""), "target_uid")
            source_path = find_player_save(transfer_world / "Players", source_uid, required=True)
            transfer_target_path = find_player_save(world / "Players", target_uid, required=True)
            source_level_document, _ = decode(
                parser, transfer_world / "Level.sav", temporary, "source-level")
            if schema_signature(source_level_document) != signature:
                raise ValueError("source and target worlds do not share the validated save schema")
            player, _ = decode(parser, source_path, temporary, "source-player")
            target_player, _ = decode(parser, transfer_target_path, temporary, "target-player")
            player_documents = player_document_inventory(
                world / "Players", parser, temporary, "target-graph-player")
            player_documents[target_uid] = target_player
        elif payload.get("action") in {"edit_inventory_slot", "edit_player_progression", "edit_owned_pal"}:
            uid = normalize_guid(payload.get("uid", ""), "uid")
            source = find_player_save(world / "Players", uid, required=True)
            player, _ = decode(parser, source, temporary, "player")
        elif payload.get("action") in {"migrate_player", "replace_player"}:
            old_uid = normalize_guid(payload.get("old_uid", ""), "old_uid")
            target_field = "new_uid" if payload["action"] == "migrate_player" else "target_uid"
            new_uid = normalize_guid(payload.get(target_field, ""), target_field)
            old_path = find_player_save(world / "Players", old_uid, required=True)
            existing_target = find_player_save(world / "Players", new_uid, required=False)
            if payload["action"] == "migrate_player" and existing_target is not None:
                raise ValueError("target player save already exists")
            if payload["action"] == "replace_player" and existing_target is None:
                raise ValueError("target player save is missing; the replacement identity must join first")
            new_path = existing_target or migrated_player_path(old_path, new_uid)
            player, _ = decode(parser, old_path, temporary, "player")
            if existing_target is not None:
                target_player, _ = decode(parser, existing_target, temporary, "target-player")
        if payload.get("action") == "cleanup_duplicates":
            duplicate_uids = diagnose(level, world / "Players")["duplicate_player_uids"]
            for index, uid in enumerate(sorted(duplicate_uids)):
                source = find_player_save(world / "Players", uid, required=False)
                if source is not None:
                    document, _ = decode(parser, source, temporary, f"duplicate-player-{index}")
                    player_documents[uid] = document
        elif payload.get("action") in {"cleanup_graph", "delete_inactive_players"}:
            player_documents = player_document_inventory(
                world / "Players", parser, temporary, "graph-player")
        effects = transform(payload, level, player, target_player, player_documents,
                            source_level_document)
        if schema_signature(level) != signature:
            raise ValueError("mutation changed the save schema signature")
        encode_verified(parser, level, world / "Level.sav", temporary, "level")
        if player is not None and old_path is not None and new_path is not None:
            encode_verified(parser, player, new_path, temporary, "player")
            old_path.unlink()
        if payload["action"] == "transfer_player":
            encode_verified(parser, target_player, transfer_target_path, temporary, "target-player")
        if payload["action"] == "delete_inactive_players":
            for uid in effects["deleted_uids"]:
                source = find_player_save(world / "Players", uid, required=True)
                source.unlink()
        verified, _ = decode(parser, world / "Level.sav", temporary, "final-level")
        observed = diagnose(verified, world / "Players")
        if payload["action"] == "rename_player":
            uid = normalize_guid(payload["uid"])
            matches = [player_name(raw) for _e, found, _i, raw in player_entries(verified) if found == uid]
            if matches != [payload["new_name"]]:
                raise ValueError("renamed player was not observed in the staged save")
        elif payload["action"] in {"migrate_player", "replace_player"}:
            target_field = "new_uid" if payload["action"] == "migrate_player" else "target_uid"
            old_uid = normalize_guid(payload["old_uid"]); new_uid = normalize_guid(payload[target_field])
            uids = [uid for _e, uid, _i, _raw in player_entries(verified)]
            if old_uid in uids or uids.count(new_uid) != 1 or not new_path.is_file() or old_path.exists():
                raise ValueError("migrated identity was not observed in the staged world")
        elif payload["action"] == "transfer_player":
            target_uid = normalize_guid(payload["target_uid"])
            target_instance = effects["target_instance_id"]
            rows = [row for row in player_entries(verified) if row[1] == target_uid]
            if len(rows) != 1 or rows[0][2] != target_instance:
                raise ValueError("transferred identity was not observed in the staged world")
            verified_player, _ = decode(
                parser, transfer_target_path, temporary, "final-target-player")
            if player_save_identity(verified_player) != (target_uid, target_instance):
                raise ValueError("transferred player save was not observed after encoding")
            player_documents[target_uid] = verified_player
            if any(graph_diagnostics(verified, player_documents).values()):
                raise ValueError("cross-world transfer left broken save graph records")
        elif payload["action"] in {"edit_inventory_slot", "edit_player_progression", "edit_owned_pal"}:
            probe = transform(payload, copy.deepcopy(verified), copy.deepcopy(player))
            if payload["action"] == "edit_inventory_slot":
                if (probe["before_item_id"] != probe["after_item_id"]
                        or probe["before_stack_count"] != probe["after_stack_count"]):
                    raise ValueError("inventory slot edit was not observed in the staged world")
            elif any(change["before"] != change["after"] for change in probe["changes"].values()):
                raise ValueError("offline character edit was not observed in the staged world")
        elif payload["action"] == "cleanup_duplicates":
            if diagnose(verified, world / "Players")["duplicate_player_uids"]:
                raise ValueError("duplicate player records remain after staged cleanup")
        elif payload["action"] == "delete_inactive_players":
            remaining_uids = {uid for _entry, uid, _instance, _raw in player_entries(verified)}
            if any(uid in remaining_uids for uid in effects["deleted_uids"]):
                raise ValueError("inactive player character remains after staged deletion")
            if any(find_player_save(world / "Players", uid, required=False) is not None
                   for uid in effects["deleted_uids"]):
                raise ValueError("inactive player save remains after staged deletion")
            if any(graph_diagnostics(verified, player_documents).values()):
                raise ValueError("inactive player deletion left broken save graph records")
        elif any(graph_diagnostics(verified, player_documents).values()):
            raise ValueError("broken or unreferenced save graph records remain after staged cleanup")
        return {"effects": effects, "diagnostics": observed, "schema": schema_signature(verified)}


def execute(payload: dict, world: pathlib.Path, install: pathlib.Path, state: pathlib.Path,
            parser: pathlib.Path, reviewed_sha: str, confirm: str, *, runner=subprocess.run,
            require_root=True, ready_attempts=24) -> dict:
    action = payload.get("action") if isinstance(payload, dict) else None
    if action not in SUPPORTED_ACTIONS or confirm != CONFIRMATIONS[action]:
        raise ValueError("exact action confirmation required")
    if require_root and os.geteuid() != 0:
        raise ValueError("offline save mutation must run as root")
    if not re.fullmatch(r"[0-9a-f]{64}", str(reviewed_sha or "")):
        raise ValueError("reviewed_sha256 must be a lowercase SHA-256")
    install = pathlib.Path(install).resolve(); state = pathlib.Path(state).resolve(strict=False)
    world = pathlib.Path(world).resolve(); parser = SAVE_INTELLIGENCE.ensure_parser(parser)
    build = game_build(install)
    if build is None:
        raise ValueError("installed Steam build could not be determined")
    here = pathlib.Path(__file__).resolve().parent
    state.mkdir(parents=True, exist_ok=True)
    with (state / "mutation.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        active = run_checked(["systemctl", "is-active", "--quiet", "palworld.service"],
                             runner=runner, allow_failure=True).returncode == 0
        if active and roster_count(here, runner):
            raise ValueError("online players must be zero before an offline save mutation")
        env = {**os.environ, "PALWORLD_LOCK_HELD": "true", "PALWORLD_BACKUP_ALREADY_SAVED": "true"}
        if active:
            run_checked([str(here / "rest-client.py"), "save"], runner=runner, timeout=30)
        reviewed = plan(payload, world, parser, build)
        if reviewed["reviewed_sha256"] != reviewed_sha:
            raise ValueError("world bytes, build, schema, parser, or mutation no longer match the reviewed plan")
        run_checked([str(here / "backup.sh"), "protected"], runner=runner, env=env)
        if active:
            run_checked(["systemctl", "stop", "palworld.service"], runner=runner, timeout=180)

        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        staging = world.parent / f".repair-{world.name}-{os.getpid()}"
        preserved = world.parent / f".palworldselfhost-before-repair-{world.name}-{stamp}-{os.getpid()}"
        installed = False
        try:
            shutil.copytree(world, staging, symlinks=False)
            restore_tree_ownership(world, staging)
            staged_files, _ = inventory(staging)
            original_files, _ = inventory(world)
            if staged_files != original_files:
                raise ValueError("staged world does not match the reviewed source bytes")
            if action == "transfer_player":
                mutation = apply_staged(payload, staging, parser, source_world(payload, world))
            else:
                mutation = apply_staged(payload, staging, parser)
            if mutation["schema"] != reviewed["schema"]:
                raise ValueError("staged mutation changed the reviewed schema signature")
            os.replace(world, preserved)
            os.replace(staging, world)
            installed = True
            if active:
                run_checked(["systemctl", "start", "palworld.service"], runner=runner, timeout=180)
                if not wait_ready(here, runner, ready_attempts):
                    raise ValueError("mutated world did not pass REST startup verification")
        except Exception:
            run_checked(["systemctl", "stop", "palworld.service"], runner=runner,
                        timeout=180, allow_failure=True)
            shutil.rmtree(staging, ignore_errors=True)
            if installed and world.exists():
                shutil.rmtree(world)
            if preserved.exists():
                os.replace(preserved, world)
            if active:
                run_checked(["systemctl", "start", "palworld.service"], runner=runner,
                            timeout=180, allow_failure=True)
            run_checked([str(here / "ops-event.py"), "audit", "save-repair", "failed",
                         "--details", action], runner=runner, allow_failure=True)
            raise
        final_preserved = state / "save-repair-preserved" / stamp / world.name
        final_preserved.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(preserved), str(final_preserved))
        result = {**reviewed, "status": "applied", "applied_at": stamp,
                  "preserved_world": str(final_preserved), "observed": mutation}
        temporary = state / "save-repair-last.json.tmp"
        temporary.write_text(json.dumps(result, indent=2) + "\n")
        os.chmod(temporary, 0o640); os.replace(temporary, state / "save-repair-last.json")
        run_checked([str(here / "ops-event.py"), "audit", "save-repair", "ok",
                     "--details", action], runner=runner, allow_failure=True)
        return result


def parse_payload(text: str) -> dict:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("payload must be valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("payload must be a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    diagnose_parser = sub.add_parser("diagnose")
    diagnose_parser.add_argument("--world", type=pathlib.Path, required=True)
    diagnose_parser.add_argument("--parser", type=pathlib.Path)
    for name in ("plan", "apply"):
        item = sub.add_parser(name)
        item.add_argument("payload")
        item.add_argument("--world", type=pathlib.Path, required=True)
        item.add_argument("--install-dir", type=pathlib.Path,
                          default=os.environ.get("PALWORLD_INSTALL_DIR"))
        item.add_argument("--state-dir", type=pathlib.Path,
                          default=os.environ.get("PALWORLD_STATE_DIR", "/var/lib/palworld"))
        item.add_argument("--parser", type=pathlib.Path)
        item.add_argument("--game-build")
        if name == "apply":
            item.add_argument("--reviewed-sha256", required=True)
            item.add_argument("--confirm", default="")
    args = parser.parse_args()
    try:
        codec = SAVE_INTELLIGENCE.ensure_parser(args.parser)
        if args.command == "diagnose":
            with tempfile.TemporaryDirectory(prefix="palworld-save-repair-diagnose-") as name:
                temporary = pathlib.Path(name)
                document, _ = decode(codec, args.world / "Level.sav", temporary, "level")
                player_documents = player_document_inventory(
                    args.world / "Players", codec, temporary, "diagnostic-player")
                diagnostics = diagnose(document, args.world / "Players")
                try:
                    diagnostics["save_graph"] = graph_diagnostics(document, player_documents)
                    diagnostics["save_graph_status"] = "available"
                except ValueError as exc:
                    diagnostics["save_graph_status"] = "unavailable"
                    diagnostics["save_graph_error"] = str(exc)
                result = {"status": "diagnosed", "world": args.world.name,
                          "game_build": game_build(pathlib.Path(os.environ.get("PALWORLD_INSTALL_DIR", "/srv/palworld/server"))),
                          "schema": schema_signature(document), "diagnostics": diagnostics}
        else:
            payload = parse_payload(args.payload)
            install = args.install_dir
            if install is None:
                raise ValueError("--install-dir or PALWORLD_INSTALL_DIR is required")
            build = args.game_build or game_build(install)
            if args.command == "plan":
                result = plan(payload, args.world, codec, build)
            else:
                result = execute(payload, args.world, install, args.state_dir, codec,
                                 args.reviewed_sha256, args.confirm)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
