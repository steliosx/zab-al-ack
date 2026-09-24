#!/usr/bin/env python3
"""Collect a lab attempt's records, diagnostics, manifests and service logs into a PRIVATE bundle, before cleanup.

Run it whatever the outcome (PASS, FAIL, REFUSED, NOT_RUN) and before any temporary directory, lab state or
checkout is removed. It copies every file that exists and records every expected file that does not. It never
copies key or password material: files whose names look like secrets are listed by name only. The bundle is
private. Its directories are mode 0700 and its files 0600, and a PRIVATE.txt marker sits at the top. It belongs
under the operator's private evidence directory, never in a Git checkout, GitHub or chat.

python3 tools/collect_attempt_evidence.py --bundle DIR --attempt LABEL [--checkout NAME=PATH]... [--tree NAME=PATH]...
    [--file NAME=PATH]...

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
import shutil
import sys
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
EXPECTED = ('evidence/deployment-e2e-run.json',)  # recorded as absent when missing (e.g. a refusal before any record)
SECRET_NAME = re.compile(r'(\.key|\.pem\.key|password|passwd|pwfile|secret|token|\.pgpass)', re.IGNORECASE)
LIMIT = 64 * 1024 * 1024
MARKER = ('PRIVATE lab evidence. May contain service logs, host details and synthetic canary material.\n'
          'Keep under the operator\'s private evidence directory (mode 0700). Never commit, push or paste it.\n')


class CollectionError(Exception):
    pass


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
        return sorted(path for path in root.rglob('*')) if root.is_dir() else []
    found: set[Path] = set()
    for pattern in CHECKOUT_PATTERNS:
        found.update(root.glob(pattern))
    return sorted(found)


def collect(bundle: Path, attempt: str, sources: list[tuple[str, str, Path]]) -> dict[str, Any]:
    """Copy what exists into bundle/attempt/NAME/..., merge MANIFEST.json, and return the manifest."""
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', attempt):
        raise CollectionError('attempt label must be 1-128 of [A-Za-z0-9._-]')
    bundle = bundle.resolve()
    for _, name, root in sources:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', name):
            raise CollectionError(f'source name {name!r} must be 1-64 of [A-Za-z0-9._-]')
        base = root.resolve()
        if bundle == base or base in bundle.parents:
            raise CollectionError(f'the bundle {bundle} is inside the collected source {base}')
    private_dir(bundle)
    (bundle / 'PRIVATE.txt').write_text(MARKER)
    (bundle / 'PRIVATE.txt').chmod(0o600)
    target_root = bundle / attempt
    private_dir(target_root)
    manifest_path = target_root / 'MANIFEST.json'
    manifest: dict[str, Any] = json.loads(manifest_path.read_text()) if manifest_path.exists() else {
        'attempt': attempt, 'private': True, 'collections': [], 'files': [], 'absent': [], 'excluded': []}
    manifest['collections'].append({'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                                    'sources': [{'kind': kind, 'name': name, 'path': str(root)} for kind, name, root in sources]})
    for kind, name, root in sources:
        if kind == 'checkout':
            for relative in EXPECTED:
                if not (root / relative).exists():
                    manifest['absent'].append({'source': name, 'path': relative})
        elif not root.exists():
            manifest['absent'].append({'source': name, 'path': str(root)})
        for path in candidates(kind, root):
            relative = path.name if kind == 'file' else path.relative_to(root).as_posix()
            entry = {'source': name, 'path': relative}
            if path.is_symlink():
                manifest['excluded'].append({**entry, 'reason': 'symbolic link', 'target': os.readlink(path)})
                continue
            if not path.is_file():
                continue
            if SECRET_NAME.search(path.name):
                manifest['excluded'].append({**entry, 'reason': 'secret-like name; not copied'})
                continue
            try:
                size = path.stat().st_size
                if size > LIMIT:
                    manifest['excluded'].append({**entry, 'reason': f'larger than {LIMIT} bytes', 'bytes': size,
                                                 'sha256': sha256(path)})
                    continue
                destination = target_root / name / relative
                for parent in reversed(destination.relative_to(target_root).parents):
                    private_dir(target_root / parent)
                shutil.copyfile(path, destination)
                destination.chmod(0o600)
                digest = sha256(destination)
                if digest != sha256(path):
                    raise CollectionError(f'{path} changed while it was collected')
            except OSError as error:
                manifest['excluded'].append({**entry, 'reason': 'unreadable: ' + type(error).__name__})
                continue
            manifest['files'].append({**entry, 'bytes': size, 'sha256': digest,
                                      'bundle_path': destination.relative_to(bundle).as_posix()})
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    manifest_path.chmod(0o600)
    return manifest


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
    parser.add_argument('--checkout', action='append', default=[])
    parser.add_argument('--tree', action='append', default=[])
    parser.add_argument('--file', action='append', default=[])
    args = parser.parse_args(argv)
    try:
        sources = parse(args.checkout, 'checkout') + parse(args.tree, 'tree') + parse(args.file, 'file')
        manifest = collect(args.bundle, args.attempt, sources)
    except CollectionError as error:
        print(json.dumps({'status': 'REFUSED', 'reason': str(error)}))
        return 2
    print(json.dumps({'status': 'COLLECTED', 'bundle': str(args.bundle.resolve() / args.attempt), 'files': len(manifest['files']),
                      'absent': len(manifest['absent']), 'excluded': len(manifest['excluded'])}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
