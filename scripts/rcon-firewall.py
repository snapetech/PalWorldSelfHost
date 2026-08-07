#!/usr/bin/env python3
"""Install and verify an nftables loopback-only boundary for Palworld REST/RCON."""

import argparse
import json
import os
import re
import subprocess


TABLE = "palworldselfhost"


def port_value():
    port = int(os.environ.get("PALWORLD_RCON_PORT", "25575"))
    if not 1024 <= port <= 65535:
        raise ValueError("PALWORLD_RCON_PORT must be between 1024 and 65535")
    return port


def rest_port_value():
    port = int(os.environ.get("PALWORLD_REST_PORT", "8212"))
    if not 1024 <= port <= 65535:
        raise ValueError("PALWORLD_REST_PORT must be between 1024 and 65535")
    return port


def run(*arguments, input_text=None, check=True):
    result = subprocess.run(arguments, input=input_text, text=True, capture_output=True, timeout=15)
    if check and result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip()[-1000:] or "nft failed")
    return result


def table_exists():
    return run("nft", "list", "table", "inet", TABLE, check=False).returncode == 0


def rules(port, rest_port):
    prefix = f"delete table inet {TABLE}\n" if table_exists() else ""
    return prefix + f"""table inet {TABLE} {{
  chain rcon_input {{
    type filter hook input priority -150; policy accept;
    iifname \"lo\" tcp dport {port} counter accept comment \"PalWorldSelfHost RCON loopback\"
    tcp dport {port} counter drop comment \"PalWorldSelfHost block external RCON\"
    iifname \"lo\" tcp dport {rest_port} counter accept comment \"PalWorldSelfHost REST loopback\"
    tcp dport {rest_port} counter drop comment \"PalWorldSelfHost block external REST\"
  }}
}}
"""


def apply():
    port = port_value()
    run("nft", "-f", "-", input_text=rules(port, rest_port_value()))
    result = check()
    if not result["effective_private"] or not result["rest_effective_private"]:
        raise RuntimeError("REST/RCON firewall verification failed after apply")
    return result


def check():
    port = port_value()
    rest_port = rest_port_value()
    result = run("nft", "list", "table", "inet", TABLE, check=False)
    output = result.stdout if result.returncode == 0 else ""
    normalized = re.sub(r"\s+", " ", output)
    loopback_accept = bool(re.search(rf'iifname "lo" tcp dport {port} .* accept', normalized))
    external_drop = bool(re.search(rf'tcp dport {port} .* drop', normalized))
    rest_loopback_accept = bool(re.search(rf'iifname "lo" tcp dport {rest_port} .* accept', normalized))
    rest_external_drop = bool(re.search(rf'tcp dport {rest_port} .* drop', normalized))
    return {
        "table": TABLE, "port": port, "rest_port": rest_port, "installed": result.returncode == 0,
        "loopback_accept": loopback_accept, "external_drop": external_drop,
        "rest_loopback_accept": rest_loopback_accept, "rest_external_drop": rest_external_drop,
        "rest_effective_private": rest_loopback_accept and rest_external_drop,
        "effective_private": loopback_accept and external_drop,
    }


def clear():
    if table_exists():
        run("nft", "delete", "table", "inet", TABLE)
    return check()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["apply", "check", "clear"])
    args = parser.parse_args()
    try:
        result = {"apply": apply, "check": check, "clear": clear}[args.action]()
        print(json.dumps(result, indent=2))
        if args.action != "clear" and (not result["effective_private"] or not result["rest_effective_private"]):
            raise SystemExit(1)
    except Exception as exc:
        print(json.dumps({"effective_private": False, "error": str(exc)[:500]}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
