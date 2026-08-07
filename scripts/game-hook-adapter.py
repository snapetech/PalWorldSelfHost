#!/usr/bin/env python3
"""Forward bounded UE4SS chat events from volatile tmpfs to the private relay."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import re
import time
import urllib.error
import urllib.request


HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("ops_lib", HERE / "ops-lib.py")
ops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops)

MAX_EVENT_BYTES = 4096
MAX_PENDING = 2048
EVENT_NAME = re.compile(r"event-[0-9]{1,20}-[0-9]{6}\.json")


def volatile_spool() -> pathlib.Path:
    configured = pathlib.Path(os.environ.get("PALWORLD_GAME_HOOK_SPOOL", "/run/palworld/game-hook"))
    resolved_parent = configured.parent.resolve()
    if not any(resolved_parent == root or root in resolved_parent.parents for root in (pathlib.Path("/run"), pathlib.Path("/dev/shm"))):
        raise ValueError("PALWORLD_GAME_HOOK_SPOOL must be on /run or /dev/shm tmpfs")
    if configured.is_symlink():
        raise ValueError("game-hook spool must not be a symlink")
    configured.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(configured, 0o700)
    return configured


def bounded_text(value, name, maximum):
    if not isinstance(value, str) or not value or len(value) > maximum or "\0" in value:
        raise ValueError(f"{name} must contain 1-{maximum} safe characters")
    return value


def parse_event(path: pathlib.Path) -> tuple[dict, bytes]:
    if path.is_symlink() or not path.is_file() or not EVENT_NAME.fullmatch(path.name):
        raise ValueError("event path is not an ordinary allowlisted spool file")
    raw = path.read_bytes()
    if not raw or len(raw) > MAX_EVENT_BYTES:
        raise ValueError("game-hook event must contain 1-4096 bytes")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("game-hook event is malformed JSON") from exc
    if not isinstance(value, dict) or value.get("schema") != 1 or value.get("kind") != "chat":
        raise ValueError("game-hook event has an unsupported schema or kind")
    category = bounded_text(value.get("category"), "category", 16).lower()
    if category not in {"say", "guild", "global"}:
        raise ValueError("game-hook event has an unsupported chat category")
    payload = {
        "event_id": "ue4ss-" + hashlib.sha256(path.name.encode() + b"\0" + raw).hexdigest()[:48],
        "player": bounded_text(value.get("player"), "player", 80),
        "message": bounded_text(value.get("message"), "message", 500),
        "category": category,
    }
    return payload, raw


def relay(payload: dict) -> int:
    token = os.environ.get("PALWORLD_CHAT_RELAY_TOKEN", "")
    if len(token) < 24:
        raise ValueError("PALWORLD_CHAT_RELAY_TOKEN must contain at least 24 characters")
    port = int(os.environ.get("PALWORLD_CHAT_RELAY_PORT", "8215"))
    if not 1024 <= port <= 65535:
        raise ValueError("PALWORLD_CHAT_RELAY_PORT must be between 1024 and 65535")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/chat", data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            response.read(4096)
            return response.status
    except urllib.error.HTTPError as exc:
        try:
            exc.read(4096)
            return exc.code
        finally:
            exc.close()


def process_once(spool=None) -> dict:
    spool = pathlib.Path(spool) if spool else volatile_spool()
    categories = {item.strip().lower() for item in os.environ.get("PALWORLD_CHAT_RELAY_CATEGORIES", "global").split(",") if item.strip()}
    if not categories or not categories <= {"say", "guild", "global"}:
        raise ValueError("relay categories must be a non-empty subset of say,guild,global")
    files = sorted((item for item in spool.iterdir() if EVENT_NAME.fullmatch(item.name)), key=lambda item: item.name)
    if len(files) > MAX_PENDING:
        for path in files[: len(files) - MAX_PENDING]:
            path.unlink(missing_ok=True)
        ops.audit("game-hook.drop", "failed", reason="spool-bound", dropped=len(files) - MAX_PENDING)
        files = files[-MAX_PENDING:]
    result = {"relayed": 0, "ignored": 0, "rejected": 0, "retry": 0}
    for path in files[:100]:
        try:
            payload, _ = parse_event(path)
            if payload["category"] not in categories:
                path.unlink(missing_ok=True); result["ignored"] += 1; continue
            status = relay(payload)
            if 200 <= status < 300 or status == 409:
                path.unlink(missing_ok=True); result["relayed"] += 1
            elif 400 <= status < 500:
                path.unlink(missing_ok=True); result["rejected"] += 1
            else:
                result["retry"] += 1
        except (ValueError, OSError, UnicodeError):
            path.unlink(missing_ok=True); result["rejected"] += 1
    return result


def main() -> None:
    if os.environ.get("PALWORLD_CHAT_RELAY_ENABLED", "false").lower() != "true":
        raise SystemExit("game-hook adapter is disabled")
    spool = volatile_spool()
    ops.audit("game-hook.start", "ok", spool="volatile", categories=sorted(
        item.strip() for item in os.environ.get("PALWORLD_CHAT_RELAY_CATEGORIES", "global").split(",") if item.strip()
    ))
    while True:
        try:
            process_once(spool)
        except Exception as exc:
            ops.audit("game-hook.poll", "failed", error=type(exc).__name__)
        time.sleep(0.25)


if __name__ == "__main__":
    main()
