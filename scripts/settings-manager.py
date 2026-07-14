#!/usr/bin/env python3
import argparse, importlib.util, json, os, pathlib, re, shutil, subprocess, time

here = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ops_lib", here / "ops-lib.py")
ops = importlib.util.module_from_spec(spec); spec.loader.exec_module(ops)
template = pathlib.Path(os.environ["PALWORLD_INSTALL_DIR"]) / "DefaultPalWorldSettings.ini"
overrides_file = ops.STATE / "settings-overrides.json"
protected = {"AdminPassword", "RESTAPIEnabled", "RESTAPIPort", "RCONEnabled", "RCONPort",
             "PublicPort", "PublicIP", "ServerName", "ServerDescription", "ServerPassword"}

def schema():
    text = template.read_text(); body = text.split("OptionSettings=(", 1)[1].rsplit(")", 1)[0]
    result = {}
    for match in re.finditer(r'(?:^|,)([A-Za-z][A-Za-z0-9_]*)=("[^"]*"|[^,]*)', body):
        key, raw = match.group(1), match.group(2).strip()
        if raw in {"True", "False"}: value, kind = raw == "True", "boolean"
        elif re.fullmatch(r"-?\d+", raw): value, kind = int(raw), "integer"
        elif re.fullmatch(r"-?(?:\d+\.?\d*|\.\d+)", raw): value, kind = float(raw), "number"
        else: value, kind = raw.strip('"'), "string"
        result[key] = {"type": kind, "default": value, "writable": key not in protected,
                       "restart_required": True}
    return result

parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True)
sub.add_parser("schema"); plan = sub.add_parser("plan"); plan.add_argument("updates")
apply = sub.add_parser("apply"); apply.add_argument("updates"); apply.add_argument("--confirm", default="")
rollback_parser = sub.add_parser("rollback"); rollback_parser.add_argument("--confirm", default="")
args = parser.parse_args(); known = schema(); current = ops.read_json(overrides_file, {})
if args.command == "schema": print(json.dumps({"settings": known, "overrides": current}, indent=2)); raise SystemExit()
if args.command in {"plan", "apply"}:
    updates = json.loads(args.updates); errors = []
    for key, value in updates.items():
        item = known.get(key)
        if not item: errors.append(f"unknown setting {key}")
        elif not item["writable"]: errors.append(f"protected setting {key}")
        elif item["type"] == "boolean" and not isinstance(value, bool): errors.append(f"{key} requires boolean")
        elif item["type"] in {"integer", "number"} and (not isinstance(value, (int, float)) or isinstance(value, bool)): errors.append(f"{key} requires number")
        elif item["type"] == "string" and not isinstance(value, str): errors.append(f"{key} requires string")
    preview = {"current": current, "updates": updates, "result": {**current, **updates}, "errors": errors,
               "restart_required": bool(updates)}
    if args.command == "plan": print(json.dumps(preview, indent=2)); raise SystemExit(bool(errors))
    if errors: raise SystemExit("; ".join(errors))
    if args.confirm != "APPLY SETTINGS": raise SystemExit("execution requires --confirm 'APPLY SETTINGS'")
    with ops.operation_lock():
        backup = ops.STATE / f"settings-overrides-{int(time.time())}.json"
        if overrides_file.exists(): shutil.copy2(overrides_file, backup)
        ops.atomic_json(overrides_file, preview["result"])
        subprocess.run([str(here / "start.sh"), "--render-only"], check=True)
        ops.audit("settings.apply", "ok", updates=updates, backup=str(backup))
        print(json.dumps({**preview, "backup": str(backup)}, indent=2))
elif args.command == "rollback":
    if args.confirm != "ROLLBACK SETTINGS": raise SystemExit("rollback requires --confirm 'ROLLBACK SETTINGS'")
    backups = sorted(ops.STATE.glob("settings-overrides-*.json"), reverse=True)
    if not backups: raise SystemExit("no settings rollback exists")
    with ops.operation_lock():
        shutil.copy2(backups[0], overrides_file); subprocess.run([str(here / "start.sh"), "--render-only"], check=True)
        ops.audit("settings.rollback", "ok", backup=str(backups[0])); print(backups[0])
