#!/usr/bin/env python3
"""Package a handoff or evidence directory with a verifiable manifest.

`build` writes `SHA256SUMS` inside the directory listing every regular file
except the manifest itself, verifies each entry against the bytes on disk,
creates `<name>.tar.gz` next to the directory and a detached `<name>.tar.gz.sha256`.
`verify` re-reads a directory's manifest (every entry must match, every file
must be listed, the manifest must not list itself) or an archive against its
detached checksum. Symlinks and special files are refused, so a bundle never
carries a path that points outside itself.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import stat
import sys
import tarfile

MANIFEST = 'SHA256SUMS'


class BundleError(ValueError):
    pass


def digest(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def regular_files(directory: Path) -> list[Path]:
    files = []
    for root, dirs, names in os.walk(directory):
        dirs.sort()
        for name in sorted(names):
            path = Path(root) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise BundleError('symlink refused: ' + path.relative_to(directory).as_posix())
            if not stat.S_ISREG(info.st_mode):
                raise BundleError('special file refused: ' + path.relative_to(directory).as_posix())
            files.append(path)
    return files


def write_manifest(directory: Path) -> Path:
    """List every file except the manifest; the listing is taken before the manifest exists."""
    manifest = directory / MANIFEST
    manifest.unlink(missing_ok=True)
    entries = [(digest(path), path.relative_to(directory).as_posix()) for path in regular_files(directory)]
    if not entries:
        raise BundleError('nothing to package')
    manifest.write_text(''.join(f'{value}  {name}\n' for value, name in entries))
    verify_directory(directory)
    return manifest


def read_manifest(manifest: Path) -> dict[str, str]:
    entries = {}
    for number, line in enumerate(manifest.read_text().splitlines(), 1):
        if not line.strip():
            continue
        value, sep, name = line.partition('  ')
        if not sep or len(value) != 64 or not name or name.startswith('/') or '..' in name.split('/'):
            raise BundleError(f'malformed manifest line {number}')
        if name in entries:
            raise BundleError('duplicate manifest entry: ' + name)
        entries[name] = value
    return entries


def verify_directory(directory: Path) -> dict[str, str]:
    manifest = directory / MANIFEST
    if not manifest.is_file():
        raise BundleError('manifest missing')
    entries = read_manifest(manifest)
    if MANIFEST in entries:
        raise BundleError('manifest lists itself')
    present = {path.relative_to(directory).as_posix() for path in regular_files(directory)} - {MANIFEST}
    missing = sorted(set(entries) - present)
    unlisted = sorted(present - set(entries))
    if missing:
        raise BundleError('listed but absent: ' + ', '.join(missing))
    if unlisted:
        raise BundleError('present but unlisted: ' + ', '.join(unlisted))
    for name, value in entries.items():
        if digest(directory / name) != value:
            raise BundleError('digest mismatch: ' + name)
    return entries


def build(directory: Path, output: Path | None = None) -> dict:
    directory = directory.resolve()
    if not directory.is_dir():
        raise BundleError('not a directory')
    write_manifest(directory)
    archive = output or directory.parent / (directory.name + '.tar.gz')
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(directory, arcname=directory.name, recursive=True, filter=lambda info: info if info.isfile() or info.isdir() else None)
    value = digest(archive)
    detached = Path(str(archive) + '.sha256')
    detached.write_text(f'{value}  {archive.name}\n')
    return {'archive': str(archive), 'sha256': value, 'detached': str(detached), 'files': len(read_manifest(directory / MANIFEST))}


def verify_archive(archive: Path) -> dict:
    detached = Path(str(archive) + '.sha256')
    if not detached.is_file():
        raise BundleError('detached checksum missing')
    expected, _, name = detached.read_text().strip().partition('  ')
    if name != archive.name or len(expected) != 64:
        raise BundleError('detached checksum malformed or for another file')
    actual = digest(archive)
    if actual != expected:
        raise BundleError('archive digest mismatch')
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        for member in members:
            if not (member.isfile() or member.isdir()) or member.name.startswith('/') or '..' in member.name.split('/'):
                raise BundleError('unsafe archive member: ' + member.name)
        manifests = [m for m in members if m.isfile() and m.name.split('/')[1:] == [MANIFEST]]
        if len(manifests) != 1:
            raise BundleError('archive must contain exactly one top-level manifest')
        root = manifests[0].name.split('/')[0]
        entries = {}
        for number, line in enumerate(tar.extractfile(manifests[0]).read().decode().splitlines(), 1):
            value, sep, entry = line.partition('  ')
            if not sep or len(value) != 64:
                raise BundleError(f'malformed manifest line {number}')
            entries[entry] = value
        if MANIFEST in entries:
            raise BundleError('manifest lists itself')
        files = {m.name[len(root) + 1:]: m for m in members if m.isfile() and m.name != manifests[0].name}
        if set(files) != set(entries):
            raise BundleError('archive contents differ from the manifest')
        for entry, value in entries.items():
            if hashlib.sha256(tar.extractfile(files[entry]).read()).hexdigest() != value:
                raise BundleError('digest mismatch inside archive: ' + entry)
    return {'archive': str(archive), 'sha256': actual, 'files': len(entries)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    builder = sub.add_parser('build')
    builder.add_argument('directory', type=Path)
    builder.add_argument('--output', type=Path, default=None)
    verifier = sub.add_parser('verify')
    verifier.add_argument('target', type=Path, help='a directory with SHA256SUMS or a .tar.gz with its .sha256')
    args = parser.parse_args(argv)
    try:
        if args.command == 'build':
            result = build(args.directory, args.output)
        elif args.target.is_dir():
            result = {'directory': str(args.target), 'files': len(verify_directory(args.target))}
        else:
            result = verify_archive(args.target)
    except (BundleError, OSError, tarfile.TarError) as error:
        print(f'{type(error).__name__}: {error}')
        return 1
    print(result)
    return 0


if __name__ == '__main__':
    sys.exit(main())
