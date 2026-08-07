#!/usr/bin/env python3
"""Read-only, build-aware Palworld save intelligence.

The Palworld save codec is deliberately kept outside this MIT project.  At
runtime we execute a SHA-256-pinned palsav-flex binary as a separate program,
then stream one top-level save record at a time from its JSON output.  No save
write operation is implemented here.
"""

import argparse
import contextlib
import hashlib
import json
import mmap
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request


PARSER_TAG = "palsav-tools-v1"
PARSER_UPSTREAM_COMMIT = "2c8c65c4a60b04e63eeb7f0c1857a5ba903a24d9"
PARSER_SHA256 = "f9a7e17782c233ba1be286ce3239447046024c76e2b5f5b375cb25f00743948e"
PARSER_URL = (
    "https://github.com/io-software-ai/palserver-gui/releases/download/"
    f"{PARSER_TAG}/palsav-linux-x64"
)
PAL_METADATA_COMMIT = "e46188978a13e74d84c9a1ce5569497ee0555cae"
PAL_METADATA_SHA256 = "a36d35ca1f5c6f4383182bd967f1defb808b472dcc63f441ea91c0b6a0353de6"
PAL_METADATA_URL = (
    "https://raw.githubusercontent.com/oMaN-Rod/palworld-save-pal/"
    f"{PAL_METADATA_COMMIT}/data/json/pals.json"
)
SECTIONS = (
    "CharacterSaveParameterMap", "GroupSaveDataMap", "ItemContainerSaveData",
    "CharacterContainerSaveData", "MapObjectSaveData", "DynamicItemSaveData",
    "BaseCampSaveData", "GuildExtraSaveDataMap",
)
INVENTORY_FIELDS = {
    "CommonContainerId": "common",
    "EssentialContainerId": "essential",
    "WeaponLoadOutContainerId": "weapons",
    "PlayerEquipArmorContainerId": "armor",
    "FoodEquipContainerId": "food",
}
MAX_RECORD_BYTES = 64 * 1024 * 1024
MAX_ROWS = 100_000


def env_path(name, default):
    return pathlib.Path(os.environ.get(name, default))


STATE = env_path("PALWORLD_STATE_DIR", "/var/lib/palworld")
INSTALL = env_path("PALWORLD_INSTALL_DIR", "/srv/palworld/server")
LIB = env_path("PALWORLD_LIB_DIR", "/usr/local/lib/palworld")
REPORT = STATE / "save-intelligence.json"
PARSER = STATE / "tools" / PARSER_TAG / "palsav-linux-x64"
PAL_METADATA = STATE / "tools" / "pal-metadata" / PAL_METADATA_COMMIT / "pals.json"


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, separators=(",", ":"), ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure_parser(explicit=None):
    override = os.environ.get("PALWORLD_SAVE_PARSER_PATH")
    candidate = pathlib.Path(explicit) if explicit else pathlib.Path(override or PARSER)
    if candidate.is_file() and sha256(candidate) == PARSER_SHA256:
        return candidate
    if explicit or override:
        raise RuntimeError("configured save parser is absent or does not match the pinned SHA-256")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    temporary = candidate.with_suffix(".part")
    with contextlib.suppress(FileNotFoundError):
        temporary.unlink()
    request = urllib.request.Request(PARSER_URL, headers={"User-Agent": "PalWorldSelfHost/save-intelligence"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, open(temporary, "wb") as output:
            shutil.copyfileobj(response, output, 1024 * 1024)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
        raise
    if sha256(temporary) != PARSER_SHA256:
        temporary.unlink()
        raise RuntimeError("downloaded save parser failed the pinned SHA-256 check")
    os.chmod(temporary, 0o750)
    os.replace(temporary, candidate)
    return candidate


def ensure_pal_metadata():
    """Return a hash-verified, commit-pinned species work-suitability table."""
    if PAL_METADATA.is_file() and sha256(PAL_METADATA) == PAL_METADATA_SHA256:
        candidate = PAL_METADATA
    else:
        PAL_METADATA.parent.mkdir(parents=True, exist_ok=True)
        temporary = PAL_METADATA.with_suffix(".part")
        temporary.unlink(missing_ok=True)
        request = urllib.request.Request(PAL_METADATA_URL, headers={"User-Agent": "PalWorldSelfHost/save-intelligence"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response, open(temporary, "wb") as output:
                shutil.copyfileobj(response, output, 1024 * 1024)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        if sha256(temporary) != PAL_METADATA_SHA256:
            temporary.unlink()
            raise RuntimeError("downloaded Pal metadata failed the pinned SHA-256 check")
        os.chmod(temporary, 0o640)
        os.replace(temporary, PAL_METADATA)
        candidate = PAL_METADATA
    parsed = json.loads(candidate.read_text())
    if not isinstance(parsed, dict):
        raise RuntimeError("pinned Pal metadata is not an object")
    labels = {
        "EmitFlame": "Kindling", "Watering": "Watering", "Seeding": "Planting",
        "GenerateElectricity": "Generating Electricity", "Handcraft": "Handiwork",
        "Collection": "Gathering", "Deforest": "Lumbering", "Mining": "Mining",
        "OilExtraction": "Oil Extraction", "ProductMedicine": "Medicine Production",
        "Cool": "Cooling", "Transport": "Transporting", "MonsterFarm": "Farming",
    }
    result = {}
    for character_id, row in parsed.items():
        suitability = row.get("work_suitability", {}) if isinstance(row, dict) and row.get("is_pal") is True else {}
        result[character_id.casefold()] = [
            {"id": key, "name": labels.get(key, key), "level": level}
            for key, level in suitability.items()
            if isinstance(level, int) and not isinstance(level, bool) and level > 0
        ]
    return result


def work_suitability(metadata, character_id):
    key = re.sub(r"^(?:BOSS_)+", "", str(character_id or ""), flags=re.IGNORECASE).casefold()
    return metadata.get(key, [])


def game_build():
    manifest = INSTALL / "steamapps" / "appmanifest_2394010.acf"
    try:
        match = re.search(r'"buildid"\s+"([0-9]+)"', manifest.read_text())
        return match.group(1) if match else None
    except OSError:
        return None


def discover_world():
    root = INSTALL / "Pal" / "Saved" / "SaveGames" / "0"
    worlds = [path.parent for path in root.glob("*/Level.sav") if path.parent.name != "backup"]
    if len(worlds) != 1:
        raise RuntimeError(f"expected one active world under {root}; found {len(worlds)}")
    return worlds[0]


def _find_array(mm, section):
    marker = json.dumps(section).encode()
    start = mm.find(marker)
    if start < 0:
        return None
    value = mm.find(b'"value":', start + len(marker))
    if value < 0:
        return None
    cursor = value + len(b'"value":')
    while cursor < len(mm) and mm[cursor] in b" \r\n\t":
        cursor += 1
    if cursor < len(mm) and mm[cursor] == ord("["):
        return cursor
    # Some Unreal array properties wrap their records in {"values":[...]}.
    if cursor < len(mm) and mm[cursor] == ord("{"):
        values = mm.find(b'"values":', cursor, min(len(mm), cursor + 4096))
        if values >= 0:
            cursor = values + len(b'"values":')
            while cursor < len(mm) and mm[cursor] in b" \r\n\t":
                cursor += 1
            if cursor < len(mm) and mm[cursor] == ord("["):
                return cursor
    return None


def _records(mm, array_start):
    cursor = array_start + 1
    while cursor < len(mm):
        while cursor < len(mm) and mm[cursor] in b" \r\n\t,":
            cursor += 1
        if cursor >= len(mm) or mm[cursor] == ord("]"):
            return
        record_start = cursor
        depth = 0
        quoted = False
        escaped = False
        while cursor < len(mm):
            byte = mm[cursor]
            if quoted:
                if escaped:
                    escaped = False
                elif byte == ord("\\"):
                    escaped = True
                elif byte == ord('"'):
                    quoted = False
            elif byte == ord('"'):
                quoted = True
            elif byte in (ord("{"), ord("[")):
                depth += 1
            elif byte in (ord("}"), ord("]")):
                if depth == 0:
                    break
                depth -= 1
                if depth == 0:
                    cursor += 1
                    break
            elif byte == ord(",") and depth == 0:
                break
            cursor += 1
            if cursor - record_start > MAX_RECORD_BYTES:
                raise RuntimeError("one save record exceeds the 64 MiB safety bound")
        raw = mm[record_start:cursor]
        if raw:
            yield json.loads(raw)


def iter_section(path, section):
    with open(path, "rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        start = _find_array(mm, section)
        if start is None:
            return
        yield from _records(mm, start)


def val(value, default=None):
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return default if value is None else value


def enum_number(prop):
    value = val(prop)
    return val(value) if isinstance(value, dict) else value


def guid(value):
    return str(value or "").replace("-", "").lower()


def id_prop(prop):
    value = val(prop, {})
    return val(value.get("ID")) if isinstance(value, dict) else None


def player_indexes(parser, players_dir, temporary):
    indexes = {"containers": {}, "inventory": {}, "paldeck": {}, "positions": {}}
    for source in sorted(players_dir.glob("*.sav"))[:5000] if players_dir.is_dir() else ():
        if not re.fullmatch(r"[0-9A-Fa-f]{32}\.sav", source.name):
            continue
        uid = source.stem.lower()
        copy = temporary / f"player-{source.name}"
        output = copy.with_suffix(copy.suffix + ".json")
        shutil.copy2(source, copy)
        convert(parser, copy, output)
        try:
            data = json.loads(output.read_text())
            save = data.get("properties", {}).get("SaveData", {}).get("value", {})
            for field, kind in (("OtomoCharacterContainerId", "party"), ("PalStorageContainerId", "palbox")):
                container = id_prop(save.get(field))
                if container:
                    indexes["containers"][guid(container)] = {"uid": uid, "kind": kind}
            inventory = val(save.get("InventoryInfo"), {})
            for field, kind in INVENTORY_FIELDS.items():
                container = id_prop(inventory.get(field)) if isinstance(inventory, dict) else None
                if container:
                    indexes["inventory"][guid(container)] = {"uid": uid, "kind": kind}
            record = val(save.get("RecordData"), {})
            species = set()
            for field in ("PaldeckUnlockFlag", "PalCaptureCount"):
                entries = val(record.get(field), []) if isinstance(record, dict) else []
                for entry in entries if isinstance(entries, list) else ():
                    key, present = entry.get("key"), val(entry.get("value"))
                    if key and (present is True or (isinstance(present, (int, float)) and present > 0)):
                        species.add(str(key))
            indexes["paldeck"][uid] = sorted(species, key=str.casefold)
            transform = val(save.get("LastTransform"), {})
            translation = val(transform.get("Translation"), {}) if isinstance(transform, dict) else {}
            if isinstance(translation, dict) and {"x", "y"} <= translation.keys():
                indexes["positions"][uid] = {
                    "x": translation["x"], "y": translation["y"], "z": translation.get("z", 0)
                }
        finally:
            copy.unlink(missing_ok=True)
            output.unlink(missing_ok=True)
    return indexes


def convert(parser, source, output):
    env = dict(os.environ, PYTHONHASHSEED="0")
    result = subprocess.run(
        [str(parser), "convert", str(source), "--to-json", "--minify-json", "-f", "-o", str(output)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, timeout=1800, env=env,
    )
    if result.returncode or not output.is_file():
        tail = result.stderr.strip()[-500:]
        raise RuntimeError(f"save conversion failed (exit {result.returncode}): {tail}")


def analyze(json_path, indexes, pal_metadata=None):
    pal_metadata = pal_metadata or {}
    players, pals, guilds, bases, map_objects = {}, [], [], [], []
    inventory = {}
    errors = []
    present = []

    def rows(section):
        try:
            found = False
            for count, entry in enumerate(iter_section(json_path, section), 1):
                found = True
                if count > MAX_ROWS:
                    raise RuntimeError(f"{section} exceeds the {MAX_ROWS} row safety bound")
                yield entry
            if found:
                present.append(section)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
            errors.append({"section": section, "error": str(exc)[:300]})

    for entry in rows("CharacterSaveParameterMap"):
        try:
            key = entry.get("key", {})
            raw = entry["value"]["RawData"]["value"]["object"]["SaveParameter"]["value"]
            uid = guid(val(key.get("PlayerUId")))
            instance = guid(val(key.get("InstanceId")))
            is_player = val(raw.get("IsPlayer"), False) is True
            name = val(raw.get("NickName"), "")
            level = enum_number(raw.get("Level"))
            location = val(raw.get("LastJumpedLocation"), {})
            if is_player:
                players[uid] = {
                    "uid": uid, "instance_id": instance, "name": name, "level": level,
                    "experience": val(raw.get("Exp")), "guild_id": None, "guild_name": None,
                    "last_online_ticks": None, "inventory": {kind: [] for kind in INVENTORY_FIELDS.values()},
                    "pals": [], "paldeck": indexes["paldeck"].get(uid, []),
                    "position": indexes["positions"].get(uid) or (location if isinstance(location, dict) else None),
                }
            else:
                character_id = val(raw.get("CharacterID"))
                if not character_id:
                    continue
                owner = guid(val(raw.get("OwnerPlayerUId")))
                slot = val(raw.get("SlotId"), {})
                container = id_prop(slot.get("ContainerId")) if isinstance(slot, dict) else None
                container_owner = indexes["containers"].get(guid(container), {})
                pals.append({
                    "instance_id": instance, "owner_uid": owner or container_owner.get("uid"),
                    "location": container_owner.get("kind", "unknown"), "character_id": character_id,
                    "_container_id": guid(container),
                    "name": name, "level": level, "rank": enum_number(raw.get("Rank")),
                    "gender": enum_number(raw.get("Gender")), "lucky": val(raw.get("IsRarePal"), False),
                    "talents": {
                        "hp": enum_number(raw.get("Talent_HP")), "attack": enum_number(raw.get("Talent_Shot")),
                        "defense": enum_number(raw.get("Talent_Defense")),
                    },
                    "passives": list(val(raw.get("PassiveSkillList"), {}).get("values", []))
                    if isinstance(val(raw.get("PassiveSkillList"), {}), dict) else [],
                    "work_suitability": work_suitability(pal_metadata, character_id),
                })
        except (KeyError, TypeError, ValueError) as exc:
            errors.append({"section": "CharacterSaveParameterMap", "error": f"record: {exc}"})

    for entry in rows("GroupSaveDataMap"):
        try:
            raw = entry["value"]["RawData"]["value"]
            if raw.get("group_type") != "EPalGroupType::Guild":
                continue
            group_id = guid(raw.get("group_id") or entry.get("key"))
            members = []
            for member in raw.get("players", []):
                uid = guid(member.get("player_uid"))
                members.append({"uid": uid, "name": member.get("player_name", ""),
                                "last_online_ticks": member.get("last_online_real_time")})
                profile = players.setdefault(uid, {"uid": uid, "name": member.get("player_name", ""),
                    "level": None, "inventory": {}, "pals": [], "paldeck": indexes["paldeck"].get(uid, [])})
                profile.update({"guild_id": group_id, "guild_name": raw.get("guild_name", ""),
                                "last_online_ticks": member.get("last_online_real_time")})
            guilds.append({
                "id": group_id, "name": raw.get("guild_name", ""),
                "admin_uid": guid(raw.get("admin_player_uid")), "level": raw.get("base_camp_level"),
                "base_ids": [guid(item) for item in raw.get("base_ids", [])], "members": members,
            })
        except (KeyError, TypeError, ValueError) as exc:
            errors.append({"section": "GroupSaveDataMap", "error": f"record: {exc}"})

    for entry in rows("BaseCampSaveData"):
        try:
            value = entry["value"]
            raw = value["RawData"]["value"]
            transform = raw.get("transform", {}).get("translation", {})
            worker = val(value.get("WorkerDirector"), {})
            worker_raw = val(worker.get("RawData"), {}) if isinstance(worker, dict) else {}
            bases.append({"id": guid(raw.get("id") or entry.get("key")), "name": raw.get("name", ""),
                          "guild_id": guid(raw.get("group_id_belong_to")), "state": raw.get("state"),
                          "_worker_container_id": guid(worker_raw.get("container_id")) if isinstance(worker_raw, dict) else "",
                          "position": {axis: transform.get(axis, 0) for axis in ("x", "y", "z")}})
        except (KeyError, TypeError, ValueError) as exc:
            errors.append({"section": "BaseCampSaveData", "error": f"record: {exc}"})

    for entry in rows("ItemContainerSaveData"):
        try:
            container = guid(val(entry.get("key", {}).get("ID")))
            owner = indexes["inventory"].get(container)
            if not owner:
                continue
            items = []
            slots = val(entry.get("value", {}).get("Slots"), {}).get("values", [])
            for slot in slots:
                raw = slot.get("RawData", {}).get("value", {})
                item = raw.get("item", {}).get("static_id")
                if item and item != "None":
                    items.append({"item_id": item, "count": raw.get("count", 0)})
            inventory.setdefault(owner["uid"], {})[owner["kind"]] = items
        except (KeyError, TypeError, ValueError) as exc:
            errors.append({"section": "ItemContainerSaveData", "error": f"record: {exc}"})

    for entry in rows("MapObjectSaveData"):
        try:
            raw = entry.get("Model", {}).get("value", {}).get("RawData", {}).get("value", {})
            transform = raw.get("initital_transform_cache", {}).get("translation", {})
            if {"x", "y"} <= transform.keys():
                map_objects.append({"type": val(entry.get("MapObjectId")),
                                    "base_id": guid(raw.get("base_camp_id_belong_to")),
                                    "position": {axis: transform.get(axis, 0) for axis in ("x", "y", "z")}})
        except (KeyError, TypeError, ValueError) as exc:
            errors.append({"section": "MapObjectSaveData", "error": f"record: {exc}"})

    worker_containers = {base["_worker_container_id"]: base for base in bases if base["_worker_container_id"]}
    for pal in pals:
        base = worker_containers.get(pal.pop("_container_id", ""))
        if base:
            pal["base_id"] = base["id"]
            pal["location"] = "base"
            base["worker_count"] = base.get("worker_count", 0) + 1
        if pal["owner_uid"] in players:
            players[pal["owner_uid"]].setdefault("pals", []).append(pal)
    for base in bases:
        base.pop("_worker_container_id", None)
        base.setdefault("worker_count", 0)
    for uid, lists in inventory.items():
        if uid in players:
            players[uid].setdefault("inventory", {}).update(lists)

    required = {"CharacterSaveParameterMap", "GroupSaveDataMap", "BaseCampSaveData"}
    missing = sorted(required - set(present))
    status = "compatible" if not errors and not missing else "degraded"
    return {
        "status": status, "schema": {"present_sections": sorted(set(present)), "missing_required": missing,
                                      "errors": errors[:100]},
        "counts": {"players": len(players), "pals": len(pals), "guilds": len(guilds),
                   "bases": len(bases), "base_workers": sum(base["worker_count"] for base in bases),
                   "map_objects": len(map_objects)},
        "players": sorted(players.values(), key=lambda row: str(row.get("name", "")).casefold()),
        "guilds": sorted(guilds, key=lambda row: row["name"].casefold()),
        "bases": bases, "map_objects": map_objects,
    }


def scan(args):
    started = time.time()
    phase = "discover"
    world = None
    level = None
    try:
        world = pathlib.Path(args.world) if args.world else discover_world()
        level = pathlib.Path(args.level) if args.level else world / "Level.sav"
        phase = "parser"
        parser = ensure_parser(args.parser)
        phase = "pal_metadata"
        pal_metadata = ensure_pal_metadata()
        if not args.level and (LIB / "rest-client.py").is_file():
            subprocess.run([str(LIB / "rest-client.py"), "save"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30, check=False)
        phase = "convert"
        with tempfile.TemporaryDirectory(prefix="palworld-save-intelligence-") as directory:
            temporary = pathlib.Path(directory)
            save_copy = temporary / "Level.sav"
            json_path = temporary / "Level.sav.json"
            shutil.copy2(level, save_copy)
            indexes = player_indexes(parser, world / "Players", temporary)
            convert(parser, save_copy, json_path)
            phase = "analyze"
            result = analyze(json_path, indexes, pal_metadata)
        result.update({
            "world": world.name,
            "level_sav": {"bytes": level.stat().st_size, "mtime": int(level.stat().st_mtime),
                          "sha256": sha256(level)},
            "pal_metadata": {"source": "oMaN-Rod/palworld-save-pal", "commit": PAL_METADATA_COMMIT,
                             "sha256": PAL_METADATA_SHA256, "species": len(pal_metadata)},
        })
    except Exception as exc:
        result = {
            "status": "error", "world": world.name if world else None,
            "level_sav": ({"bytes": level.stat().st_size, "mtime": int(level.stat().st_mtime)}
                          if level and level.is_file() else None),
            "schema": {"present_sections": [], "missing_required": [],
                       "errors": [{"phase": phase, "error": str(exc)[:500]}]},
            "counts": {}, "players": [], "guilds": [], "bases": [], "map_objects": [],
        }
    result.update({
        "generated_at": int(time.time()), "duration_seconds": round(time.time() - started, 3),
        "game_build": args.game_build or game_build(),
        "parser": {"tag": PARSER_TAG, "sha256": PARSER_SHA256,
                   "upstream_commit": PARSER_UPSTREAM_COMMIT, "mode": "external-read-only"},
    })
    atomic_json(pathlib.Path(args.output) if args.output else REPORT, result)
    print(json.dumps(result if args.full else {key: result.get(key) for key in (
        "status", "generated_at", "duration_seconds", "world", "game_build", "level_sav", "parser", "schema", "counts"
    )}, indent=2))
    return 0 if result["status"] == "compatible" else (2 if result["status"] == "degraded" else 1)


def status(args):
    path = pathlib.Path(args.output) if args.output else REPORT
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        data = {"status": "never_scanned", "counts": {}, "schema": {}}
    if not args.full:
        data = {key: data.get(key) for key in (
            "status", "generated_at", "duration_seconds", "world", "game_build", "level_sav", "parser", "schema", "counts"
        )}
    print(json.dumps(data, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    scan_parser = sub.add_parser("scan", help="copy, decode and inspect an active save read-only")
    scan_parser.add_argument("--world")
    scan_parser.add_argument("--level")
    scan_parser.add_argument("--parser")
    scan_parser.add_argument("--game-build")
    scan_parser.add_argument("--output")
    scan_parser.add_argument("--full", action="store_true")
    scan_parser.set_defaults(function=scan)
    status_parser = sub.add_parser("status", help="show the last scan")
    status_parser.add_argument("--output")
    status_parser.add_argument("--full", action="store_true")
    status_parser.set_defaults(function=status)
    args = parser.parse_args()
    return args.function(args)


if __name__ == "__main__":
    raise SystemExit(main())
