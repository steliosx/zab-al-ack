"""Owned, retained pytest scratch for local gate runs.

By default pytest keeps its base temporary directories under the shared
``/tmp/pytest-of-<user>`` and removes all but the three newest, so a later
session can delete an earlier session's scratch. Instead, each gate invocation
gets a fresh explicit ``--basetemp`` below its own ``mkdtemp`` root, which pytest
never rotates. The root stays short because fixtures bind Unix sockets below it.

Before the child starts, a PENDING custody record binding the invocation to the
root is written to the run's private raw directory. Whatever way the invocation
ends, the scratch is then copied there with a complete inventory, and the record
is finalised. A record still PENDING means retention never ran (for example an
uncatchable termination); ``recover`` retains it later from the record alone.
The original is always left in place.
"""
from __future__ import annotations

from fnmatch import fnmatchcase
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import uuid

from review_runs import atomic_json, now, private_directory

# Statuses that never let a gate record pass; the tests' own exit status is kept separately.
FAILED_RETENTION = frozenset({'INCOMPLETE', 'NOT_OWNED', 'FAILED', 'PENDING'})
# Fixtures the suite deliberately makes unreadable: regular files with mode 0 at exactly
# these paths. Their metadata is listed and their bytes are not custody material; any
# other unreadable entry is INCOMPLETE.
EXPECTED_UNREADABLE = {
    'test_unreadable_environment_fi*/runner.env':
        'tests/release/test_runner_account_check.py: mode-0 environment file',
    'test_preserve_fails_closed_and*/evidence/native-run.json':
        'tests/release/test_lab_retention.py[unreadable]: mode-0 source',
}
# /var/tmp where writable, else the system temporary directory. /var/tmp is conventionally
# not a size-limited tmpfs (a full reference scratch is gigabytes and every original is
# kept), though that depends on the host. /var/tmp/zab-XXXXXXXX/b is still shorter than
# pytest's own /tmp/pytest-of-<user>/pytest-<n>, which matters for socket fixtures.
ROOT_PARENT: str | None = '/var/tmp' if os.path.isdir('/var/tmp') and os.access('/var/tmp', os.W_OK) else None
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
# Never follows a final link and never blocks on a FIFO swapped in after classification.
_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC


class Unstable(OSError):
    """An entry changed identity or content while being retained."""


class OwnedScratch:
    """One invocation's owned basetemp, its pending custody record and, on exit, its retention.

    ``with`` never swallows the child's failure, timeout or interruption.
    """

    def __init__(self, label: str, parent: Path, name: str | None = None):
        parent = private_directory(parent)
        if name is not None:
            # Single use: an existing destination (PENDING or finished) is refused before anything is written.
            self.destination = parent / name
            self.destination.mkdir(mode=0o700)
        root = Path(tempfile.mkdtemp(prefix='zab-', dir=ROOT_PARENT))
        info = os.lstat(root)
        self.binding = {'label': label, 'invocation': uuid.uuid4().hex, 'pid': os.getpid(), 'root': str(root),
                        'device': info.st_dev, 'inode': info.st_ino, 'created_at_utc': now()}
        atomic_json(root / 'OWNER.json', self.binding)
        self.basetemp = root / 'b'
        if name is None:
            self.destination = parent / (label + '-' + root.name)
            self.destination.mkdir(mode=0o700)
        # Persisted before the child starts, so a crashed parent leaves a recoverable, visibly incomplete record.
        atomic_json(self.destination / 'custody.json', dict(self.binding, status='PENDING'))
        self.status = 'PENDING'

    def __enter__(self) -> 'OwnedScratch':
        return self

    def __exit__(self, kind, value, traceback) -> bool:
        self.status = retain_status(self.destination)
        return False


def _expected(name: str) -> str | None:
    parts = name.split('/')
    for pattern, reason in EXPECTED_UNREADABLE.items():
        pattern_parts = pattern.split('/')
        if len(parts) == len(pattern_parts) and all(fnmatchcase(p, q) for p, q in zip(parts, pattern_parts, strict=True)):
            return reason
    return None


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _read_owner(root_fd: int) -> dict:
    fd = os.open('OWNER.json', _FILE, dir_fd=root_fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Unstable('owner_record_not_regular')
        with os.fdopen(os.dup(fd), 'rb') as handle:
            return json.loads(handle.read(65536))
    finally:
        os.close(fd)


def _copy_file(parent_fd: int, name: str, expected: tuple[int, int], target: Path) -> dict:
    fd = os.open(name, _FILE, dir_fd=parent_fd)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or _identity(before) != expected:
            raise Unstable('entry_replaced_before_open')
        digest = hashlib.sha256()
        size = 0
        with open(target, 'xb') as writer:
            while chunk := os.read(fd, 1 << 20):
                digest.update(chunk)
                writer.write(chunk)
                size += len(chunk)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    os.chmod(target, 0o600)
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or size != after.st_size:
        raise Unstable('source_changed_during_copy')
    if hashlib.sha256(target.read_bytes()).hexdigest() != digest.hexdigest():
        raise Unstable('copy_inventory_mismatch')
    return {'size': size, 'sha256': digest.hexdigest(), 'mode': oct(stat.S_IMODE(before.st_mode))}


def _walk(directory_fd: int, prefix: str, target: Path, record: dict) -> None:
    for name in sorted(os.listdir(directory_fd)):
        relative = prefix + name
        info = None
        try:
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                link = os.readlink(name, dir_fd=directory_fd)
                os.symlink(link, target / name)
                record['links'][relative] = link
            elif stat.S_ISDIR(info.st_mode):
                child = os.open(name, _DIRECTORY, dir_fd=directory_fd)
                try:
                    if _identity(os.fstat(child)) != _identity(info):
                        raise Unstable('directory_replaced_before_open')
                    (target / name).mkdir(mode=0o700)
                    _walk(child, relative + '/', target / name, record)
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                record['files'][relative] = _copy_file(directory_fd, name, _identity(info), target / name)
            else:
                record['special'][relative] = stat.filemode(info.st_mode)
        except PermissionError as error:
            # Only a listed fixture path that is a regular file with the deliberate mode 0 is exempt.
            # A directory, any other type or mode, or an unlisted path stays an unexpected denial.
            deliberate = info is not None and stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0
            reason = _expected(relative) if deliberate else None
            (record['unreadable_expected'] if reason else record['unreadable'])[relative] = reason or error.strerror
        except OSError as error:
            record['errors'][relative] = type(error).__name__ + ': ' + (error.strerror or str(error))


def retain(destination: Path) -> dict:
    """Copy one invocation's scratch, bound by its custody record, without following links.

    Every open is relative to an already opened directory and refuses a final link,
    and each opened object's identity must equal what was listed. Links are copied
    as links. FIFOs, sockets and devices are listed, never opened. A root whose
    device, inode or owner record differs from the custody record is NOT_OWNED and
    nothing is copied. An unexpected unreadable entry, a vanished or changed entry,
    or a copy that does not match its inventory makes the retention INCOMPLETE.
    The original directory is never removed or re-permissioned.
    """
    custody = json.loads((destination / 'custody.json').read_text())
    if (destination / 'scratch').exists() or (destination / 'retention.json').exists():
        # An OSError, so a caller's run fails rather than misclassifying the error.
        raise FileExistsError('retention_destination_not_empty')
    record: dict = {'custody': custody, 'started_at_utc': now(), 'files': {}, 'links': {}, 'special': {},
                    'unreadable': {}, 'unreadable_expected': {}, 'errors': {}}
    try:
        root_fd = os.open(custody['root'], _DIRECTORY)
    except OSError as error:
        root_fd, record['errors']['.'] = -1, type(error).__name__
    try:
        if root_fd < 0 or _identity(os.fstat(root_fd)) != (custody['device'], custody['inode']):
            record['status'] = 'NOT_OWNED'
            record['errors'].setdefault('.', 'root_replaced')
        elif _read_owner(root_fd).get('invocation') != custody['invocation']:
            record['status'] = 'NOT_OWNED'
            record['errors']['.'] = 'owner_record_replaced'
        else:
            try:
                base_fd = os.open('b', _DIRECTORY, dir_fd=root_fd)
            except FileNotFoundError:
                # pytest creates the basetemp only when a test requests temporary paths.
                record['status'] = 'NOT_CREATED'
            except PermissionError as error:
                record['status'] = 'INCOMPLETE'
                record['unreadable']['.'] = error.strerror
            else:
                record['status'] = 'INCOMPLETE'  # until the whole walk has completed
                try:
                    (destination / 'scratch').mkdir(mode=0o700)
                    _walk(base_fd, '', destination / 'scratch', record)
                finally:
                    os.close(base_fd)
                record['status'] = 'INCOMPLETE' if record['errors'] or record['unreadable'] else 'COMPLETE'
    except (OSError, ValueError) as error:
        # Before the basetemp is opened, a failure means the root cannot be shown to be ours.
        record['status'] = 'NOT_OWNED' if 'status' not in record else 'INCOMPLETE'
        record['errors'].setdefault('.', type(error).__name__ + ': ' + (getattr(error, 'strerror', None) or str(error)))
    finally:
        if root_fd >= 0:
            os.close(root_fd)
    record['finished_at_utc'] = now()
    atomic_json(destination / 'retention.json', record)
    atomic_json(destination / 'custody.json', dict(custody, status=record['status'], retained_at_utc=record['finished_at_utc']))
    return record


def retain_status(destination: Path) -> str:
    """Retain and return only the status; a failure to retain is itself a status."""
    try:
        return retain(destination)['status']
    except (OSError, ValueError, KeyError):
        return 'FAILED'


def recover(destination: Path) -> Path:
    """Retain a record left PENDING, into a new sibling; the original record is kept as found."""
    custody = json.loads((destination / 'custody.json').read_text())
    if custody.get('status') != 'PENDING':
        raise ValueError('custody_record_not_pending')
    sibling = private_directory(destination.parent / (destination.name + '-recovered-' + uuid.uuid4().hex[:8]))
    atomic_json(sibling / 'custody.json', dict(custody, recovered_from=destination.name))
    retain(sibling)
    return sibling
