#!/usr/bin/env python3
"""Small client for Pocketpair's loopback-only REST API."""

import argparse
import base64
import json
import os
import urllib.error
import urllib.request


def request(endpoint: str, method: str = "GET", payload=None):
    port = os.environ.get("PALWORLD_REST_PORT", "8212")
    password = os.environ["PALWORLD_ADMIN_PASSWORD"]
    token = base64.b64encode(f"admin:{password}".encode()).decode()
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/api/{endpoint}", data=data, method=method,
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            raw = response.read()
            return json.loads(raw) if raw else {"ok": True}
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"REST {method} {endpoint} failed: HTTP {exc.code} {exc.read().decode(errors='replace')}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("endpoint", choices=["info", "players", "settings", "metrics", "save", "announce", "shutdown", "stop", "kick", "ban", "unban"])
    parser.add_argument("--message", default="")
    parser.add_argument("--wait", type=int, default=0)
    parser.add_argument("--userid", default="")
    args = parser.parse_args()
    method = "GET" if args.endpoint in {"info", "players", "settings", "metrics"} else "POST"
    payload = None
    if args.endpoint == "announce": payload = {"message": args.message}
    if args.endpoint == "shutdown": payload = {"waittime": args.wait, "message": args.message}
    if args.endpoint in {"kick", "ban", "unban"}:
        if not args.userid: parser.error(f"{args.endpoint} requires --userid")
        payload = {"userid": args.userid, "message": args.message}
    print(json.dumps(request(args.endpoint, method, payload), indent=2, sort_keys=True))


if __name__ == "__main__": main()
