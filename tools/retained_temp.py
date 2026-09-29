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
import os
from pathlib import Path
import stat
import tempfile

from review_runs import atomic_json, now, private_directory


def fresh_basetemp(label: str) -> Path:
    """Return a basetemp that does not exist yet: pytest empties an existing one."""
    root = Path(tempfile.mkdtemp(prefix='zab-'))
    atomic_json(root / 'OWNER.json', {'label': label, 'pid': os.getpid(), 'created_at_utc': now()})
    return root / 'b'


def _copy_file(source: Path, target: Path) -> dict:
    digest = hashlib.sha256()
    size = 0
    with source.open('rb') as reader, target.open('xb') as writer:
        for chunk in iter(lambda: reader.read(1 << 20), b''):
            digest.update(chunk)
            writer.write(chunk)
            size += len(chunk)
    os.chmod(target, 0o600)
    return {'size': size, 'sha256': digest.hexdigest()}


def retain(basetemp: Path, destination: Path) -> dict:
    """Copy one invocation's scratch without following links or changing permissions.

    Links are copied as links. FIFOs, sockets and devices are listed, not opened.
    An entry that cannot be read is listed with its error. Any other failure,
    including an entry that vanishes, makes the retention INCOMPLETE. The
    original directory is never removed.
    """
    destination = private_directory(destination)
    if any(destination.iterdir()):
        # An OSError, so a caller's run fails rather than misclassifying the error.
        raise FileExistsError('retention_destination_not_empty')
    owner = basetemp.parent / 'OWNER.json'
    record: dict = {'source': str(basetemp), 'owner': owner.read_text() if owner.is_file() else None,
                    'started_at_utc': now(), 'files': {}, 'links': {}, 'special': {}, 'unreadable': {}, 'errors': {}}
    if not os.path.lexists(basetemp):
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
                record['errors'][name] = type(error).__name__ + ': ' + (error.strerror or '')
        record['status'] = 'INCOMPLETE' if record['errors'] else 'COMPLETE'
    record['finished_at_utc'] = now()
    atomic_json(destination / 'retention.json', record)
    return record
