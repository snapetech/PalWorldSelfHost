#!/usr/bin/env python3
"""Render PalWorldSettings.ini from the server's shipped defaults."""

import argparse
import json
import re
from pathlib import Path


def replace(option_line: str, key: str, value: str, *, required: bool = True) -> str:
    pattern = re.compile(rf"(?P<prefix>(?:^|[,(])\s*{re.escape(key)}=)(?P<value>\"(?:[^\"]|\\.)*\"|[^,)]*)")
    rendered, count = pattern.subn(lambda m: m.group("prefix") + value, option_line, count=1)
    if required and count != 1:
        raise SystemExit(f"required setting {key!r} is absent from the installed server template")
    return rendered


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("template", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--player-exp", required=True, type=float)
    parser.add_argument("--admin-password", required=True)
    parser.add_argument("--rest-port", required=True, type=int)
    parser.add_argument("--overrides", type=Path)
    args = parser.parse_args()

    text = args.template.read_text()
    if "OptionSettings=(" not in text:
        raise SystemExit("invalid DefaultPalWorldSettings.ini: OptionSettings is absent")
    text = replace(text, "DeathPenalty", "None")
    text = replace(text, "ExpRate", f"{args.player_exp:.6f}")
    text = replace(text, "ServerName", f'"{args.name.replace(chr(34), "")}"')
    text = replace(text, "ServerDescription", f'"{args.description.replace(chr(34), "")}"')
    text = replace(text, "AdminPassword", f'"{args.admin_password.replace(chr(34), "")}"')
    text = replace(text, "PublicPort", str(args.port))
    text = replace(text, "RCONEnabled", "False")
    text = replace(text, "RESTAPIEnabled", "True", required=False)
    text = replace(text, "RESTAPIPort", str(args.rest_port), required=False)
    if args.overrides and args.overrides.exists():
        overrides = json.loads(args.overrides.read_text())
        for key, value in overrides.items():
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key):
                raise SystemExit(f"invalid override key: {key!r}")
            if isinstance(value, bool): rendered = "True" if value else "False"
            elif isinstance(value, (int, float)): rendered = str(value)
            elif isinstance(value, str): rendered = f'"{value.replace(chr(34), "")}"'
            else: raise SystemExit(f"unsupported override value for {key!r}")
            text = replace(text, key, rendered)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(args.output)


if __name__ == "__main__":
    main()
