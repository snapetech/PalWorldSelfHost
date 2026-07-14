#!/usr/bin/env python3
import argparse, importlib.util, pathlib

path = pathlib.Path(__file__).with_name("ops-lib.py")
spec = importlib.util.spec_from_file_location("ops_lib", path)
ops = importlib.util.module_from_spec(spec); spec.loader.exec_module(ops)
parser = argparse.ArgumentParser()
sub = parser.add_subparsers(dest="command", required=True)
phase = sub.add_parser("phase"); phase.add_argument("name"); phase.add_argument("--details", default="")
event = sub.add_parser("audit"); event.add_argument("action"); event.add_argument("result"); event.add_argument("--details", default="")
notice = sub.add_parser("notify"); notice.add_argument("event"); notice.add_argument("message"); notice.add_argument("--severity", default="info")
args = parser.parse_args()
if args.command == "phase": ops.maintenance_phase(args.name, message=args.details)
elif args.command == "audit": ops.audit(args.action, args.result, message=args.details)
else: ops.notify(args.event, args.message, args.severity)
