"""Preserve lab phase diagnostics privately before any destructive reset.

Both native runners (Compose `tools/native_ci.py` and local
`tools/native_local.py`) call `preserve` before they invalidate a previous
run's reports or reset the disposable cluster directory. The copies live under
`build/private/retained-phases/<run id>/<label>/` in the checkout: private,
never exported, and carried out of a runner workspace by the trusted job hook's
pre-reset archive. This is per-phase preservation inside the run; end-of-job
retention cannot recover a file the run itself deleted between phases.

A failure here must stop the caller's destructive step. `preserve` raises
`RetentionError` and never returns a partial success.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Iterable

RETAINED = Path('build/private/retained-phases')
LIMIT = 64 * 1024 * 1024
LABEL = r'[a-z][a-z0-9_-]{0,63}'


class RetentionError(ValueError):
    """The diagnostics could not be preserved; the caller must not reset."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RetentionError(message)


def _unlinked(path: Path) -> None:
    _require(not any(part.is_symlink() for part in (path, *path.parents)), 'symlink in retention path: ' + path.name)


def _sync(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _copy(source: Path, target: Path) -> dict:
    """Copy one regular file with O_NOFOLLOW on both ends and verify the bytes."""
    digest = hashlib.sha256()
    size = 0
    try:
        # O_NONBLOCK: a FIFO in place of a diagnostic must fail, not block. An
        # unreadable source is a RetentionError like every other failure here,
        # so the caller refuses its destructive step instead of crashing.
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as reader:
            info = os.fstat(reader.fileno())
            _require(stat.S_ISREG(info.st_mode), 'not a regular file: ' + source.name)
            _require(info.st_size <= LIMIT, 'diagnostic exceeds the retention bound: ' + source.name)
            out = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(out, 'wb') as writer:
                while True:
                    chunk = reader.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    size += len(chunk)
                    _require(size <= LIMIT, 'diagnostic grew past the retention bound: ' + source.name)
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
    except OSError as error:
        raise RetentionError(type(error).__name__ + ' while copying ' + source.name) from error
    with target.open('rb') as check:
        verified = hashlib.file_digest(check, 'sha256').hexdigest()
    _require(verified == digest.hexdigest(), 'retained copy does not match its source: ' + source.name)
    return {'sha256': verified, 'bytes': size}


def preserve(root: Path, run_id: str, label: str, sources: Iterable[Path]) -> dict:
    """Copy every existing source file into a new private retention directory.

    Sources are resolved relative to `root` when relative. A source that does
    not exist is recorded as absent, not an error: the caller lists everything a
    phase may have written. A symlink, special file, oversized file, unreadable
    file or a destination that already exists raises RetentionError.
    Returns a bounded record for the public run report (paths and digests of the
    retained copies; never their content).
    """
    _require(bool(re.fullmatch(r'[0-9a-f]{6,32}', run_id)), 'invalid retention run identity')
    _require(bool(re.fullmatch(LABEL, label)), 'invalid retention label')
    root = root.resolve()
    # build/private is shared with the pool state directory, which an
    # unprivileged service user must traverse; only the retention tree is 0700.
    (root / RETAINED.parent).mkdir(parents=True, exist_ok=True)
    directory = root / RETAINED / run_id / label
    _unlinked(directory.parent)
    try:
        (root / RETAINED).mkdir(exist_ok=True, mode=0o700)
        (root / RETAINED / run_id).mkdir(exist_ok=True, mode=0o700)
        directory.mkdir(mode=0o700)
    except FileExistsError as error:
        raise RetentionError('retention directory already exists: ' + label) from error
    except OSError as error:
        raise RetentionError(type(error).__name__ + ' creating retention directory') from error
    files: dict[str, dict] = {}
    absent: list[str] = []
    for item in sources:
        source = Path(item)
        if not source.is_absolute():
            source = root / source
        relative = source.relative_to(root).as_posix() if source.is_relative_to(root) else source.as_posix()
        if not source.is_symlink() and not source.exists():
            absent.append(relative)
            continue
        _require(not source.is_symlink(), 'refusing to retain a symlink: ' + relative)
        name = relative.replace('/', '__')
        _require(name not in files, 'duplicate retention source: ' + relative)
        files[name] = {'source': relative, **_copy(source, directory / name)}
    manifest = {'schema_version': 1, 'run_id': run_id, 'label': label,
                'preserved_at': datetime.now(timezone.utc).isoformat(), 'files': files, 'absent': sorted(absent)}
    encoded = json.dumps(manifest, indent=2, sort_keys=True).encode() + b'\n'
    manifest_path = directory / 'manifest.json'
    try:
        fd = os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        _sync(directory)
    except OSError as error:
        raise RetentionError(type(error).__name__ + ' writing retention manifest') from error
    for name, entry in files.items():
        with (directory / name).open('rb') as handle:
            _require(hashlib.file_digest(handle, 'sha256').hexdigest() == entry['sha256'],
                     'retained copy changed before the manifest was sealed: ' + name)
    return {'status': 'RETAINED', 'label': label, 'directory': directory.relative_to(root).as_posix(),
            'retained': len(files), 'absent': len(absent), 'manifest_sha256': hashlib.sha256(encoded).hexdigest()}
