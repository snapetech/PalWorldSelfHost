#!/usr/bin/env python3
"""Assess Palworld health and perform bounded, loop-protected recovery."""

import importlib.util
import json
import os
import pathlib
import socket
import subprocess
import time


HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("ops_lib", HERE / "ops-lib.py")
ops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops)
RECOVERY_STATE = ops.STATE / "health-recovery.json"


def env_int(name, default, minimum=0, maximum=1_000_000):
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def recovery_decision(now, state, *, service_active, rest_ok, uptime, rss_bytes, players_online):
    state = dict(state or {})
    threshold = env_int("PALWORLD_STALL_FAILURE_THRESHOLD", 3, 2, 12)
    prior_uptime = state.get("last_metrics_uptime")
    prior_check = state.get("last_checked_at")
    stalled_sample = bool(
        service_active and rest_ok and uptime is not None and prior_uptime is not None
        and prior_check and now - prior_check >= 60 and uptime <= prior_uptime
    )
    if service_active and (not rest_ok or stalled_sample):
        state["stall_streak"] = int(state.get("stall_streak", 0)) + 1
    else:
        state["stall_streak"] = 0
    if rest_ok and uptime is not None:
        state["last_metrics_uptime"] = uptime
    state["last_checked_at"] = now

    reason = None
    if service_active and state["stall_streak"] >= threshold:
        reason = "REST unavailable" if not rest_ok else "world uptime stopped advancing"

    memory_gib = env_int("PALWORLD_MEMORY_RESTART_GIB", 0, 0, 1024)
    memory_limit = memory_gib * 1024**3
    memory_exceeded = bool(service_active and memory_limit and rss_bytes >= memory_limit)
    if not reason and memory_exceeded:
        defer_players = os.environ.get("PALWORLD_MEMORY_RESTART_DEFER_PLAYERS", "true").lower() == "true"
        if defer_players and players_online is None:
            return {
                "action": "defer", "reason": f"RSS reached {rss_bytes / 1024**3:.1f} GiB; player roster unavailable",
                "players_online": None,
            }, state
        if players_online and defer_players:
            return {
                "action": "defer", "reason": f"RSS reached {rss_bytes / 1024**3:.1f} GiB",
                "players_online": players_online,
            }, state
        reason = f"RSS reached {rss_bytes / 1024**3:.1f} GiB"

    if not reason:
        return {"action": "none"}, state
    if os.environ.get("PALWORLD_HEALTH_RECOVERY_ENABLED", "true").lower() != "true":
        return {"action": "disabled", "reason": reason}, state

    cooldown = env_int("PALWORLD_RECOVERY_COOLDOWN_MINUTES", 30, 5, 1440) * 60
    if now - int(state.get("last_recovery_at", 0)) < cooldown:
        return {"action": "cooldown", "reason": reason}, state
    window = env_int("PALWORLD_RECOVERY_WINDOW_HOURS", 6, 1, 168) * 3600
    maximum = env_int("PALWORLD_RECOVERY_MAX_RESTARTS", 3, 1, 20)
    recoveries = [int(item) for item in state.get("recoveries", []) if int(item) >= now - window]
    state["recoveries"] = recoveries
    if len(recoveries) >= maximum:
        return {"action": "blocked", "reason": reason, "recoveries": len(recoveries)}, state
    return {
        "action": "restart", "reason": reason,
        "mode": "forced" if state["stall_streak"] >= threshold else "graceful",
    }, state


def rest_json(endpoint):
    process = subprocess.run(
        [str(HERE / "rest-client.py"), endpoint], text=True, capture_output=True, timeout=15,
    )
    if process.returncode:
        return None
    try:
        return json.loads(process.stdout)
    except json.JSONDecodeError:
        return None


def service_properties():
    process = subprocess.run(
        ["systemctl", "show", "palworld.service", "-p", "ActiveState", "-p", "MainPID", "-p", "NRestarts"],
        text=True, capture_output=True,
    )
    return dict(line.split("=", 1) for line in process.stdout.splitlines() if "=" in line)


def process_rss(pid):
    if not pid:
        return 0
    try:
        fields = pathlib.Path(f"/proc/{pid}/stat").read_text().split()
        return int(fields[23]) * os.sysconf("SC_PAGE_SIZE")
    except (FileNotFoundError, IndexError, ValueError):
        return 0


def perform_recovery(decision):
    try:
        with ops.operation_lock(blocking=False):
            if decision["mode"] == "graceful":
                process = subprocess.run(
                    [str(HERE / "graceful-restart.sh"), "Memory threshold reached; server restart is required."],
                    text=True, capture_output=True, timeout=600,
                    env={**os.environ, "PALWORLD_LOCK_HELD": "true"},
                )
            else:
                process = subprocess.run(
                    ["systemctl", "restart", "palworld.service"],
                    text=True, capture_output=True, timeout=180,
                )
    except BlockingIOError:
        return False, "another world mutation is in progress"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return process.returncode == 0, (process.stdout + process.stderr).strip()[-2000:]


def main():
    now = int(time.time())
    backup_root = pathlib.Path(os.environ["PALWORLD_BACKUP_LOCAL_ROOT"])
    max_age = env_int("PALWORLD_BACKUP_MAX_AGE_HOURS", 26, 1, 8760) * 3600
    issues = []
    properties = service_properties()
    active = properties.get("ActiveState") == "active"
    if not active:
        issues.append("service is not active")
    restarts = int(properties.get("NRestarts", "0") or 0)
    crash_limit = env_int("PALWORLD_CRASH_LIMIT", 5, 1, 100)
    if restarts >= crash_limit:
        issues.append(f"crash-loop guard: {restarts} automatic restarts")

    game_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        game_socket.bind(("127.0.0.1", env_int("PALWORLD_PORT", 8211, 1, 65535)))
    except OSError:
        pass
    else:
        issues.append("UDP game port is not occupied")
    finally:
        game_socket.close()

    backups = sorted(backup_root.glob("palworld-*.tar.zst"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not backups:
        issues.append("no local backup exists")
    elif time.time() - backups[0].stat().st_mtime > max_age:
        issues.append("local backup is stale")

    metrics = rest_json("metrics") if active else None
    if active and metrics is None:
        issues.append("REST API health check failed")
    pid = int(properties.get("MainPID", "0") or 0)
    rss = process_rss(pid)
    memory_limit = env_int("PALWORLD_MEMORY_RESTART_GIB", 0, 0, 1024) * 1024**3
    player_payload = rest_json("players") if memory_limit and rss >= memory_limit and metrics is not None else None
    player_count = len(player_payload.get("players", [])) if player_payload is not None else None
    recovery_state = ops.read_json(RECOVERY_STATE, {})
    decision, recovery_state = recovery_decision(
        now, recovery_state, service_active=active, rest_ok=metrics is not None,
        uptime=metrics.get("uptime") if metrics else None, rss_bytes=rss, players_online=player_count,
    )
    recovery = {**decision, "attempted_at": now}
    if decision["action"] == "restart":
        succeeded, detail = perform_recovery(decision)
        recovery.update({"ok": succeeded, "detail": detail})
        ops.audit("health.recovery", "ok" if succeeded else "failed", **recovery)
        ops.notify(
            "health-recovery" if succeeded else "health-recovery-failed",
            f"Palworld recovery {'started' if succeeded else 'failed'}: {decision['reason']}",
            "info" if succeeded else "error",
        )
        if succeeded:
            recovery_state["last_recovery_at"] = now
            recovery_state.setdefault("recoveries", []).append(now)
            recovery_state["stall_streak"] = 0
        else:
            issues.append(f"automatic recovery failed: {detail or decision['reason']}")
    elif decision["action"] not in {"none"}:
        issues.append(f"automatic recovery {decision['action']}: {decision.get('reason', '')}")
        if decision["action"] in {"blocked", "disabled"}:
            ops.audit("health.recovery", decision["action"], **recovery)
    ops.atomic_json(RECOVERY_STATE, recovery_state)

    state_path = ops.STATE / "health.json"
    previous = ops.read_json(state_path, {})
    payload = {
        "checked_at": now, "ok": not issues, "issues": issues,
        "service": {"active": active, "pid": pid, "restarts": restarts, "rss_bytes": rss},
        "rest": {"ok": metrics is not None, "stall_streak": recovery_state.get("stall_streak", 0)},
        "recovery": recovery,
    }
    ops.atomic_json(state_path, payload)
    if previous.get("ok") is not None and previous.get("ok") != (not issues):
        ops.notify(
            "Palworld recovered" if not issues else "Palworld health failure",
            "; ".join(issues) or "All health checks pass", "info" if not issues else "error",
        )
        ops.audit("health.transition", "ok" if not issues else "failed", issues=issues)
    if issues:
        raise SystemExit("Palworld health failure on " + socket.gethostname() + ": " + "; ".join(issues))


if __name__ == "__main__":
    main()
