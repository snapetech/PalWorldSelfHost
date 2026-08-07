#!/usr/bin/env python3
"""Typed and guarded-raw Palworld settings transactions."""

import argparse
import difflib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import time


HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("ops_lib", HERE / "ops-lib.py")
ops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops)

INSTALL = pathlib.Path(os.environ["PALWORLD_INSTALL_DIR"])
TEMPLATE = INSTALL / "DefaultPalWorldSettings.ini"
CONFIG_PLATFORM = "WindowsServer" if os.environ.get("PALWORLD_RUNTIME") == "wine-windows" else "LinuxServer"
ACTIVE = INSTALL / "Pal/Saved/Config" / CONFIG_PLATFORM / "PalWorldSettings.ini"
OVERRIDES = ops.STATE / "settings-overrides.json"
RAW = ops.STATE / "settings-raw.ini"
PENDING = ops.STATE / "settings-restart-required.json"
MAX_RAW_BYTES = 48 * 1024
PROTECTED = {
    "AdminPassword", "RESTAPIEnabled", "RESTAPIPort", "RCONEnabled", "RCONPort",
    "PublicPort", "PublicIP", "ServerName", "ServerDescription", "ServerPassword",
}
SECRETS = {"AdminPassword", "ServerPassword"}

DESCRIPTIONS = {
    "Difficulty": "Built-in world difficulty mode.",
    "DayTimeSpeedRate": "Multiplier for daytime progression.",
    "NightTimeSpeedRate": "Multiplier for nighttime progression.",
    "ExpRate": "Experience gained by players and Pals.",
    "PalCaptureRate": "Base Pal capture probability multiplier.",
    "PalSpawnNumRate": "Pal population multiplier; higher values increase host load.",
    "PalDamageRateAttack": "Damage dealt by Pals.",
    "PalDamageRateDefense": "Damage received by Pals.",
    "PlayerDamageRateAttack": "Damage dealt by players.",
    "PlayerDamageRateDefense": "Damage received by players.",
    "CollectionDropRate": "Resources obtained from gathering objects.",
    "CollectionObjectRespawnSpeedRate": "Gathering-object respawn speed.",
    "EnemyDropItemRate": "Items dropped by defeated enemies.",
    "DeathPenalty": "Inventory/equipment loss applied when a player dies.",
    "PalEggDefaultHatchingTime": "Base incubation time for a large egg, in hours.",
    "ServerPlayerMaxNum": "Maximum simultaneous players accepted by the server.",
    "CoopPlayerMaxNum": "Maximum players in a cooperative group.",
    "BaseCampWorkerMaxNum": "Maximum Pals assigned to one base.",
    "bIsPvP": "Enables player-versus-player rules supported by this game build.",
    "bEnableFriendlyFire": "Allows friendly player damage.",
    "bEnableInvaderEnemy": "Allows enemy raid events against bases.",
    "bExistPlayerAfterLogout": "Keeps player bodies present after logout.",
    "AutoSaveSpan": "Seconds between game-managed automatic world saves; longer intervals reduce save stalls but increase rollback exposure.",
    "ChatPostLimitPerMinute": "Maximum chat messages one player may post per minute.",
    "LogFormatType": "Selects plain-text or structured JSON dedicated-server logs.",
    "bIsUseBackupSaveData": "Enables the game's own backup-save copies; this adds storage I/O and is separate from toolkit backups.",
    "CrossplayPlatforms": "Platform identifiers allowed to join this world.",
    "BanListURL": "Remote ban-list document fetched by the dedicated server.",
    "bUseAuth": "Requires platform authentication for connecting players.",
    "bAllowClientMod": "Allows clients using supported mods to connect.",
    "bEnableVoiceChat": "Enables in-game voice chat.",
    "VoiceChatMaxVolumeDistance": "Distance in Unreal units at which voice playback begins attenuating.",
    "VoiceChatZeroVolumeDistance": "Distance in Unreal units at which voice playback becomes silent.",
    "MaxGuildsPerFrame": "Maximum guild records processed per frame; higher values improve immediacy at greater CPU cost.",
    "PlayerDataPalStorageUpdateCheckTickInterval": "Seconds between checks for player Pal-storage updates.",
    "bPalLost": "Enables Pal loss under game modes that support it.",
    "bAllowGlobalPalboxExport": "Allows Pals to be exported through the Global Palbox.",
    "bAllowGlobalPalboxImport": "Allows Pals to be imported through the Global Palbox.",
    "EnablePredatorBossPal": "Enables predator boss Pals in the world.",
    "AdditionalDropItemWhenPlayerKillingInPvPMode": "Selects the item set dropped for a PvP player kill.",
    "AdditionalDropItemNumWhenPlayerKillingInPvPMode": "Number of additional items dropped for a PvP player kill.",
    "BlockRespawnTime": "Seconds a player must wait before respawning.",
    "RespawnPenaltyDurationThreshold": "Death duration threshold in seconds before the respawn penalty applies.",
    "RespawnPenaltyTimeScale": "Multiplier applied to the respawn penalty duration.",
    "GuildRejoinCooldownMinutes": "Minutes a player must wait before rejoining a guild.",
    "ServerReplicatePawnCullDistance": "Maximum pawn replication distance in Unreal units; lower values reduce network and CPU load.",
    "BuildingNameDisplayCacheTTLSeconds": "Seconds building-owner names remain cached.",
    "DropItemMaxNum": "Maximum ordinary dropped items retained in the world; lower limits reduce host load.",
    "DropItemAliveMaxHours": "Hours ordinary dropped items remain in the world before cleanup.",
    "PhysicsActiveDropItemMaxNum": "Maximum dropped items receiving physics simulation; -1 means unlimited.",
    "DenyTechnologyList": "Comma-delimited technology identifiers players may not unlock.",
    "RandomizerType": "Selects whether Pal randomization applies by region or across the whole world.",
    "RandomizerSeed": "Seed used when creating a randomized world; blank selects a random seed.",
    "bIsRandomizerPalLevelRandom": "Randomizes Pal levels when world randomization is enabled.",
}

CATEGORY_KEYS = {
    "Server and network": {
        "ServerName", "ServerDescription", "ServerPassword", "AdminPassword", "ServerPlayerMaxNum",
        "CoopPlayerMaxNum", "PublicIP", "PublicPort", "bIsMultiplay", "bShowPlayerList",
        "bIsShowJoinLeftMessage", "RESTAPIEnabled", "RESTAPIPort", "RCONEnabled", "RCONPort",
        "ChatPostLimitPerMinute", "LogFormatType", "bIsUseBackupSaveData", "AutoSaveSpan", "Region",
        "CrossplayPlatforms", "BanListURL", "bUseAuth", "bAllowClientMod", "bEnableVoiceChat",
        "VoiceChatMaxVolumeDistance", "VoiceChatZeroVolumeDistance",
        "AutoTransferMasterCheckIntervalSeconds", "AutoTransferMasterThresholdDays",
        "ItemContainerForceMarkDirtyInterval", "MaxGuildsPerFrame", "PlayerDataPalStorageUpdateCheckTickInterval",
    },
    "Pals and breeding": {
        "PalCaptureRate", "PalSpawnNumRate", "PalDamageRateAttack", "PalDamageRateDefense",
        "PalStomachDecreaceRate", "PalStaminaDecreaceRate", "PalAutoHPRegeneRate",
        "PalAutoHpRegeneRateInSleep", "PalEggDefaultHatchingTime", "WorkSpeedRate", "bPalLost",
        "bAllowGlobalPalboxExport", "bAllowGlobalPalboxImport", "EnablePredatorBossPal",
        "MonsterFarmActionSpeedRate",
    },
    "Players and guilds": {
        "PlayerDamageRateAttack", "PlayerDamageRateDefense", "PlayerStomachDecreaceRate",
        "PlayerStaminaDecreaceRate", "PlayerAutoHPRegeneRate", "PlayerAutoHpRegeneRateInSleep",
        "ItemWeightRate", "EquipmentDurabilityDamageRate", "bEnablePlayerToPlayerDamage",
        "bEnableFriendlyFire", "bIsPvP", "DeathPenalty", "bEnableFastTravel",
        "bIsStartLocationSelectByMap", "bExistPlayerAfterLogout", "bEnableNonLoginPenalty",
        "bAllowEnhanceStat_Health", "bAllowEnhanceStat_Attack", "bAllowEnhanceStat_Stamina",
        "bAllowEnhanceStat_Weight", "bAllowEnhanceStat_WorkSpeed",
        "AdditionalDropItemWhenPlayerKillingInPvPMode", "AdditionalDropItemNumWhenPlayerKillingInPvPMode",
        "bAdditionalDropItemWhenPlayerKillingInPvPMode", "bDisplayPvPItemNumOnWorldMap_Player",
        "BlockRespawnTime", "RespawnPenaltyDurationThreshold", "RespawnPenaltyTimeScale",
        "GuildPlayerMaxNum", "BaseCampMaxNum", "BaseCampMaxNumInGuild", "BaseCampWorkerMaxNum",
        "bAutoResetGuildNoOnlinePlayers", "AutoResetGuildTimeNoOnlinePlayers",
        "bEnableDefenseOtherGuildPlayer", "bCanPickupOtherGuildDeathPenaltyDrop",
        "bInvisibleOtherGuildBaseCampAreaFX", "GuildRejoinCooldownMinutes",
        "bDisplayPvPItemNumOnWorldMap_BaseCamp",
    },
    "Bases and resources": {
        "BuildObjectDamageRate", "BuildObjectDeteriorationDamageRate", "bBuildAreaLimit",
        "MaxBuildingLimitNum", "ServerReplicatePawnCullDistance", "BuildObjectHpRate",
        "bEnableBuildingPlayerUIdDisplay", "BuildingNameDisplayCacheTTLSeconds", "DropItemMaxNum",
        "DropItemAliveMaxHours", "CollectionDropRate", "EnemyDropItemRate", "ItemCorruptionMultiplier",
        "SupplyDropSpan", "DropItemMaxNum_UNKO", "bActiveUNKO", "PhysicsActiveDropItemMaxNum",
        "DenyTechnologyList", "CollectionObjectHpRate", "CollectionObjectRespawnSpeedRate",
    },
    "Difficulty and rates": {"ExpRate", "Difficulty", "DayTimeSpeedRate", "NightTimeSpeedRate"},
}

CHOICES = {
    "Difficulty": ["None", "Casual", "Normal", "Hard"],
    "DeathPenalty": ["None", "Item", "ItemAndEquipment", "All"],
    "LogFormatType": ["Text", "Json"],
    "RandomizerType": ["None", "Region", "All"],
    "AdditionalDropItemWhenPlayerKillingInPvPMode": ["None", "PlayerDropItem", "AllItems"],
}

RANGES = {
    "ServerPlayerMaxNum": (1, 99, 1), "CoopPlayerMaxNum": (1, 8, 1),
    "PublicPort": (1024, 65535, 1), "RESTAPIPort": (1024, 65535, 1), "RCONPort": (1024, 65535, 1),
    "ChatPostLimitPerMinute": (1, 120, 1), "AutoSaveSpan": (10, 600, 5),
    "VoiceChatMaxVolumeDistance": (0, 50000, 100), "VoiceChatZeroVolumeDistance": (0, 50000, 100),
    "AutoTransferMasterCheckIntervalSeconds": (60, 86400, 1), "AutoTransferMasterThresholdDays": (0, 365, 1),
    "ItemContainerForceMarkDirtyInterval": (0, 60, .1), "MaxGuildsPerFrame": (1, 100, 1),
    "PlayerDataPalStorageUpdateCheckTickInterval": (0, 60, .1), "PalEggDefaultHatchingTime": (0, 240, 1),
    "AdditionalDropItemNumWhenPlayerKillingInPvPMode": (0, 9999, 1), "BlockRespawnTime": (0, 3600, .5),
    "RespawnPenaltyDurationThreshold": (0, 3600, .5), "RespawnPenaltyTimeScale": (0, 10, .1),
    "GuildPlayerMaxNum": (1, 100, 1), "BaseCampMaxNum": (1, 1024, 1),
    "BaseCampMaxNumInGuild": (1, 10, 1), "BaseCampWorkerMaxNum": (1, 50, 1),
    "AutoResetGuildTimeNoOnlinePlayers": (1, 168, 1), "GuildRejoinCooldownMinutes": (0, 10080, 1),
    "MaxBuildingLimitNum": (0, 10000, 1), "ServerReplicatePawnCullDistance": (5000, 15000, 1),
    "BuildingNameDisplayCacheTTLSeconds": (0, 600, 1), "DropItemMaxNum": (0, 5000, 1),
    "DropItemAliveMaxHours": (0, 24, .5), "SupplyDropSpan": (30, 1440, 1),
    "DropItemMaxNum_UNKO": (0, 5000, 1), "PhysicsActiveDropItemMaxNum": (-1, 10000, 1),
    "BuildObjectDeteriorationDamageRate": (0, 20, .1),
    "PalCaptureRate": (.5, 20, .1), "PalSpawnNumRate": (.5, 20, .1),
    "CollectionDropRate": (.5, 20, .1), "EnemyDropItemRate": (.5, 20, .1),
    "CollectionObjectHpRate": (.5, 20, .1), "CollectionObjectRespawnSpeedRate": (.5, 20, .1),
}

MAX_LENGTHS = {
    "ServerName": 64, "ServerDescription": 256, "ServerPassword": 64, "AdminPassword": 64,
    "PublicIP": 64, "Region": 64, "CrossplayPlatforms": 256, "BanListURL": 512,
    "DenyTechnologyList": 512, "RandomizerSeed": 32,
}


def option_body(text):
    if text.count("OptionSettings=(") != 1:
        raise ValueError("configuration must contain exactly one OptionSettings=(...) block")
    return text.split("OptionSettings=(", 1)[1].rsplit(")", 1)[0]


def parsed_values(text):
    body = option_body(text)
    result = {}
    for match in re.finditer(r'(?:^|,)([A-Za-z][A-Za-z0-9_]*)=("[^"]*"|[^,]*)', body):
        key, raw = match.group(1), match.group(2).strip()
        if raw in {"True", "False"}:
            value, kind = raw == "True", "boolean"
        elif re.fullmatch(r"-?\d+", raw):
            value, kind = int(raw), "integer"
        elif re.fullmatch(r"-?(?:\d+\.?\d*|\.\d+)", raw):
            value, kind = float(raw), "number"
        else:
            value, kind = raw.strip('"'), "string"
        result[key] = {"type": kind, "value": value, "raw": raw}
    if not result:
        raise ValueError("OptionSettings block contains no readable settings")
    return result


def label_for(key):
    label = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", key).replace("_", " ")
    if label.startswith("b "):
        label = label[2:]
    return label


def category_for(key):
    for category, keys in CATEGORY_KEYS.items():
        if key in keys:
            return category
    lowered = key.lower()
    if any(word in lowered for word in ("damage", "death", "difficulty", "capture", "exp", "spawn", "drop")):
        return "Difficulty and rates"
    if any(word in lowered for word in ("pal", "egg")):
        return "Pals and breeding"
    if any(word in lowered for word in ("player", "pvp", "guild", "coop")):
        return "Players and guilds"
    if any(word in lowered for word in ("base", "build", "collection", "object")):
        return "Bases and resources"
    if any(word in lowered for word in ("server", "admin", "password", "port", "rcon", "restapi", "public")):
        return "Server and network"
    return "World rules"


def description_for(key, kind):
    if key in DESCRIPTIONS:
        return DESCRIPTIONS[key]
    label = label_for(key).lower()
    if key.endswith(("Rate", "SpeedRate")):
        return f"Multiplier applied to {label}; 1.0 preserves the installed game's default behavior."
    if key.endswith("IntervalSeconds"):
        return f"Seconds between {label.removesuffix(' interval seconds')} operations."
    if key.endswith("CooldownMinutes"):
        return f"Required cooldown in minutes for {label.removesuffix(' cooldown minutes')}."
    if key.endswith("MaxHours") or key.endswith("TimeNoOnlinePlayers"):
        return f"Maximum duration in hours for {label}."
    if "MaxNum" in key or key.startswith("Max"):
        return f"Maximum allowed or retained count for {label}."
    if key.startswith("bAllow"):
        return f"Controls whether the server allows {label.removeprefix('allow ')}."
    if key.startswith("bEnable") or key.startswith("Enable"):
        return f"Controls whether the world enables {label.removeprefix('enable ')}."
    if key.startswith("b") and kind == "boolean":
        return f"Toggles the {label} world rule."
    if kind == "boolean":
        return f"Enables or disables {label}."
    if kind == "integer":
        return f"Whole-number limit or interval used for {label}."
    if kind == "number":
        return f"Numeric world value used for {label}."
    return f"Text value supplied to the dedicated server for {label}."


def guidance_for(key, kind):
    guidance = {
        "label": label_for(key),
        "category": category_for(key),
        "description": description_for(key, kind),
    }
    if key in CHOICES:
        guidance["choices"] = CHOICES[key]
    if key in RANGES:
        minimum, maximum, step = RANGES[key]
        guidance.update({"recommended_min": minimum, "recommended_max": maximum, "step": step})
    elif kind in {"integer", "number"} and key.endswith(("Rate", "SpeedRate")):
        guidance.update({"recommended_min": 0.1, "recommended_max": 20, "step": 0.1})
    elif kind == "integer" and "MaxNum" in key:
        guidance.update({"recommended_min": 0, "recommended_max": 10000, "step": 1})
    if key in MAX_LENGTHS:
        guidance["max_length"] = MAX_LENGTHS[key]
    units = {
        "AutoSaveSpan": "seconds", "AutoTransferMasterCheckIntervalSeconds": "seconds",
        "ItemContainerForceMarkDirtyInterval": "seconds", "PlayerDataPalStorageUpdateCheckTickInterval": "seconds",
        "PalEggDefaultHatchingTime": "hours", "BlockRespawnTime": "seconds",
        "RespawnPenaltyDurationThreshold": "seconds", "AutoResetGuildTimeNoOnlinePlayers": "hours",
        "GuildRejoinCooldownMinutes": "minutes", "BuildingNameDisplayCacheTTLSeconds": "seconds",
        "DropItemAliveMaxHours": "hours", "SupplyDropSpan": "minutes",
    }
    if key in units:
        guidance["unit"] = units[key]
    return guidance


def schema():
    values = parsed_values(TEMPLATE.read_text())
    result = {}
    for key, item in values.items():
        result[key] = {
            "type": item["type"], "default": "<managed>" if key in PROTECTED else item["value"],
            "writable": key not in PROTECTED,
            "restart_required": True, **guidance_for(key, item["type"]),
        }
    return result


def raw_source():
    for source in (RAW, ACTIVE, TEMPLATE):
        if source.is_file():
            return source.read_text()
    raise ValueError("no Palworld settings source exists")


def replace_raw(text, key, raw_value, required=False):
    pattern = re.compile(rf"(?P<prefix>(?:^|[,(])\s*{re.escape(key)}=)(?P<value>\"[^\"]*\"|[^,)]*)")
    rendered, count = pattern.subn(lambda match: match.group("prefix") + raw_value, text, count=1)
    if required and count != 1:
        raise ValueError(f"protected setting {key} cannot be removed in raw mode")
    return rendered


def redact_raw(text):
    for key in SECRETS:
        text = replace_raw(text, key, '"__PALWORLD_MANAGED_SECRET__"')
    return text


def validate_and_merge_raw(candidate):
    if not isinstance(candidate, str):
        raise ValueError("raw configuration must be text")
    encoded = candidate.encode()
    if len(encoded) > MAX_RAW_BYTES:
        raise ValueError(f"raw configuration exceeds {MAX_RAW_BYTES} bytes")
    if "\0" in candidate:
        raise ValueError("raw configuration contains a NUL byte")
    candidate = candidate.replace("\r\n", "\n").replace("\r", "\n")
    parsed_values(candidate)
    current = raw_source()
    current_values = parsed_values(current)
    for key in PROTECTED:
        if key in current_values:
            candidate = replace_raw(candidate, key, current_values[key]["raw"], required=True)
    parsed_values(candidate)
    return candidate if candidate.endswith("\n") else candidate + "\n"


def raw_plan(candidate):
    merged = validate_and_merge_raw(candidate)
    before = redact_raw(raw_source()).splitlines()
    after = redact_raw(merged).splitlines()
    diff = "\n".join(difflib.unified_diff(before, after, "current.ini", "proposed.ini", lineterm=""))
    return {
        "changed": before != after,
        "restart_required": before != after,
        "bytes": len(merged.encode()),
        "protected_fields": sorted(PROTECTED),
        "diff": diff,
    }, merged


def mark_pending(source, changed):
    ops.atomic_json(PENDING, {
        "applied_at": int(time.time()), "source": source, "changed": sorted(changed),
    })


def render_or_restore(path, previous, existed):
    try:
        subprocess.run([str(HERE / "start.sh"), "--render-only"], check=True)
    except (OSError, subprocess.CalledProcessError):
        if existed:
            ops.atomic_text(path, previous)
        else:
            path.unlink(missing_ok=True)
        subprocess.run([str(HERE / "start.sh"), "--render-only"], check=False)
        raise


def typed_presets(known):
    recipes = {
        "Casual": {
            "ExpRate": 2.0, "PalCaptureRate": 1.5, "CollectionDropRate": 2.0,
            "EnemyDropItemRate": 2.0, "PalEggDefaultHatchingTime": 0.5, "DeathPenalty": "None",
        },
        "Hard": {
            "ExpRate": 0.5, "PalCaptureRate": 0.8, "CollectionDropRate": 0.8,
            "EnemyDropItemRate": 0.8, "PalDamageRateAttack": 1.2, "DeathPenalty": "All",
        },
        "Vanilla defaults": {key: item["default"] for key, item in known.items() if item["writable"]},
    }
    descriptions = {
        "Casual": "Faster progression and gathering with no death loss.",
        "Hard": "Slower progression, stronger Pals and full death loss.",
        "Vanilla defaults": "Reset every writable value to this installed build's shipped default.",
    }
    return {
        name: {"description": descriptions[name], "values": {
            key: value for key, value in values.items() if key in known and known[key]["writable"]
        }} for name, values in recipes.items()
    }


def schema_payload(known, overrides):
    effective = {}
    if ACTIVE.is_file():
        try:
            effective = {key: item["value"] for key, item in parsed_values(ACTIVE.read_text()).items()}
        except ValueError:
            effective = {}
    for key in PROTECTED:
        if key in effective:
            effective[key] = "<managed>"
    return {
        "settings": known, "overrides": overrides, "effective": effective,
        "pending_restart": ops.read_json(PENDING, {}), "presets": typed_presets(known),
    }


def apply_typed(updates, known, current):
    result = {**current, **updates}
    existed = OVERRIDES.exists()
    previous = OVERRIDES.read_text() if existed else ""
    backup = ops.STATE / f"settings-overrides-{time.time_ns()}.json"
    ops.atomic_text(backup, previous if existed else "{}\n")
    ops.atomic_json(OVERRIDES, result)
    try:
        render_or_restore(OVERRIDES, previous, existed)
    except (OSError, subprocess.CalledProcessError):
        ops.audit("settings.apply", "failed", updates=sorted(updates), backup=str(backup))
        raise
    mark_pending("typed", updates)
    ops.audit("settings.apply", "ok", updates=sorted(updates), backup=str(backup))
    return backup


def replace_typed(values, source):
    existed = OVERRIDES.exists()
    previous = OVERRIDES.read_text() if existed else ""
    backup = ops.STATE / f"settings-overrides-{time.time_ns()}.json"
    ops.atomic_text(backup, previous if existed else "{}\n")
    ops.atomic_json(OVERRIDES, values)
    try:
        render_or_restore(OVERRIDES, previous, existed)
    except (OSError, subprocess.CalledProcessError):
        ops.audit("settings.replace", "failed", source=source, backup=str(backup))
        raise
    mark_pending(source, values)
    ops.audit("settings.replace", "ok", source=source, backup=str(backup))
    return backup


parser = argparse.ArgumentParser()
sub = parser.add_subparsers(dest="command", required=True)
sub.add_parser("schema")
plan_parser = sub.add_parser("plan")
plan_parser.add_argument("updates")
apply_parser = sub.add_parser("apply")
apply_parser.add_argument("updates")
apply_parser.add_argument("--confirm", default="")
rollback_parser = sub.add_parser("rollback")
rollback_parser.add_argument("--confirm", default="")
restore_patch_parser = sub.add_parser("restore-patch")
restore_patch_parser.add_argument("patch")
restore_patch_parser.add_argument("--confirm", default="")
sub.add_parser("raw-show")
raw_plan_parser = sub.add_parser("raw-plan")
raw_plan_parser.add_argument("content")
raw_apply_parser = sub.add_parser("raw-apply")
raw_apply_parser.add_argument("content")
raw_apply_parser.add_argument("--confirm", default="")
raw_rollback_parser = sub.add_parser("raw-rollback")
raw_rollback_parser.add_argument("--confirm", default="")
args = parser.parse_args()

try:
    known = schema()
    current = ops.read_json(OVERRIDES, {})
    if args.command == "schema":
        print(json.dumps(schema_payload(known, current), indent=2))
    elif args.command in {"plan", "apply"}:
        updates = json.loads(args.updates)
        errors = []
        if not isinstance(updates, dict):
            errors.append("updates must be an object")
            updates = {}
        for key, value in updates.items():
            item = known.get(key)
            if not item:
                errors.append(f"unknown setting {key}")
            elif not item["writable"]:
                errors.append(f"protected setting {key}")
            elif item["type"] == "boolean" and not isinstance(value, bool):
                errors.append(f"{key} requires boolean")
            elif item["type"] in {"integer", "number"} and (not isinstance(value, (int, float)) or isinstance(value, bool)):
                errors.append(f"{key} requires number")
            elif item["type"] == "string" and not isinstance(value, str):
                errors.append(f"{key} requires string")
            elif item["type"] == "string" and item.get("max_length") and len(value) > item["max_length"]:
                errors.append(f"{key} must be at most {item['max_length']} characters")
            elif item.get("choices") and value not in item["choices"]:
                errors.append(f"{key} must be one of {', '.join(item['choices'])}")
        preview = {
            "current": current, "updates": updates, "result": {**current, **updates},
            "errors": errors, "restart_required": bool(updates),
        }
        if args.command == "plan":
            print(json.dumps(preview, indent=2))
            raise SystemExit(bool(errors))
        if errors:
            raise ValueError("; ".join(errors))
        if args.confirm != "APPLY SETTINGS":
            raise ValueError("execution requires --confirm 'APPLY SETTINGS'")
        with ops.operation_lock():
            backup = apply_typed(updates, known, current)
        print(json.dumps({**preview, "backup": str(backup)}, indent=2))
    elif args.command == "rollback":
        if args.confirm != "ROLLBACK SETTINGS":
            raise ValueError("rollback requires --confirm 'ROLLBACK SETTINGS'")
        backups = sorted(ops.STATE.glob("settings-overrides-*.json"), reverse=True)
        if not backups:
            raise ValueError("no settings rollback exists")
        with ops.operation_lock():
            previous = OVERRIDES.read_text() if OVERRIDES.exists() else ""
            existed = OVERRIDES.exists()
            ops.atomic_text(OVERRIDES, backups[0].read_text())
            render_or_restore(OVERRIDES, previous, existed)
            mark_pending("typed-rollback", ["rollback"])
        ops.audit("settings.rollback", "ok", backup=str(backups[0]))
        print(json.dumps({"ok": True, "backup": str(backups[0])}))
    elif args.command == "restore-patch":
        if args.confirm != "RESTORE EVENT SETTINGS":
            raise ValueError("restore requires --confirm 'RESTORE EVENT SETTINGS'")
        patch = json.loads(args.patch)
        if not isinstance(patch, dict):
            raise ValueError("event restore patch must be an object")
        restored = dict(current)
        for key, entry in patch.items():
            item = known.get(key)
            if not item or not item["writable"] or not isinstance(entry, dict) or not isinstance(entry.get("present"), bool):
                raise ValueError(f"invalid event restore entry {key}")
            if entry["present"]:
                value = entry.get("value")
                if item["type"] == "boolean" and not isinstance(value, bool): raise ValueError(f"{key} requires boolean")
                if item["type"] in {"integer", "number"} and (not isinstance(value, (int, float)) or isinstance(value, bool)): raise ValueError(f"{key} requires number")
                if item["type"] == "string" and not isinstance(value, str): raise ValueError(f"{key} requires string")
                if item.get("choices") and value not in item["choices"]: raise ValueError(f"{key} has an invalid choice")
                restored[key] = value
            else:
                restored.pop(key, None)
        with ops.operation_lock():
            backup = replace_typed(restored, "timed-event-restore")
        print(json.dumps({"ok": True, "backup": str(backup), "restored": sorted(patch)}, indent=2))
    elif args.command == "raw-show":
        print(json.dumps({
            "content": redact_raw(raw_source()), "max_bytes": MAX_RAW_BYTES,
            "protected_fields": sorted(PROTECTED), "pending_restart": ops.read_json(PENDING, {}),
        }))
    elif args.command in {"raw-plan", "raw-apply"}:
        preview, merged = raw_plan(args.content)
        if args.command == "raw-plan":
            print(json.dumps(preview, indent=2))
            raise SystemExit()
        if args.confirm != "APPLY RAW SETTINGS":
            raise ValueError("execution requires --confirm 'APPLY RAW SETTINGS'")
        with ops.operation_lock():
            before = raw_source()
            existed = RAW.exists()
            previous = RAW.read_text() if existed else ""
            backup = ops.STATE / f"settings-raw-{time.time_ns()}.ini"
            ops.atomic_text(backup, before)
            ops.atomic_text(RAW, merged)
            try:
                render_or_restore(RAW, previous, existed)
            except (OSError, subprocess.CalledProcessError):
                ops.audit("settings.raw.apply", "failed", backup=str(backup))
                raise
            mark_pending("raw", ["raw configuration"])
        ops.audit("settings.raw.apply", "ok", backup=str(backup))
        print(json.dumps({**preview, "backup": str(backup)}, indent=2))
    elif args.command == "raw-rollback":
        if args.confirm != "ROLLBACK RAW SETTINGS":
            raise ValueError("rollback requires --confirm 'ROLLBACK RAW SETTINGS'")
        backups = sorted(ops.STATE.glob("settings-raw-*.ini"), reverse=True)
        if not backups:
            raise ValueError("no raw settings rollback exists")
        with ops.operation_lock():
            previous = RAW.read_text() if RAW.exists() else ""
            existed = RAW.exists()
            ops.atomic_text(RAW, backups[0].read_text())
            render_or_restore(RAW, previous, existed)
            mark_pending("raw-rollback", ["raw configuration"])
        ops.audit("settings.raw.rollback", "ok", backup=str(backups[0]))
        print(json.dumps({"ok": True, "backup": str(backups[0])}))
except (ValueError, json.JSONDecodeError, OSError, subprocess.CalledProcessError) as exc:
    raise SystemExit(str(exc))
