#!/usr/bin/env python3
"""Exact-confirmed, reversible Discord integration validation."""

import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import secrets
import time


HERE = pathlib.Path(__file__).resolve().parent
STATE = pathlib.Path(os.environ.get("PALWORLD_STATE_DIR", "/var/lib/palworld"))
RECEIPT = STATE / "discord-validation.json"
CONFIRMATION = "VALIDATE DISCORD DELIVERY"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


control = load("discord_validation_control", HERE / "bot_control.py")
ops = load("discord_validation_ops", HERE / "ops-lib.py")


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def configured_bot():
    token = os.environ.get("PALWORLD_DISCORD_BOT_TOKEN", "").strip()
    application_id = os.environ.get("PALWORLD_DISCORD_APPLICATION_ID", "").strip()
    guild_id = os.environ.get("PALWORLD_DISCORD_GUILD_ID", "").strip()
    channels = control.snowflake_set(
        os.environ.get("PALWORLD_DISCORD_CONTROL_CHANNELS"),
        "Discord control channels", required=True,
    )
    admins = control.snowflake_set(
        os.environ.get("PALWORLD_DISCORD_ADMINS"),
        "Discord administrators", required=True,
    )
    moderators = control.snowflake_set(
        os.environ.get("PALWORLD_DISCORD_MODERATORS"),
        "Discord moderators",
    )
    if len(token) < 24:
        raise ValueError("PALWORLD_DISCORD_BOT_TOKEN must contain at least 24 characters")
    channel = os.environ.get("PALWORLD_DISCORD_CHAT_CHANNEL", "").strip()
    channel = control.snowflake(channel, "Discord validation channel")
    if os.environ.get("PALWORLD_CHAT_RELAY_ENABLED", "false").lower() != "true":
        raise ValueError("PALWORLD_CHAT_RELAY_ENABLED=true is required")
    relay_token = os.environ.get("PALWORLD_CHAT_RELAY_TOKEN", "").strip()
    if len(relay_token) < 24:
        raise ValueError("PALWORLD_CHAT_RELAY_TOKEN must contain at least 24 characters")
    bot = control.DiscordBot(
        token=token, application_id=application_id, guild_id=guild_id,
        channels=channels, admins=admins, moderators=moderators,
        engine=None, ops=ops, state_dir=STATE,
    )
    material = {
        "schema": 1,
        "application_id": application_id,
        "guild_id": guild_id,
        "channels": sorted(channels),
        "admins": sorted(admins),
        "moderators": sorted(moderators),
        "relay_channel": channel,
        "bot_token_sha256": digest(token),
        "relay_token_sha256": digest(relay_token),
        "commands": control.discord_commands(),
    }
    plan_hash = digest(json.dumps(material, sort_keys=True, separators=(",", ":")))
    return bot, channel, plan_hash


def plan():
    _, _, plan_hash = configured_bot()
    return {
        "schema": 1,
        "plan_hash": plan_hash,
        "confirmation": CONFIRMATION,
        "commands": [item["name"] for item in control.discord_commands()],
        "external_effects": [
            "replace the configured guild command set",
            "send one mention-suppressed validation message",
            "delete that validation message immediately",
        ],
        "persistent_content": False,
    }


def execute(expected_plan_hash, confirmation):
    bot, channel, plan_hash = configured_bot()
    if expected_plan_hash != plan_hash:
        raise ValueError("Discord validation plan changed; create a fresh plan")
    if confirmation != CONFIRMATION:
        raise ValueError(f"exact confirmation {CONFIRMATION} is required")
    marker = secrets.token_hex(8)
    try:
        with ops.operation_lock("discord-validation"):
            result = bot.validate_external_delivery(channel, marker)
            if not result.get("message_deleted"):
                raise RuntimeError("Discord validation message was not deleted")
            receipt = {
                "schema": 1,
                "validated": True,
                "validated_at": int(time.time()),
                "plan_hash": plan_hash,
                **result,
            }
            ops.atomic_json(RECEIPT, receipt)
            ops.audit(
                "bot.discord-validation", "ok", remote_source="discord",
                commands_registered=result["commands_registered"],
                message_deleted=True,
            )
        return receipt
    except Exception as exc:
        ops.audit(
            "bot.discord-validation", "failed", remote_source="discord",
            error=type(exc).__name__,
        )
        raise


def status():
    receipt = ops.read_json(RECEIPT, {})
    if not isinstance(receipt, dict) or not receipt:
        return {"schema": 1, "validated": False, "reason": "no validation receipt"}
    try:
        _, _, current_plan_hash = configured_bot()
    except ValueError:
        return {
            "schema": 1, "validated": False,
            "receipt_present": True, "reason": "Discord configuration is incomplete",
        }
    if receipt.get("plan_hash") != current_plan_hash:
        return {
            "schema": 1, "validated": False,
            "receipt_present": True, "reason": "Discord configuration changed after validation",
        }
    return receipt


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("plan")
    execute_parser = subparsers.add_parser("execute")
    execute_parser.add_argument("--expected-plan-hash", required=True)
    execute_parser.add_argument("--confirm", required=True)
    subparsers.add_parser("status")
    arguments = parser.parse_args()
    try:
        if arguments.command == "plan":
            result = plan()
        elif arguments.command == "execute":
            result = execute(arguments.expected_plan_hash, arguments.confirm)
        else:
            result = status()
        print(json.dumps(result, indent=2, sort_keys=True))
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
