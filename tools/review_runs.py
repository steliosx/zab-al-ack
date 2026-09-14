"""Private retained review output and cooperative, process-owned checkout locks."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def checked_path(path: Path) -> Path:
    if '..' in path.parts:
        raise ValueError('review_path_traversal')
    path = path.absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('review_path_symlink')
    return path


def private_directory(path: Path) -> Path:
    """Only the owned leaf is private; never chmod a pre-existing ancestor."""
    path = checked_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        info = path.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('review_directory_not_private') from None
    return path


def atomic_json(path: Path, data: dict, *, mode: int = 0o600) -> None:
    """mkstemp creates 0600 and os.replace keeps it; state the mode explicitly.

    Retained review output stays private inside its own 0700 directory. A caller
    writing a published record must pass the mode its readers need (0o644).
    """
    path = checked_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.review-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(data, handle, indent=2)
            handle.write('\n')
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def new_run(root: Path, run_id: str | None = None) -> Path:
    if run_id is None:
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:12]
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', run_id):
        raise ValueError('review_run_id_invalid')
    base = private_directory(root / 'build/review-runs')
    output = base / run_id
    output.mkdir(mode=0o700)  # Explicit IDs are single use, including after interruption.
    return output


@contextmanager
def workspace_lock(root: Path, name: str = 'review'):
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,40}', name):
        raise ValueError('review_lock_name_invalid')
    base = private_directory(root / 'build/review-locks')
    path = checked_path(base / (name + '.lock'))
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('review_lock_not_private')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('review_workspace_busy') from None
        yield
    finally:
        os.close(fd)  # Keep the inode; unlinking could split concurrent locks.
