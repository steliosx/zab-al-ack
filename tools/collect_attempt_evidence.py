#!/usr/bin/env python3
"""Collect a lab attempt's records, diagnostics, manifests and service logs into a PRIVATE bundle, before cleanup.

Run it whatever the outcome (PASS, FAIL, REFUSED, NOT_RUN) and before any temporary directory, lab state or
checkout is removed. It copies every file that exists and records every expected file that does not. It never
copies key or password material: files whose names look like secrets are listed by name only. The bundle is
private. Its directories are mode 0700 and its files 0600, and a PRIVATE.txt marker sits at the top. It belongs
under the operator's private evidence directory, never in a Git checkout, GitHub or chat.

The verdict is COLLECTED only when every selected file that exists was copied and read back with the same
SHA-256. Anything else is INCOMPLETE and lists each failure (copy or destination error, a link anywhere on the
path, a path that resolves outside its source, an existing destination with different bytes). An INCOMPLETE
collection must not be followed by any cleanup that deletes the sources. Files are opened without following
links and checked against their source after opening; nothing already in the bundle is overwritten. A new
attempt gets a fresh directory (--new refuses an existing one); later collections into the same attempt may
add files but never replace one.

python3 tools/collect_attempt_evidence.py --bundle DIR --attempt LABEL [--new] [--checkout NAME=PATH]... [--tree NAME=PATH]...
    [--file NAME=PATH]...      exit 0 COLLECTED, 3 INCOMPLETE, 2 REFUSED

  --checkout  a lab checkout: its canonical and diagnostic records, retained phases (manifests and copies) and
              the lab state's logs, status and ownership records (CHECKOUT_PATTERNS)
  --tree      every regular file under a directory (for example a gateway's logs)
  --file      one file (for example a driver's result JSON)
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
from typing import Any

CHECKOUT_PATTERNS = (
    'evidence/deployment-e2e-run.json',
    'evidence/deployment-e2e-run.retention-failure.json',
    'build/private/retained-phases/**/*',
    '.deployment-lab/**/*.log',
    '.deployment-lab/**/*.json',
    '.zabbix-lab/*.log',
    '.zabbix-lab/*.json',
)
CHECKOUT_TOPS = ('evidence', 'build/private/retained-phases', '.deployment-lab', '.zabbix-lab')  # where links are refused
EXPECTED = ('evidence/deployment-e2e-run.json',)  # recorded as absent when missing (e.g. a refusal before any record)
SECRET_NAME = re.compile(r'(\.key|\.pem\.key|password|passwd|pwfile|secret|token|\.pgpass)', re.IGNORECASE)
MARKER = ('PRIVATE lab evidence. May contain service logs, host details and synthetic canary material.\n'
          'Keep under the operator\'s private evidence directory (mode 0700). Never commit, push or paste it.\n')
LABEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')


class CollectionError(Exception):
    pass


def attempt_label(prefix: str) -> str:
    """A collision-resistant attempt label: prefix, UTC second and 32 random bits."""
    return f"{prefix}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{secrets.token_hex(4)}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)


def candidates(kind: str, root: Path) -> list[Path]:
    if kind == 'file':
        return [root]
    if kind == 'tree':
        return sorted(path for path in root.rglob('*')) if root.is_dir() and not root.is_symlink() else []
    found: set[Path] = set()
    for pattern in CHECKOUT_PATTERNS:
        found.update(root.glob(pattern))
    return sorted(found)


def links_below(kind: str, root: Path) -> list[Path]:
    """Every link at or under the directories this source selects from; glob would silently skip linked directories."""
    tops = [root] if kind == 'tree' else [root / top for top in CHECKOUT_TOPS] if kind == 'checkout' else []
    found = []
    for top in tops:
        if top.is_symlink():
            found.append(top)
            continue
        for directory, dirnames, filenames in os.walk(top):
            found.extend(Path(directory) / name for name in [*dirnames, *filenames] if (Path(directory) / name).is_symlink())
    return found


def linked_component(root: Path, path: Path) -> Path | None:
    """The first link on the way from root (exclusive) to path (inclusive), if any."""
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            return current
    return None


def verified_copy(path: Path, container: Path, destination: Path) -> tuple[int, str, bool]:
    """Copy path, opened without following links and proven to lie under container, to destination without ever
    replacing an existing file. Returns (bytes, sha256, already_retained); raises CollectionError or OSError."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        opened = Path(os.readlink(f'/proc/self/fd/{fd}'))
        if container != opened and container not in opened.parents:
            raise CollectionError(f'opened file {opened} is outside {container}')
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise CollectionError('not a regular file once opened')
        temporary = destination.with_name(f'.{destination.name}.{secrets.token_hex(6)}.partial')
        out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        digest, size = hashlib.sha256(), 0
        try:
            with os.fdopen(out, 'wb') as sink:
                for block in iter(lambda: os.read(fd, 1 << 20), b''):
                    digest.update(block)
                    size += len(block)
                    sink.write(block)
                sink.flush()
                os.fsync(sink.fileno())
            if sha256(temporary) != digest.hexdigest():
                raise CollectionError('the copy does not read back with the source digest')
            try:
                os.link(temporary, destination)  # never replaces an existing file
            except FileExistsError:
                if sha256(destination) != digest.hexdigest():
                    raise CollectionError('a different file is already retained at ' + destination.name) from None
                return size, digest.hexdigest(), True
            return size, digest.hexdigest(), False
        finally:
            temporary.unlink(missing_ok=True)
    finally:
        os.close(fd)


def collect(bundle: Path, attempt: str, sources: list[tuple[str, str, Path]], new: bool = False) -> dict[str, Any]:
    """Copy what exists into bundle/attempt/NAME/..., merge MANIFEST.json, and return the manifest.

    manifest['complete'] is True only when no selected, existing file failed to be retained, across every
    collection into this attempt."""
    if not LABEL.fullmatch(attempt):
        raise CollectionError('attempt label must be 1-128 of [A-Za-z0-9._-]')
    bundle = bundle.resolve()
    for _, name, root in sources:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', name):
            raise CollectionError(f'source name {name!r} must be 1-64 of [A-Za-z0-9._-]')
        base = root.resolve()
        if bundle == base or base in bundle.parents:
            raise CollectionError(f'the bundle {bundle} is inside the collected source {base}')
    try:
        private_dir(bundle)
        marker = bundle / 'PRIVATE.txt'
        if not marker.exists():
            marker.write_text(MARKER)
            marker.chmod(0o600)
        target_root = bundle / attempt
        if new:
            target_root.mkdir(mode=0o700)  # FileExistsError: a new attempt never reuses a directory
        private_dir(target_root)
    except OSError as error:
        raise CollectionError(f'the bundle cannot be prepared: {type(error).__name__}: {error}') from error
    manifest_path = target_root / 'MANIFEST.json'
    manifest: dict[str, Any] = json.loads(manifest_path.read_text()) if manifest_path.exists() else {
        'attempt': attempt, 'private': True, 'collections': [], 'files': [], 'absent': [], 'excluded': [], 'failed': []}
    manifest.setdefault('failed', [])
    manifest['collections'].append({'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                                    'sources': [{'kind': kind, 'name': name, 'path': str(root)} for kind, name, root in sources]})
    retained = {(entry['source'], entry['path']) for entry in manifest['files']}
    for kind, name, root in sources:
        if kind == 'checkout':
            for relative in EXPECTED:
                if not (root / relative).exists():
                    manifest['absent'].append({'source': name, 'path': relative})
        elif not root.exists() and not root.is_symlink():
            manifest['absent'].append({'source': name, 'path': str(root)})
            continue
        base = root.parent if kind == 'file' else root
        if base.is_symlink() or (kind != 'file' and root.is_symlink()):
            manifest['failed'].append({'source': name, 'path': str(root), 'reason': 'the source root is a link'})
            continue
        container = base.resolve()
        links = links_below(kind, root)
        for link in links:
            manifest['failed'].append({'source': name, 'path': link.relative_to(root).as_posix(),
                                       'reason': f'link on the path, not followed: {link.relative_to(root)}'})
        for path in candidates(kind, root):
            relative = path.name if kind == 'file' else path.relative_to(root).as_posix()
            entry = {'source': name, 'path': relative}
            link = linked_component(base, path)
            if link is not None:
                if link not in links:  # not already reported above
                    manifest['failed'].append({**entry, 'reason': f'link on the path, not followed: {link.relative_to(base)}'})
                continue
            if not path.is_file():
                continue
            if SECRET_NAME.search(path.name):
                manifest['excluded'].append({**entry, 'reason': 'secret-like name; not copied'})
                continue
            destination = target_root / name / relative
            try:
                for parent in reversed(destination.relative_to(target_root).parents):
                    private_dir(target_root / parent)
                size, digest, already = verified_copy(path, container, destination)
            except (CollectionError, OSError) as error:
                manifest['failed'].append({**entry, 'reason': f'not retained: {type(error).__name__}: {error}'})
                continue
            if not already or (name, relative) not in retained:
                manifest['files'].append({**entry, 'bytes': size, 'sha256': digest,
                                          'bundle_path': destination.relative_to(bundle).as_posix()})
                retained.add((name, relative))
    manifest['complete'] = not manifest['failed']
    staging = manifest_path.with_name('.MANIFEST.json.partial')
    staging.write_text(json.dumps(manifest, indent=2) + '\n')
    staging.chmod(0o600)
    os.replace(staging, manifest_path)
    return manifest


def verdict(manifest: dict[str, Any]) -> str:
    return 'COLLECTED' if manifest.get('complete') else 'INCOMPLETE'


def parse(values: list[str], kind: str) -> list[tuple[str, str, Path]]:
    parsed = []
    for value in values:
        name, separator, path = value.partition('=')
        if not separator or not path:
            raise CollectionError(f'--{kind} takes NAME=PATH, not {value!r}')
        parsed.append((kind, name, Path(path)))
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--attempt', required=True)
    parser.add_argument('--new', action='store_true', help='refuse an attempt directory that already exists')
    parser.add_argument('--checkout', action='append', default=[])
    parser.add_argument('--tree', action='append', default=[])
    parser.add_argument('--file', action='append', default=[])
    args = parser.parse_args(argv)
    try:
        sources = parse(args.checkout, 'checkout') + parse(args.tree, 'tree') + parse(args.file, 'file')
        manifest = collect(args.bundle, args.attempt, sources, new=args.new)
    except CollectionError as error:
        print(json.dumps({'status': 'REFUSED', 'reason': str(error)}))
        return 2
    status = verdict(manifest)
    print(json.dumps({'status': status, 'bundle': str(args.bundle.resolve() / args.attempt), 'files': len(manifest['files']),
                      'absent': len(manifest['absent']), 'excluded': len(manifest['excluded']), 'failed': manifest['failed']}))
    return 0 if status == 'COLLECTED' else 3


if __name__ == '__main__':
    sys.exit(main())
