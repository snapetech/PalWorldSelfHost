#!/usr/bin/env python3
"""Install a scoped Palworld hairpin DNAT rule for LAN community-server clients."""

import argparse
import ipaddress
import json
import os
import re
import subprocess


LEGACY_TABLE = "palworldhairpin"


def enabled():
    value = os.environ.get("PALWORLD_HAIRPIN_NAT_ENABLED", "false").strip().lower()
    if value not in {"true", "false"}:
        raise ValueError("PALWORLD_HAIRPIN_NAT_ENABLED must be true or false")
    return value == "true"


def settings():
    public_ip = str(ipaddress.IPv4Address(os.environ["PALWORLD_PUBLIC_IP"]))
    bind_ip = str(ipaddress.IPv4Address(os.environ["PALWORLD_BIND_IP"]))
    lan_cidr = str(ipaddress.IPv4Network(os.environ.get("PALWORLD_LAN_CIDR", "192.168.0.0/16"), strict=False))
    interface = os.environ.get("PALWORLD_NETWORK_INTERFACE", "").strip()
    if not interface or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", interface):
        raise ValueError("PALWORLD_NETWORK_INTERFACE is required and invalid")
    port = int(os.environ.get("PALWORLD_PORT", "8211"))
    if not 1024 <= port <= 65535:
        raise ValueError("PALWORLD_PORT must be between 1024 and 65535")
    return public_ip, bind_ip, lan_cidr, interface, port


def run(*arguments, input_text=None, check=True):
    result = subprocess.run(arguments, input=input_text, text=True, capture_output=True, timeout=15)
    if check and result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip()[-1000:] or "nft failed")
    return result


def rule_arguments(values=None):
    public_ip, bind_ip, lan_cidr, interface, port = values or settings()
    return (
        "PREROUTING", "-i", interface, "-s", lan_cidr, "-d", public_ip,
        "-p", "udp", "--dport", str(port), "-j", "DNAT",
        "--to-destination", f"{bind_ip}:{port}",
    )


def installed(values=None):
    return run("iptables", "-t", "nat", "-C", *rule_arguments(values), check=False).returncode == 0


def clear_rule(values=None):
    while installed(values):
        run("iptables", "-t", "nat", "-D", *rule_arguments(values))


def clear_legacy_table():
    if run("nft", "list", "table", "ip", LEGACY_TABLE, check=False).returncode == 0:
        run("nft", "delete", "table", "ip", LEGACY_TABLE)


def apply():
    if not enabled():
        clear_legacy_table()
        return {"enabled": False, "installed": False, "effective": True}
    values = settings()
    clear_legacy_table()
    if not installed(values):
        run("iptables", "-t", "nat", "-I", *rule_arguments(values))
    result = check()
    if not result["effective"]:
        raise RuntimeError("hairpin NAT verification failed after apply")
    return result


def check():
    required = enabled()
    effective = not required
    details = {"enabled": required, "installed": False}
    if required:
        public_ip, bind_ip, lan_cidr, interface, port = settings()
        effective = installed((public_ip, bind_ip, lan_cidr, interface, port))
        details["installed"] = effective
        details.update({"interface": interface, "lan_cidr": lan_cidr, "port": port})
    return {**details, "effective": effective}


def clear():
    if enabled():
        clear_rule()
    clear_legacy_table()
    return {"enabled": enabled(), "installed": False, "effective": not enabled()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["apply", "check", "clear"])
    args = parser.parse_args()
    try:
        result = {"apply": apply, "check": check, "clear": clear}[args.action]()
        print(json.dumps(result, indent=2))
        if args.action != "clear" and not result["effective"]:
            raise SystemExit(1)
    except Exception as exc:
        print(json.dumps({"effective": False, "error": str(exc)[:500]}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
