#!/usr/bin/env python3
"""Bounded, privacy-reducing poller for Palworld's optional Game Data API.

The endpoint is not present in every dedicated-server build.  Capability and
freshness are therefore recorded independently from ordinary REST health.
"""

import argparse
import base64
import json
import math
import os
import pathlib
import tempfile
import time
import urllib.error
import urllib.request


MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_ACTORS = 250_000
MAX_PROJECTED_ACTORS = 2_048
STATE = pathlib.Path(os.environ.get("PALWORLD_STATE_DIR", "/var/lib/palworld"))
REPORT = STATE / "game-data.json"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


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
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def read_report(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, PermissionError, json.JSONDecodeError):
        return {}


def clean(value, limit=100):
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def active(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    return None


def activity(actor):
    raw = " ".join(clean(actor.get(key), 128).lower() for key in ("Action", "AI_Action", "Stage"))
    for result, needles in (
        ("incapacitated", ("down", "dead", "knockout", "incapacitat")),
        ("combat", ("attack", "battle", "combat")),
        ("sleeping", ("sleep",)), ("eating", ("eat", "food")),
        ("transporting", ("transport", "carry")),
        ("working", ("work", "craft", "build", "generate", "harvest", "mine", "logging")),
        ("moving", ("move", "walk", "run", "return")),
    ):
        if any(needle in raw for needle in needles):
            return result
    state = active(actor.get("IsActive"))
    return "idle" if state is True else ("inactive" if state is False else "unknown")


def character_id(value):
    result = clean(value, 200)
    if "/" in result or "." in result:
        result = result.replace("/", ".").rsplit(".", 1)[-1]
    if result.startswith("Default__"):
        result = result[9:]
    if result.endswith("_C"):
        result = result[:-2]
    boss = result.upper().startswith("BOSS_")
    return (result[5:] if boss else result)[:100], boss


def actor_kind(actor):
    if actor.get("Type") == "PalBox":
        return "PalBox"
    if actor.get("Type") == "Character":
        unit = clean(actor.get("UnitType"), 32)
        return unit if unit in {"Player", "OtomoPal", "BaseCampPal", "WildPal", "NPC"} else "Unknown"
    return "Unknown"


def project_snapshot(snapshot):
    if not isinstance(snapshot, dict):
        raise ValueError("top-level JSON must be an object")
    fps, average = snapshot.get("FPS"), snapshot.get("AverageFPS")
    if not finite_number(fps) or not finite_number(average):
        raise ValueError("FPS fields must be finite numbers")
    actors = snapshot.get("ActorData")
    if not isinstance(actors, list):
        raise ValueError("ActorData must be an array")
    if len(actors) > MAX_ACTORS:
        raise ValueError("actor count exceeds safety limit")
    counts = {key: 0 for key in ("players", "party_pals", "base_pals", "wild_pals", "npcs", "palboxes", "unknown")}
    count_key = {"Player": "players", "OtomoPal": "party_pals", "BaseCampPal": "base_pals",
                 "WildPal": "wild_pals", "NPC": "npcs", "PalBox": "palboxes", "Unknown": "unknown"}
    selected = []
    for actor in actors:
        if not isinstance(actor, dict):
            raise ValueError("each ActorData row must be an object")
        for key in ("LocationX", "LocationY", "LocationZ"):
            if not finite_number(actor.get(key, 0)):
                raise ValueError("actor coordinates must be finite numbers")
        kind = actor_kind(actor)
        counts[count_key[kind]] += 1
        if kind not in {"Player", "OtomoPal", "BaseCampPal", "PalBox", "WildPal"}:
            continue
        if len(selected) >= MAX_PROJECTED_ACTORS:
            continue
        species, boss = character_id(actor.get("Class", ""))
        maximum, current = actor.get("MaxHP"), actor.get("HP")
        hp_percent = None
        if finite_number(maximum) and finite_number(current) and maximum > 0 and current >= 0:
            hp_percent = round(min(100, 100 * current / maximum), 1)
        selected.append({
            "kind": kind, "character_id": species if kind != "Player" else "", "boss": boss,
            "name": clean(actor.get("NickName")), "trainer_name": clean(actor.get("TrainerNickName")),
            "guild_name": clean(actor.get("GuildName")),
            "level": actor.get("level") if isinstance(actor.get("level"), int) else None,
            "hp_percent": hp_percent, "active": active(actor.get("IsActive")), "activity": activity(actor),
            "position": {"x": actor.get("LocationX", 0), "y": actor.get("LocationY", 0), "z": actor.get("LocationZ", 0)},
        })
    return {
        "source_time": clean(snapshot.get("Time"), 32), "fps": fps, "average_fps": average,
        "counts": counts, "actors": selected, "truncated": len(selected) < sum(
            counts[key] for key in ("players", "party_pals", "base_pals", "wild_pals", "palboxes")
        ), "source_actor_count": len(actors),
    }


def fetch_snapshot(opener=None):
    port = int(os.environ.get("PALWORLD_REST_PORT", "8212"))
    if port < 1 or port > 65535:
        raise ValueError("PALWORLD_REST_PORT is invalid")
    token = base64.b64encode(f"admin:{os.environ['PALWORLD_ADMIN_PASSWORD']}".encode()).decode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/api/game-data",
        headers={"Authorization": f"Basic {token}", "Accept": "application/json"},
    )
    opener = opener or urllib.request.build_opener(NoRedirect())
    with opener.open(request, timeout=12) as response:
        length = response.headers.get("Content-Length")
        if length and int(length) > MAX_RESPONSE_BYTES:
            raise ValueError("response exceeds safety limit")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("response exceeds safety limit")
    return json.loads(raw)


def poll(path=REPORT, fetch=fetch_snapshot, now=None):
    now = int(time.time() if now is None else now)
    previous = read_report(path)
    enabled = os.environ.get("PALWORLD_GAME_DATA_ENABLED", "false").lower() == "true"
    if not enabled:
        result = {"state": "disabled", "last_attempt_at": now, "counts": {}, "actors": []}
        atomic_json(path, result)
        return result
    try:
        projection = project_snapshot(fetch())
        old_count = previous.get("source_actor_count", 0)
        new_count = projection["source_actor_count"]
        if previous.get("captured_at") and old_count >= 10 and new_count * 4 < old_count and not previous.get("collapse_pending"):
            result = dict(previous, state="stale", last_attempt_at=now, last_error="collapsed", collapse_pending=True)
        else:
            result = dict(projection, state="ready", captured_at=now, last_attempt_at=now,
                          last_error="none", collapse_pending=False)
    except urllib.error.HTTPError as exc:
        category = "unsupported" if exc.code == 404 else ("unauthorized" if exc.code == 401 else "response")
        result = {"state": category if category in {"unsupported", "unauthorized"} else ("stale" if previous.get("captured_at") else "unavailable"),
                  "last_attempt_at": now, "last_error": category, "http_status": exc.code,
                  "captured_at": previous.get("captured_at"), "counts": previous.get("counts", {}),
                  "actors": previous.get("actors", []) if previous.get("captured_at") else []}
        exc.close()
    except (OSError, ValueError, KeyError, json.JSONDecodeError, TimeoutError) as exc:
        category = "invalid_response" if isinstance(exc, (ValueError, json.JSONDecodeError)) else "unreachable"
        result = {"state": "stale" if previous.get("captured_at") else "unavailable", "last_attempt_at": now,
                  "last_error": category, "captured_at": previous.get("captured_at"),
                  "counts": previous.get("counts", {}), "actors": previous.get("actors", []) if previous.get("captured_at") else []}
    atomic_json(path, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("poll", "status"))
    parser.add_argument("--output", default=str(REPORT))
    args = parser.parse_args()
    path = pathlib.Path(args.output)
    result = poll(path) if args.command == "poll" else read_report(path)
    print(json.dumps(result or {"state": "never_polled", "counts": {}, "actors": []}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
