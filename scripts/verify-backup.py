#!/usr/bin/env python3
"""Validate a PalWorldSelfHost archive and optionally extract it safely."""
import argparse
import contextlib
import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


@contextlib.contextmanager
def open_bundle(archive):
    process = subprocess.Popen(
        ["zstd", "-dc", "--", str(archive)], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as bundle:
            yield bundle
        _, error = process.communicate(timeout=30)
        if process.returncode:
            raise ValueError(f"zstd decompression failed: {error.decode(errors='replace')[-500:]}")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def validate_member(member, maximum_bytes, total_bytes):
    pure = pathlib.PurePosixPath(member.name)
    if not member.name or pure.is_absolute() or ".." in pure.parts or "\\" in member.name:
        raise ValueError(f"unsafe archive path: {member.name!r}")
    allowed = pure == pathlib.PurePosixPath("DefaultPalWorldSettings.ini") or pure.parts[:2] == ("Pal", "Saved")
    if not allowed:
        raise ValueError(f"archive member is outside managed roots: {member.name!r}")
    if not (member.isdir() or member.isreg()):
        raise ValueError(f"archive contains a link or special file: {member.name!r}")
    if member.isreg():
        total_bytes += member.size
        if total_bytes > maximum_bytes:
            raise ValueError(f"archive expands beyond {maximum_bytes} bytes")
    return pure, total_bytes


def validate_archive(archive):
    maximum_members = int(os.environ.get("PALWORLD_BACKUP_MAX_MEMBERS", "1000000"))
    maximum_bytes = int(os.environ.get("PALWORLD_BACKUP_MAX_EXTRACT_BYTES", str(256 * 1024**3)))
    total_bytes = 0
    level_save = False
    seen = set()
    regular_files = set()
    with open_bundle(archive) as bundle:
        for index, member in enumerate(bundle, start=1):
            if index > maximum_members:
                raise ValueError(f"archive exceeds {maximum_members} members")
            pure, total_bytes = validate_member(member, maximum_bytes, total_bytes)
            if pure in seen:
                raise ValueError(f"archive contains a duplicate member: {member.name!r}")
            if any(parent in regular_files for parent in pure.parents):
                raise ValueError(f"archive member is below a regular file: {member.name!r}")
            if member.isreg() and any(existing != pure and pure in existing.parents for existing in seen):
                raise ValueError(f"archive regular file replaces a directory: {member.name!r}")
            seen.add(pure)
            if member.isreg():
                regular_files.add(pure)
            if len(pure.parts) >= 5 and pure.parts[:3] == ("Pal", "Saved", "SaveGames") and pure.name == "Level.sav":
                level_save = True
        if not level_save:
            raise ValueError("archive has no Pal/Saved/SaveGames world Level.sav")


def extract_archive(archive, target):
    maximum_bytes = int(os.environ.get("PALWORLD_BACKUP_MAX_EXTRACT_BYTES", str(256 * 1024**3)))
    total_bytes = 0
    with open_bundle(archive) as bundle:
        for member in bundle:
            pure, total_bytes = validate_member(member, maximum_bytes, total_bytes)
            destination = target.joinpath(*pure.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            source = bundle.extractfile(member)
            if source is None:
                raise ValueError(f"regular file has no content stream: {member.name!r}")
            with source, destination.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            os.chmod(destination, member.mode & 0o777)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=pathlib.Path)
    parser.add_argument("--extract-to", type=pathlib.Path)
    args = parser.parse_args()
    archive = args.archive.resolve()
    sidecar = pathlib.Path(str(archive) + ".sha256")
    if not archive.is_file() or not sidecar.is_file():
        raise SystemExit("archive or checksum sidecar is missing")
    fields = sidecar.read_text().split()
    if not fields or not re.fullmatch(r"[0-9a-fA-F]{64}", fields[0]):
        raise SystemExit(f"invalid checksum sidecar: {sidecar}")
    actual = digest(archive)
    if actual.lower() != fields[0].lower():
        raise SystemExit(f"checksum mismatch: {archive}")
    try:
        validate_archive(archive)
        if args.extract_to:
            target = args.extract_to.resolve()
            if not target.is_dir():
                raise ValueError(f"extraction target is not a directory: {target}")
            extract_archive(archive, target)
    except (ValueError, tarfile.TarError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"invalid backup archive: {exc}")
    print(f"verified: {archive}")


if __name__ == "__main__":
    main()
