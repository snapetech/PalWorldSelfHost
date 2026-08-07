#!/usr/bin/env python3
"""Inspect local listener exposure and optionally verify public game reachability."""

import importlib.util
import ipaddress
import json
import os
import pathlib
import subprocess
import time
import urllib.parse
import urllib.request


HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("ops_lib", HERE / "ops-lib.py")
ops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops)


def endpoint(value):
    value = value.strip()
    if value.startswith("[") and "]:" in value:
        address, port = value[1:].rsplit("]:", 1)
    elif ":" in value:
        address, port = value.rsplit(":", 1)
    else:
        return value, None
    try:
        return address, int(port)
    except ValueError:
        return address, None


def parse_listeners(output):
    listeners = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 5:
            continue
        address, port = endpoint(fields[4])
        if port is not None:
            listeners.append({"protocol": fields[0].lower(), "address": address, "port": port})
    return listeners


def loopback(address):
    if address in {"localhost"}:
        return True
    if address in {"*", "0.0.0.0", "::", "[::]"}:
        return False
    try:
        return ipaddress.ip_address(address.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def probe_public(host, port):
    probe_url = os.environ.get("PALWORLD_PUBLIC_PROBE_URL", "").strip()
    if not probe_url:
        return {"status": "unconfigured", "reachable": None, "detail": "external probe URL is not configured"}
    parsed = urllib.parse.urlparse(probe_url)
    allow_http = os.environ.get("PALWORLD_PUBLIC_PROBE_ALLOW_HTTP", "false").lower() == "true"
    if parsed.scheme != "https" and not (allow_http and parsed.scheme == "http"):
        return {"status": "invalid", "reachable": None, "detail": "probe URL must use HTTPS"}
    query = urllib.parse.urlencode({"host": host, "port": port, "protocol": "udp"})
    separator = "&" if parsed.query else "?"
    request = urllib.request.Request(probe_url + separator + query, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read(4097)
        if len(body) > 4096:
            raise ValueError("probe response exceeds 4 KiB")
        payload = json.loads(body)
        if not isinstance(payload.get("reachable"), bool):
            raise ValueError("probe response must contain boolean reachable")
        return {
            "status": "reachable" if payload["reachable"] else "unreachable",
            "reachable": payload["reachable"], "detail": str(payload.get("detail", ""))[:500],
        }
    except Exception as exc:
        return {"status": "error", "reachable": None, "detail": str(exc)[:500]}


def assess(listeners, public_ip, game_port, rest_port, ops_port, rcon_port,
           rcon_firewall=False, rest_firewall=False):
    issues = []
    channels = {}
    for name, protocol, port, must_loopback in (
        ("game", "udp", game_port, False),
        ("rest", "tcp", rest_port, True),
        ("operations", "tcp", ops_port, True),
        ("rcon", "tcp", rcon_port, True),
    ):
        matches = [item for item in listeners if item["protocol"].startswith(protocol) and item["port"] == port]
        unsafe = [item["address"] for item in matches if not loopback(item["address"])]
        channels[name] = {
            "port": port, "listening": bool(matches),
            "addresses": [item["address"] for item in matches], "non_loopback": unsafe,
        }
        if name == "rcon":
            channels[name]["firewall_protected"] = bool(rcon_firewall)
            channels[name]["effective_private"] = not unsafe or bool(rcon_firewall)
        if name == "rest":
            channels[name]["firewall_protected"] = bool(rest_firewall)
            channels[name]["effective_private"] = not unsafe or bool(rest_firewall)
        if name == "game" and not matches:
            issues.append(f"game UDP {port} is not listening")
        protected = (name == "rcon" and rcon_firewall) or (name == "rest" and rest_firewall)
        if must_loopback and unsafe and not protected:
            issues.append(f"{name} TCP {port} listens on a non-loopback address")
    public = probe_public(public_ip, game_port) if public_ip else {
        "status": "unconfigured", "reachable": None, "detail": "PALWORLD_PUBLIC_IP is empty",
    }
    if public.get("reachable") is False:
        issues.append("external probe reports the public game port unreachable")
    return {
        "checked_at": int(time.time()), "safe": not any("non-loopback" in issue for issue in issues),
        "ok": not issues, "reachability_proven": public.get("reachable") is not None,
        "issues": issues, "channels": channels, "public_game": public,
    }


def main():
    process = subprocess.run(["ss", "-H", "-lntu"], text=True, capture_output=True, timeout=15)
    if process.returncode:
        raise SystemExit((process.stdout + process.stderr).strip() or "ss failed")
    firewall = subprocess.run(
        ["sudo", "-n", str(HERE / "rcon-firewall.py"), "check"],
        text=True, capture_output=True, timeout=15,
    )
    try:
        firewall_status = json.loads(firewall.stdout)
        firewall_private = firewall.returncode == 0 and bool(firewall_status.get("effective_private"))
        rest_firewall_private = firewall.returncode == 0 and bool(firewall_status.get("rest_effective_private"))
    except json.JSONDecodeError:
        firewall_private = False
        rest_firewall_private = False
    result = assess(
        parse_listeners(process.stdout), os.environ.get("PALWORLD_PUBLIC_IP", "").strip(),
        int(os.environ.get("PALWORLD_PORT", "8211")), int(os.environ.get("PALWORLD_REST_PORT", "8212")),
        int(os.environ.get("PALWORLD_OPS_PORT", "8213")), int(os.environ.get("PALWORLD_RCON_PORT", "25575")),
        firewall_private,
        rest_firewall_private,
    )
    previous = ops.read_json(ops.STATE / "exposure.json", {})
    ops.atomic_json(ops.STATE / "exposure.json", result)
    if previous.get("issues") != result["issues"] or previous.get("public_game", {}).get("status") != result["public_game"]["status"]:
        ops.audit("exposure.transition", "ok" if result["ok"] else "failed", issues=result["issues"], public_status=result["public_game"]["status"])
        if any("non-loopback" in issue for issue in result["issues"]):
            ops.notify("control-plane-exposure", "; ".join(result["issues"]), "error")
    print(json.dumps(result, indent=2))
    raise SystemExit(not result["safe"])


if __name__ == "__main__":
    main()
