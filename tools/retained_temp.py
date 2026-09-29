"""Owned, retained pytest scratch for local gate runs.

By default pytest keeps its base temporary directories under the shared
``/tmp/pytest-of-<user>`` and removes all but the three newest, so a later
session can delete an earlier session's scratch. Instead, each gate invocation
gets a fresh explicit ``--basetemp`` below its own ``mkdtemp`` root, which pytest
never rotates. The root stays short because fixtures bind Unix sockets below it.
After the invocation the scratch is copied into the run's private raw directory
with a complete inventory. The original is left in place.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import uuid

from review_runs import atomic_json, now, private_directory

# Statuses that never let a gate record pass; the tests' own exit status is kept separately.
FAILED_RETENTION = frozenset({'INCOMPLETE', 'NOT_OWNED', 'FAILED'})
# Roots created by this process: basetemp -> (invocation id, device, inode).
_OWNED: dict[str, tuple[str, int, int]] = {}


def fresh_basetemp(label: str) -> Path:
    """Return a basetemp that does not exist yet: pytest empties an existing one.

    Ownership binds this invocation's random identity to the root directory's
    device and inode, so a replaced or foreign root is never taken for it.
    """
    root = Path(tempfile.mkdtemp(prefix='zab-'))
    info = os.lstat(root)
    invocation = uuid.uuid4().hex
    atomic_json(root / 'OWNER.json', {'label': label, 'invocation': invocation, 'pid': os.getpid(),
                                      'device': info.st_dev, 'inode': info.st_ino, 'created_at_utc': now()})
    basetemp = root / 'b'
    _OWNED[str(basetemp)] = (invocation, info.st_dev, info.st_ino)
    return basetemp


def _owner_error(basetemp: Path) -> str | None:
    expected = _OWNED.get(str(basetemp))
    if expected is None:
        return 'basetemp_not_created_by_this_process'
    try:
        info = os.lstat(basetemp.parent)
        owner = json.loads((basetemp.parent / 'OWNER.json').read_text())
    except (OSError, ValueError) as error:
        return type(error).__name__
    if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != expected[1:]:
        return 'root_replaced'
    if not isinstance(owner, dict) or owner.get('invocation') != expected[0]:
        return 'owner_record_replaced'
    return None


def _copy_file(source: Path, target: Path) -> dict:
    before = os.lstat(source)
    digest = hashlib.sha256()
    size = 0
    with source.open('rb') as reader, target.open('xb') as writer:
        for chunk in iter(lambda: reader.read(1 << 20), b''):
            digest.update(chunk)
            writer.write(chunk)
            size += len(chunk)
    os.chmod(target, 0o600)
    after = os.lstat(source)
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns) \
            or size != after.st_size:
        raise OSError('source_changed_during_copy')
    if hashlib.sha256(target.read_bytes()).hexdigest() != digest.hexdigest():
        raise OSError('copy_inventory_mismatch')
    return {'size': size, 'sha256': digest.hexdigest()}


def retain(basetemp: Path, destination: Path) -> dict:
    """Copy one invocation's scratch without following links or changing permissions.

    Links are copied as links. FIFOs, sockets and devices are listed, not opened.
    An entry that cannot be read is listed with its error. Any other failure,
    including an entry that vanishes or changes while being copied, makes the
    retention INCOMPLETE. A root this invocation does not own is never copied.
    The original directory is never removed.
    """
    destination = private_directory(destination)
    if any(destination.iterdir()):
        # An OSError, so a caller's run fails rather than misclassifying the error.
        raise FileExistsError('retention_destination_not_empty')
    owner = basetemp.parent / 'OWNER.json'
    record: dict = {'source': str(basetemp), 'owner': owner.read_text() if owner.is_file() else None,
                    'started_at_utc': now(), 'files': {}, 'links': {}, 'special': {}, 'unreadable': {}, 'errors': {}}
    ownership = _owner_error(basetemp)
    if ownership:
        record['status'] = 'NOT_OWNED'
        record['errors']['.'] = ownership
    elif not os.path.lexists(basetemp):
        # pytest creates the basetemp only when a test requests temporary paths.
        record['status'] = 'NOT_CREATED'
    else:
        scratch = destination / 'scratch'
        pending = [(basetemp, scratch)]
        while pending:
            source, target = pending.pop()
            name = str(source.relative_to(basetemp))
            try:
                info = os.lstat(source)
                if stat.S_ISLNK(info.st_mode):
                    link = os.readlink(source)
                    os.symlink(link, target)
                    record['links'][name] = link
                elif stat.S_ISDIR(info.st_mode):
                    target.mkdir(mode=0o700)
                    pending.extend((source / child, target / child) for child in sorted(os.listdir(source)))
                elif stat.S_ISREG(info.st_mode):
                    record['files'][name] = dict(_copy_file(source, target), mode=oct(stat.S_IMODE(info.st_mode)))
                else:
                    record['special'][name] = stat.filemode(info.st_mode)
            except PermissionError as error:
                record['unreadable'][name] = error.strerror
            except OSError as error:
                record['errors'][name] = type(error).__name__ + ': ' + (error.strerror or str(error))
        record['status'] = 'INCOMPLETE' if record['errors'] else 'COMPLETE'
    record['finished_at_utc'] = now()
    atomic_json(destination / 'retention.json', record)
    return record


def retain_status(basetemp: Path, destination: Path) -> str:
    """Retain and return only the status; a failure to retain is itself a status."""
    try:
        return retain(basetemp, destination)['status']
    except OSError:
        return 'FAILED'
