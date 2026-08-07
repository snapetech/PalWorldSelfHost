#!/usr/bin/env python3
"""Minimal loopback-only Source RCON client for the Palworld server."""

import argparse
import json
import os
import socket
import struct


MAX_PACKET = 4 * 1024 * 1024
MAX_COMMAND_BYTES = 512


def validate_command(command):
    command = str(command).strip()
    if command.startswith("/"):
        command = command[1:].lstrip()
    if not command:
        raise ValueError("command is required")
    if any(character in command for character in "\r\n\0"):
        raise ValueError("command must be one line without NUL bytes")
    if len(command.encode("utf-8")) > MAX_COMMAND_BYTES:
        raise ValueError(f"command exceeds {MAX_COMMAND_BYTES} UTF-8 bytes")
    if command.split(None, 1)[0].casefold() == "adminpassword":
        raise ValueError("AdminPassword is forbidden because credentials must never enter console history")
    return command


def packet(request_id, packet_type, body):
    payload = struct.pack("<ii", request_id, packet_type) + body.encode("utf-8") + b"\0\0"
    return struct.pack("<i", len(payload)) + payload


def receive_exact(connection, length):
    result = bytearray()
    while len(result) < length:
        block = connection.recv(length - len(result))
        if not block:
            raise ConnectionError("RCON connection closed before the response completed")
        result.extend(block)
    return bytes(result)


def receive_packet(connection):
    size = struct.unpack("<i", receive_exact(connection, 4))[0]
    if size < 10 or size > MAX_PACKET:
        raise ValueError(f"invalid RCON response size {size}")
    payload = receive_exact(connection, size)
    request_id, packet_type = struct.unpack("<ii", payload[:8])
    if payload[-2:] != b"\0\0":
        raise ValueError("invalid RCON response terminator")
    return request_id, packet_type, payload[8:-2].decode("utf-8", errors="replace")


def execute(command, *, host="127.0.0.1", port=None, password=None, timeout=5):
    command = validate_command(command)
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("RCON client is restricted to loopback")
    port = int(port or os.environ.get("PALWORLD_RCON_PORT", "25575"))
    if not 1 <= port <= 65535:
        raise ValueError("RCON port must be between 1 and 65535")
    password = password if password is not None else os.environ.get("PALWORLD_ADMIN_PASSWORD", "")
    if not password:
        raise ValueError("PALWORLD_ADMIN_PASSWORD is not configured")

    with socket.create_connection((host, port), timeout=timeout) as connection:
        connection.settimeout(timeout)
        connection.sendall(packet(1, 3, password))
        authenticated = False
        for _ in range(3):
            request_id, packet_type, _ = receive_packet(connection)
            if request_id == -1:
                raise PermissionError("RCON authentication failed")
            if request_id == 1 and packet_type == 2:
                authenticated = True
                break
        if not authenticated:
            raise PermissionError("RCON authentication response was not received")

        connection.sendall(packet(2, 2, command))
        chunks = []
        for _ in range(32):
            try:
                request_id, _, body = receive_packet(connection)
            except (ConnectionError, TimeoutError, socket.timeout):
                if chunks:
                    break
                raise
            if request_id == -1:
                raise PermissionError("RCON command was rejected")
            # Palworld currently returns command responses with request ID 0
            # even when the Source-RCON command packet uses another ID.
            if request_id not in {0, 2}:
                continue
            chunks.append(body)
            # Palworld returns its documented command responses in one packet.
            # Briefly drain any immediately queued continuation packets, but a
            # quiet socket after a valid response is successful completion.
            connection.settimeout(0.05)
        return "".join(chunks).rstrip("\0")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command")
    parser.add_argument("--timeout", type=float, default=5)
    args = parser.parse_args()
    try:
        output = execute(args.command, timeout=max(0.5, min(args.timeout, 15)))
        print(json.dumps({"ok": True, "command": validate_command(args.command), "output": output}))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)[:500]}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
