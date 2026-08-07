#!/usr/bin/env python3
"""Constrained Palworld file browser/editor with preservation-first writes."""

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import pathlib
import re
import shutil
import tempfile
import time


MAX_TEXT_BYTES = 1024 * 1024
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 2000
TEXT_EXTENSIONS = {".ini", ".json", ".txt", ".cfg", ".lua", ".log", ".md", ".yaml", ".yml"}
UPLOAD_EXTENSIONS = TEXT_EXTENSIONS | {".pak", ".ucas", ".utoc"}
WRITABLE_ROOTS = {"config", "mods"}


def paths():
    install = pathlib.Path(os.environ.get("PALWORLD_INSTALL_DIR", "/srv/palworld/server")).resolve()
    state = pathlib.Path(os.environ.get("PALWORLD_STATE_DIR", "/var/lib/palworld")).resolve()
    return {
        "config": install / "Pal" / "Saved" / "Config" / ("WindowsServer" if os.environ.get("PALWORLD_RUNTIME") == "wine-windows" else "LinuxServer"),
        "mods": install / "Pal" / "Content" / "Paks",
        "logs": install / "Pal" / "Saved" / "Logs",
    }, state


def clean_relative(value):
    if not isinstance(value, str) or len(value) > 500 or "\0" in value or "\\" in value:
        raise ValueError("path must be a relative POSIX path of at most 500 characters")
    path = pathlib.PurePosixPath(value or ".")
    if path.is_absolute() or any(part in {"", ".."} for part in path.parts):
        raise ValueError("absolute and parent paths are forbidden")
    return path


def resolve(root_name, relative, *, must_exist=False):
    roots, _ = paths()
    if root_name not in roots:
        raise ValueError("unknown file root")
    base = roots[root_name]
    pure = clean_relative(relative)
    candidate = base.joinpath(*pure.parts)
    current = base
    if base.exists() and base.is_symlink():
        raise ValueError("managed root may not be a symlink")
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("symlink traversal is forbidden")
    resolved_parent = (candidate if candidate == base else candidate.parent).resolve(strict=False)
    try:
        resolved_parent.relative_to(base.resolve(strict=False))
    except ValueError as exc:
        raise ValueError("path escapes managed root") from exc
    if must_exist and not candidate.exists():
        raise ValueError("file does not exist")
    return base, candidate


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def listing(root_name, relative="."):
    _, directory = resolve(root_name, relative, must_exist=True)
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("path is not a directory")
    rows = []
    for count, entry in enumerate(sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold())), 1):
        if count > MAX_ENTRIES:
            raise ValueError("directory exceeds the 2,000-entry safety bound")
        stat = entry.lstat()
        kind = "symlink" if entry.is_symlink() else ("directory" if entry.is_dir() else ("file" if entry.is_file() else "other"))
        rows.append({"name": entry.name, "kind": kind, "bytes": stat.st_size if kind == "file" else None,
                     "mtime": int(stat.st_mtime), "writable": root_name in WRITABLE_ROOTS and kind in {"file", "directory"}})
    return {"root": root_name, "path": str(clean_relative(relative)), "writable": root_name in WRITABLE_ROOTS,
            "entries": rows, "truncated": False}


def read_text(root_name, relative):
    _, target = resolve(root_name, relative, must_exist=True)
    if not target.is_file() or target.is_symlink() or target.suffix.casefold() not in TEXT_EXTENSIONS:
        raise ValueError("only allowlisted regular text files can be read")
    if target.stat().st_size > MAX_TEXT_BYTES:
        raise ValueError("text file exceeds the 1 MiB safety bound")
    try:
        content = target.read_text()
    except UnicodeDecodeError as exc:
        raise ValueError("file is not UTF-8 text") from exc
    return {"root": root_name, "path": str(clean_relative(relative)), "bytes": target.stat().st_size,
            "sha256": sha256(target), "content": content, "writable": root_name in WRITABLE_ROOTS}


def plan_upload(root_name, relative, source):
    if root_name not in WRITABLE_ROOTS:
        raise ValueError("root is read-only")
    _, target = resolve(root_name, relative)
    source = pathlib.Path(source)
    if not source.is_file() or source.is_symlink():
        raise ValueError("upload source must be a regular file")
    size = source.stat().st_size
    if size > MAX_UPLOAD_BYTES:
        raise ValueError("upload exceeds the 64 MiB safety bound")
    extension = target.suffix.casefold()
    if extension not in UPLOAD_EXTENSIONS:
        raise ValueError("file extension is not allowlisted")
    if extension in TEXT_EXTENSIONS and size > MAX_TEXT_BYTES:
        raise ValueError("text upload exceeds the 1 MiB safety bound")
    before = {"exists": target.exists()}
    if target.exists():
        if not target.is_file() or target.is_symlink():
            raise ValueError("target is not a replaceable regular file")
        before.update({"bytes": target.stat().st_size, "sha256": sha256(target)})
    return {"root": root_name, "path": str(clean_relative(relative)), "before": before,
            "after": {"bytes": size, "sha256": sha256(source)},
            "restart_required": root_name in {"config", "mods"}, "steps": [
                "revalidate path and source hash", "preserve replaced file", "atomically install upload",
                "leave game restart to a separate explicit operator action",
            ]}


def preserve(root_name, relative, target):
    _, state = paths()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets_token()[:8]
    destination = state / "file-manager-backups" / stamp / root_name / pathlib.PurePosixPath(relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, destination)
    return destination


def upload(root_name, relative, source, expected_sha, confirm):
    if confirm != "UPLOAD FILE":
        raise ValueError("confirmation required")
    _, state = paths()
    state.mkdir(parents=True, exist_ok=True)
    with (state / "file-manager.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        plan = plan_upload(root_name, relative, source)
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha or "") or plan["after"]["sha256"] != expected_sha:
            raise ValueError("upload SHA-256 does not match reviewed content")
        _, target = resolve(root_name, relative)
        preserved = preserve(root_name, relative, target) if target.exists() else None
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as output, open(source, "rb") as incoming:
                shutil.copyfileobj(incoming, output, 1024 * 1024); output.flush(); os.fsync(output.fileno())
            os.chmod(temporary, target.stat().st_mode & 0o777 if target.exists() else 0o640)
            os.replace(temporary, target)
        finally:
            with contextlib.suppress(FileNotFoundError): os.unlink(temporary)
        if sha256(target) != expected_sha:
            if preserved: shutil.copy2(preserved, target)
            else: target.unlink(missing_ok=True)
            raise ValueError("installed file failed post-write SHA-256 verification")
    return {**plan, "status": "installed", "preserved": str(preserved) if preserved else None}


def quarantine(root_name, relative, confirm):
    if confirm != "QUARANTINE FILE":
        raise ValueError("confirmation required")
    if root_name not in WRITABLE_ROOTS:
        raise ValueError("root is read-only")
    _, state = paths()
    state.mkdir(parents=True, exist_ok=True)
    with (state / "file-manager.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _, target = resolve(root_name, relative, must_exist=True)
        if not target.is_file() or target.is_symlink():
            raise ValueError("only regular files can be quarantined")
        destination = state / "file-manager-quarantine" / secrets_token() / root_name / clean_relative(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(target, destination)
    return {"status": "quarantined", "root": root_name, "path": str(clean_relative(relative)),
            "quarantine": str(destination), "restart_required": True}


def secrets_token():
    return hashlib.sha256(os.urandom(32)).hexdigest()[:20]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("list", "read"):
        item = sub.add_parser(command); item.add_argument("root"); item.add_argument("path", nargs="?", default=".")
    for command in ("plan", "upload"):
        item = sub.add_parser(command); item.add_argument("root"); item.add_argument("path"); item.add_argument("source")
        if command == "upload": item.add_argument("--sha256", required=True); item.add_argument("--confirm", default="")
    item = sub.add_parser("quarantine"); item.add_argument("root"); item.add_argument("path"); item.add_argument("--confirm", default="")
    args = parser.parse_args()
    try:
        if args.command == "list": result = listing(args.root, args.path)
        elif args.command == "read": result = read_text(args.root, args.path)
        elif args.command == "plan": result = plan_upload(args.root, args.path, args.source)
        elif args.command == "upload": result = upload(args.root, args.path, args.source, args.sha256, args.confirm)
        else: result = quarantine(args.root, args.path, args.confirm)
        print(json.dumps(result, indent=2))
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__":
    main()
