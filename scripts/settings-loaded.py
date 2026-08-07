#!/usr/bin/env python3
"""Record that the current rendered settings reached a running service."""

import importlib.util
import pathlib
import time


HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("ops_lib", HERE / "ops-lib.py")
ops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops)

pending = ops.read_json(ops.STATE / "settings-restart-required.json", {})
if pending:
    ops.atomic_json(ops.STATE / "settings-last-loaded.json", {
        **pending, "loaded_at": int(time.time()),
    })
    (ops.STATE / "settings-restart-required.json").unlink(missing_ok=True)
    ops.audit("settings.loaded", "ok", source=pending.get("source"))
server_pending = ops.read_json(ops.STATE / "server-config-restart-required.json", {})
if server_pending:
    ops.atomic_json(ops.STATE / "server-config-last-loaded.json", {**server_pending, "loaded_at": int(time.time())})
    (ops.STATE / "server-config-restart-required.json").unlink(missing_ok=True)
    ops.audit("server-config.loaded", "ok", source=server_pending.get("source"))
