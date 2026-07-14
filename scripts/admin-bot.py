#!/usr/bin/env python3
"""Optional allowlisted Matrix command bot; uses only the standard library."""
import importlib.util, json, os, pathlib, subprocess, time, urllib.parse, urllib.request
here=pathlib.Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("ops_lib",here/"ops-lib.py"); ops=importlib.util.module_from_spec(spec); spec.loader.exec_module(ops)
base=os.environ.get("PALWORLD_MATRIX_HOMESERVER","").rstrip("/"); token=os.environ.get("PALWORLD_MATRIX_ACCESS_TOKEN","")
rooms=set(filter(None,os.environ.get("PALWORLD_MATRIX_ROOMS","").split(","))); users=set(filter(None,os.environ.get("PALWORLD_MATRIX_ADMINS","").split(",")))
if not (base and token and rooms and users): raise SystemExit("Matrix bot is not configured")
def request(method,path,payload=None):
    data=None if payload is None else json.dumps(payload).encode(); req=urllib.request.Request(base+path,data=data,method=method,headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=40) as response:return json.load(response)
def send(room,text):
    txn=str(time.time_ns()); request("PUT",f"/_matrix/client/v3/rooms/{urllib.parse.quote(room,safe='')}/send/m.room.message/{txn}",{"msgtype":"m.text","body":text[:4000]})
def command(text):
    parts=text.strip().split(maxsplit=1); cmd=parts[0].lower(); arg=parts[1] if len(parts)>1 else ""
    table={"!status":[str(here/"rest-client.py"),"info"],"!players":[str(here/"rest-client.py"),"players"],"!save":[str(here/"rest-client.py"),"save"],"!backup":["sudo","-n",str(here/"backup.sh"),"daily"],"!restart":["sudo","-n",str(here/"graceful-restart.sh")]}
    if cmd=="!announce" and arg: argv=[str(here/"rest-client.py"),"announce","--message",arg]
    elif cmd in table: argv=table[cmd]
    else:return "Commands: !status !players !save !backup !restart !announce TEXT"
    p=subprocess.run(argv,text=True,capture_output=True,timeout=3600); ops.audit("bot."+cmd[1:],"ok" if p.returncode==0 else "failed"); return (p.stdout+p.stderr).strip() or ("Completed" if p.returncode==0 else "Failed")
since=""
while True:
    try:
        data=request("GET","/_matrix/client/v3/sync?timeout=30000"+("&since="+urllib.parse.quote(since) if since else "")); since=data["next_batch"]
        for room,body in data.get("rooms",{}).get("join",{}).items():
            if room not in rooms: continue
            for event in body.get("timeline",{}).get("events",[]):
                if event.get("type")=="m.room.message" and event.get("sender") in users and event.get("content",{}).get("body","").startswith("!"): send(room,command(event["content"]["body"]))
    except Exception as exc: ops.audit("bot.poll","failed",error=str(exc)); time.sleep(10)
