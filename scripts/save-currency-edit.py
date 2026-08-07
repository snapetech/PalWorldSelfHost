#!/usr/bin/env python3
"""Offline, verified item-stack edits for a stopped-world Palworld save.

Palworld has no vanilla or PalDefender-free way to grant/adjust a specific
player's items remotely. This edits the world save directly while the
server is stopped: decompress (Oodle/Kraken saves via the bundled
third_party/ooz decompressor, legacy zlib saves natively), decode only the
.worldSaveData.ItemContainerSaveData property tree via palworld-save-tools
(every other property is left as opaque, unparsed bytes and therefore
byte-for-byte preserved), adjust one item stack, re-encode, and verify the
resulting GVAS differs from the original by only the edited field before
ever writing anything. Saves are always written back as plain zlib (PlZ,
save type 0x31); Palworld reads that format regardless of which format it
last wrote, and no Oodle *compressor* is available or required.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import re
import struct
import subprocess
import sys
import tempfile
import time
import zlib

HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("ops_lib", HERE / "ops-lib.py")
ops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops)

INSTALL = pathlib.Path(os.environ.get("PALWORLD_INSTALL_DIR", "/srv/palworld/server")).resolve()
STATE = pathlib.Path(os.environ.get("PALWORLD_STATE_DIR", "/var/lib/palworld")).resolve()
DECOMPRESSOR = HERE.parent / "third_party" / "ooz" / "palworld_decompress"
BACKUPS = STATE / "save-currency-edit-backups"

PLAYER_ID = re.compile(r"[0-9A-Fa-f]{32}")
ITEM_ID = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,79}")
CONTAINER_FIELDS = (
    "CommonContainerId", "EssentialContainerId", "FoodEquipContainerId",
    "PlayerEquipArmorContainerId", "DropSlotContainerId",
)
MAGIC_ZLIB = b"PlZ"
MAGIC_OODLE = b"PlM"
MAX_UNCOMPRESSED = 512 * 1024 * 1024


def _psl():
    try:
        import palworld_save_tools  # noqa: F401
    except ImportError as exc:
        raise ValueError(
            "the palworld-save-tools package is required (pip install palworld-save-tools)"
        ) from exc


def world_dir() -> pathlib.Path:
    root = INSTALL / "Pal/Saved/SaveGames/0"
    candidates = [p for p in root.iterdir() if p.is_dir() and re.fullmatch(r"[0-9A-Fa-f]{32}", p.name)]
    if len(candidates) != 1:
        raise ValueError(f"expected exactly one world save directory under {root}, found {len(candidates)}")
    return candidates[0]


def server_active() -> bool:
    service = os.environ.get("PALWORLD_SERVICE", "palworld.service")
    try:
        if subprocess.run(
            ["systemctl", "is-active", "--quiet", service],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
        ).returncode == 0:
            return True
    except (OSError, subprocess.TimeoutExpired):
        pass
    for entry in pathlib.Path("/proc").glob("[0-9]*/cmdline"):
        try:
            command = entry.read_bytes().replace(b"\0", b" ").decode(errors="ignore").casefold()
        except OSError:
            continue
        if "palserver-linux-shipping" in command or "palserver-win64-shipping" in command:
            return True
    return False


def sav_header(data: bytes):
    if len(data) < 12:
        raise ValueError("file is too small to be a Palworld save")
    uncompressed_len = int.from_bytes(data[0:4], "little")
    compressed_len = int.from_bytes(data[4:8], "little")
    magic = data[8:11]
    save_type = data[11]
    if magic == b"CNK":
        raise ValueError("Xbox container saves are not supported")
    if magic not in (MAGIC_ZLIB, MAGIC_OODLE):
        raise ValueError(f"unrecognized save magic {magic!r}")
    if not 0 < uncompressed_len <= MAX_UNCOMPRESSED:
        raise ValueError("implausible uncompressed length in save header")
    if compressed_len != len(data) - 12:
        raise ValueError("compressed length in header does not match file size")
    return uncompressed_len, compressed_len, magic, save_type


def decompress(path: pathlib.Path) -> bytes:
    data = path.read_bytes()
    uncompressed_len, _, magic, save_type = sav_header(data)
    payload = data[12:]
    if magic == MAGIC_ZLIB:
        raw = zlib.decompress(payload)
        if save_type == 0x32:
            raw = zlib.decompress(raw)
    else:
        if not DECOMPRESSOR.is_file():
            raise ValueError(f"{DECOMPRESSOR} is missing; run third_party/ooz/fetch-build.sh first")
        with tempfile.TemporaryDirectory() as tmp:
            payload_path = pathlib.Path(tmp) / "payload"
            out_path = pathlib.Path(tmp) / "out"
            payload_path.write_bytes(payload)
            subprocess.run(
                [str(DECOMPRESSOR), str(payload_path), str(uncompressed_len), str(out_path)],
                check=True, capture_output=True, timeout=120,
            )
            raw = out_path.read_bytes()
    if len(raw) != uncompressed_len:
        raise ValueError("decompressed length does not match the save header")
    return raw


def recompress_zlib(raw: bytes) -> bytes:
    compressed = zlib.compress(raw)
    out = bytearray()
    out += len(raw).to_bytes(4, "little")
    out += len(compressed).to_bytes(4, "little")
    out += MAGIC_ZLIB
    out += bytes([0x31])
    out += compressed
    return bytes(out)


@contextlib.contextmanager
def quiet():
    """palworld-save-tools prints informational struct-type-guess notices to
    stdout during decode; keep this tool's stdout to its final JSON only."""
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def filtered_custom_properties():
    from palworld_save_tools.rawdata import item_container
    return {
        ".worldSaveData.ItemContainerSaveData.Value.RawData": (
            item_container.decode,
            item_container.encode,
        ),
    }


def player_container_ids(player_path: pathlib.Path) -> dict[str, str]:
    from palworld_save_tools.gvas import GvasFile
    from palworld_save_tools.paltypes import PALWORLD_TYPE_HINTS

    raw = decompress(player_path)
    with quiet():
        gvas = GvasFile.read(raw, PALWORLD_TYPE_HINTS, {}, allow_nan=True)
    info = gvas.properties["SaveData"]["value"]["InventoryInfo"]["value"]
    return {
        field: str(info[field]["value"]["ID"]["value"])
        for field in CONTAINER_FIELDS
        if field in info
    }


def parse_slot(raw_values) -> tuple[int, int, int, str, bytes, int]:
    b = bytes(raw_values)
    if len(b) < 12:
        raise ValueError("item slot raw data is too short")
    slot_index, stack_count, strlen = struct.unpack_from("<iii", b, 0)
    if not 0 <= strlen <= len(b) - 12:
        raise ValueError("item slot string length is out of range")
    item_id = b[12:12 + strlen].split(b"\x00")[0].decode()
    rest = b[12 + strlen:]
    return slot_index, stack_count, strlen, item_id, rest, len(b)


def find_item(gvas, container_ids: dict[str, str], item_id: str):
    containers = gvas.properties["worldSaveData"]["value"]["ItemContainerSaveData"]["value"]
    wanted = set(container_ids.values())
    for entry in containers:
        key = str(entry["key"]["ID"]["value"])
        if key not in wanted:
            continue
        slots = entry["value"]["Slots"]["value"]["values"]
        for slot in slots:
            raw = slot["RawData"]["value"]["values"]
            if len(raw) < 12:
                continue
            slot_index, stack_count, strlen, found_id, rest, total_len = parse_slot(raw)
            if found_id == item_id:
                return slot, slot_index, stack_count, strlen, rest, total_len
    return None


def canonical_hash(value: dict) -> str:
    import hashlib
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def compute(player_id: str, item_id: str, delta: int):
    _psl()
    if not PLAYER_ID.fullmatch(player_id):
        raise ValueError("player id must be the 32-character hex save filename stem")
    if not ITEM_ID.fullmatch(item_id):
        raise ValueError("item id is invalid")
    if delta == 0:
        raise ValueError("delta must be nonzero")

    save_dir = world_dir()
    player_path = save_dir / "Players" / f"{player_id.upper()}.sav"
    level_path = save_dir / "Level.sav"
    if not player_path.is_file():
        raise ValueError(f"no player save found at {player_path}")
    if not level_path.is_file():
        raise ValueError(f"no Level.sav found at {level_path}")

    container_ids = player_container_ids(player_path)

    from palworld_save_tools.gvas import GvasFile
    from palworld_save_tools.paltypes import PALWORLD_TYPE_HINTS

    custom_properties = filtered_custom_properties()
    level_raw = decompress(level_path)
    with quiet():
        gvas = GvasFile.read(level_raw, PALWORLD_TYPE_HINTS, custom_properties, allow_nan=True)

    found = find_item(gvas, container_ids, item_id)
    if found is None:
        raise ValueError(f"item {item_id} was not found in any of this player's containers")
    slot, slot_index, stack_count, strlen, rest, total_len = found
    new_amount = stack_count + delta
    if new_amount < 0:
        raise ValueError(f"resulting stack count would be negative ({stack_count} + {delta})")

    return {
        "gvas": gvas, "custom_properties": custom_properties, "level_path": level_path,
        "slot": slot, "slot_index": slot_index, "current_amount": stack_count,
        "new_amount": new_amount, "strlen": strlen, "rest": rest, "total_len": total_len,
        "level_raw": level_raw,
    }


def plan(player_id: str, item_id: str, delta: int) -> dict:
    state = compute(player_id, item_id, delta)
    result = {
        "player_id": player_id.upper(),
        "item_id": item_id,
        "delta": delta,
        "current_amount": state["current_amount"],
        "new_amount": state["new_amount"],
        "level_sav_sha256": __import__("hashlib").sha256(state["level_raw"]).hexdigest(),
        "server_must_be_stopped": True,
        "server_active": server_active(),
        "confirmation": "APPLY SAVE ITEM EDIT",
    }
    result["plan_hash"] = canonical_hash(result)
    return result


def apply(player_id: str, item_id: str, delta: int, expected_plan_hash: str, confirmation: str) -> dict:
    reviewed = plan(player_id, item_id, delta)
    if reviewed["plan_hash"] != expected_plan_hash:
        raise ValueError("save item edit plan changed after review")
    if confirmation != reviewed["confirmation"]:
        raise ValueError(f"exact confirmation {reviewed['confirmation']} is required")
    if server_active():
        raise ValueError("Palworld must be stopped before editing the world save")

    state = compute(player_id, item_id, delta)
    if state["current_amount"] != reviewed["current_amount"]:
        raise ValueError("item stack changed on disk after review")

    slot = state["slot"]
    slot_index, strlen, rest, total_len = state["slot_index"], state["strlen"], state["rest"], state["total_len"]
    old_raw = bytes(slot["RawData"]["value"]["values"])
    string_field = old_raw[12:12 + strlen]
    new_raw = struct.pack("<iii", slot_index, state["new_amount"], strlen) + string_field + rest
    if len(new_raw) != total_len:
        raise ValueError("post-edit slot length does not match the original")

    with ops.operation_lock():
        if server_active():
            raise ValueError("Palworld became active after review")

        slot["RawData"]["value"]["values"] = tuple(new_raw)
        new_gvas = state["gvas"].write(state["custom_properties"])

        if len(new_gvas) != len(state["level_raw"]):
            raise ValueError("post-edit save size does not match the original; refusing to write")
        diffs = [i for i in range(len(new_gvas)) if new_gvas[i] != state["level_raw"][i]]
        if not diffs or max(diffs) - min(diffs) > 64:
            raise ValueError(
                f"post-edit verification failed: {len(diffs)} byte(s) differ across a "
                f"{(max(diffs) - min(diffs) + 1) if diffs else 0}-byte span; refusing to write"
            )

        new_sav = recompress_zlib(new_gvas)

        # Round-trip our own output back through the encoder before it ever touches the
        # real file: if this fails, the original on disk is never replaced.
        check_raw = decompress_bytes_for_verification(new_sav)
        if check_raw != new_gvas:
            raise ValueError("self-verification of the recompressed save failed; refusing to write")

        BACKUPS.mkdir(parents=True, exist_ok=True)
        backup = BACKUPS / f"{time.time_ns()}-Level.sav"
        backup.write_bytes(state["level_path"].read_bytes())

        descriptor, tmp_name = tempfile.mkstemp(prefix=".Level.sav.", dir=str(state["level_path"].parent))
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(new_sav)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_name, state["level_path"])
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    ops.audit(
        "save-currency-edit.apply", "ok", player_id=player_id.upper(), item_id=item_id,
        delta=delta, new_amount=state["new_amount"], backup=str(backup),
    )
    return {**reviewed, "backup": str(backup), "changed_bytes": len(diffs)}


def decompress_bytes_for_verification(sav_bytes: bytes) -> bytes:
    uncompressed_len, _, magic, save_type = sav_header(sav_bytes)
    payload = sav_bytes[12:]
    raw = zlib.decompress(payload)
    if save_type == 0x32:
        raw = zlib.decompress(raw)
    if len(raw) != uncompressed_len:
        raise ValueError("self-verification decompressed length mismatch")
    return raw


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "apply"):
        item = sub.add_parser(name)
        item.add_argument("player_id", help="32-character hex player save filename stem")
        item.add_argument("item_id", help="Palworld item static id, e.g. Money")
        item.add_argument("delta", type=int, help="signed amount to add to the current stack")
        if name == "apply":
            item.add_argument("--expected-plan-hash", required=True)
            item.add_argument("--confirm", default="")
    args = parser.parse_args()
    try:
        if args.command == "plan":
            result = plan(args.player_id, args.item_id, args.delta)
        else:
            result = apply(args.player_id, args.item_id, args.delta, args.expected_plan_hash, args.confirm)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (ValueError, OSError, subprocess.SubprocessError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)[:1000]}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
