#!/usr/bin/env python3
"""Private host hooks for the dedicated Zabalack runner; never an evidence producer.

Install a reviewed copy outside the checkout, owned by root. The command-line
interface deliberately offers no path overrides. Unit tests inject Paths and
context directly; those synthetic records are not Actions execution evidence.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from uuid import uuid4

MIN_FREE_BYTES = 5 * 1024**3
MAX_ARCHIVE_BYTES = 4 * 1024**3
MAX_FILES = 50000
EXCLUDED = {'.git', 'node_modules', '.venv', '__pycache__', '.pytest_cache',
            '.mypy_cache', '.ruff_cache', '.cache'}
SHA = re.compile(r'[0-9a-f]{40}')
REPOSITORY = 'steliosx/zab-al-ack'


@dataclass(frozen=True)
class Paths:
    runner: Path = Path('/opt/zabalack-actions')
    evidence: Path = Path('/var/lib/zabalack-evidence')

    @property
    def workspace(self):
        return self.runner / '_work/zab-al-ack/zab-al-ack'

    @property
    def temporary(self):
        return self.runner / '_work/_temp'


def now():
    return datetime.now(timezone.utc).isoformat()


def checked(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('unsafe_path')
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError('symlink_path')
    return path


def private_directory(path):
    checked(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('retention_directory_not_private')


def read_json(path):
    checked(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
            raise ValueError('unsafe_json_file')
        return json.load(handle)


def atomic_json(path, data):
    checked(path)
    fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def validate_paths(paths):
    for path in (paths.runner, paths.workspace, paths.temporary, paths.evidence):
        checked(path)
    if paths.evidence == paths.runner or paths.evidence.is_relative_to(paths.runner):
        raise ValueError('retention_inside_runner')
    if paths.runner.is_relative_to(paths.evidence):
        raise ValueError('runner_inside_retention')
    private_directory(paths.evidence)


def capacity(paths, required=0):
    if (shutil.disk_usage(paths.evidence).free < MIN_FREE_BYTES + required
            or shutil.disk_usage(paths.runner).free < MIN_FREE_BYTES):
        raise ValueError('insufficient_retention_space')


@contextmanager
def hook_lock(paths):
    path = checked(paths.evidence / 'hook.lock')
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('unsafe_hook_lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)  # Keep the inode. This is separate from the Linux lab lock.


def actions_context(paths, environment):
    """Read only required metadata; never serialize the environment/event body."""
    if environment.get('GITHUB_ACTIONS') != 'true':
        raise ValueError('actions_context_required')
    if environment.get('GITHUB_WORKSPACE') != str(paths.workspace):
        raise ValueError('wrong_workspace')
    if environment.get('RUNNER_TEMP') != str(paths.temporary):
        raise ValueError('wrong_temporary_directory')
    fields = {'repository': 'GITHUB_REPOSITORY', 'run_id': 'GITHUB_RUN_ID',
              'run_attempt': 'GITHUB_RUN_ATTEMPT', 'job': 'GITHUB_JOB',
              'sha': 'GITHUB_SHA', 'event_name': 'GITHUB_EVENT_NAME',
              'ref': 'GITHUB_REF', 'runner_os': 'RUNNER_OS', 'runner_arch': 'RUNNER_ARCH'}
    context = {key: environment.get(name, '') for key, name in fields.items()}
    if any(not isinstance(value, str) or not value or len(value) > 512
           or any(ord(char) < 32 for char in value) for value in context.values()):
        raise ValueError('invalid_context')
    if context['repository'] != REPOSITORY or context['runner_os'] != 'Linux' or context['runner_arch'] != 'ARM64':
        raise ValueError('wrong_repository_or_platform')
    if (not SHA.fullmatch(context['sha']) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,99}', context['job'])
            or any(not re.fullmatch(r'[1-9][0-9]*', context[name]) for name in ('run_id', 'run_attempt'))):
        raise ValueError('invalid_run_identity')
    event_path = checked(Path(environment.get('GITHUB_EVENT_PATH', '')))
    if not event_path.is_relative_to(paths.temporary):
        raise ValueError('event_outside_runner_temporary_directory')
    event = read_json(event_path)
    if not isinstance(event, dict):
        raise ValueError('invalid_event')
    pull = event.get('pull_request')
    if pull is not None:
        if (not isinstance(pull, dict) or not isinstance(pull.get('head'), dict)
                or not isinstance(pull.get('base'), dict)):
            raise ValueError('invalid_pull_request')
        for name, value in {'head_sha': pull.get('head', {}).get('sha'),
                            'base_sha': pull.get('base', {}).get('sha'),
                            'merge_sha': pull.get('merge_commit_sha')}.items():
            if value is not None:
                if not isinstance(value, str) or not SHA.fullmatch(value):
                    raise ValueError('invalid_pull_request_commit')
                context[name] = value
    return context


def actual_head(workspace):
    if not workspace.is_dir():
        return None
    result = subprocess.run(['/usr/bin/git', '-c', 'safe.directory=' + str(workspace),
                             '-C', str(workspace), 'rev-parse', '--verify', 'HEAD'],
                            capture_output=True, text=True, timeout=10,
                            env={'PATH': '/usr/bin:/bin', 'HOME': '/var/lib/zabalack-runner'})
    value = result.stdout.strip()
    return value if result.returncode == 0 and SHA.fullmatch(value) else None


def selected_files(root):
    """Retain safe internal alias metadata without traversing a symlink."""
    checked(root)
    files = []
    target_directories = {}
    total = 0
    if not root.exists():
        return files, total
    def failed_walk(error):
        raise error
    for directory, directories, names in os.walk(root, followlinks=False, onerror=failed_walk):
        directories[:] = sorted(name for name in directories if name not in EXCLUDED)
        for name in [*directories, *names]:
            if name in EXCLUDED:
                continue
            path = Path(directory) / name
            checked(path.parent)
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                alias = internal_alias(root, path, info)
                files.append((path, info, alias))
                if stat.S_ISDIR(alias.target_stat.st_mode):
                    target_directories[alias.target] = alias.target_stat
                if name in directories:
                    directories.remove(name)
                continue
            if stat.S_ISDIR(info.st_mode):
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('unsafe_workspace_entry')
            files.append((path, info, None))
            total += info.st_size
            if len(files) + len(target_directories) > MAX_FILES or total > MAX_ARCHIVE_BYTES:
                raise ValueError('retention_bound_exceeded')
        if len(files) + len(target_directories) > MAX_FILES:
            raise ValueError('retention_bound_exceeded')
    files.extend((path, info, None) for path, info in target_directories.items())
    return sorted(files), total


class HashingReader:
    def __init__(self, handle):
        self.handle = handle
        self.digest = hashlib.sha256()

    def read(self, size):
        data = self.handle.read(size)
        self.digest.update(data)
        return data


def file_identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode, info.st_nlink


@dataclass(frozen=True)
class InternalAlias:
    original: str
    linkname: str
    target: Path
    target_stat: os.stat_result


def internal_alias(root, path, expected):
    """Inspect link text and concrete target metadata; never follow a link."""
    checked(path.parent)
    before = path.lstat()
    if file_identity(before) != file_identity(expected) or not stat.S_ISLNK(before.st_mode):
        raise ValueError('workspace_changed_during_retention')
    original = os.readlink(path)
    raw = Path(original)
    target = Path(os.path.normpath(raw if raw.is_absolute() else path.parent / raw))
    if (target == root or not target.is_relative_to(root)
            or any(part in EXCLUDED for part in target.relative_to(root).parts)):
        raise ValueError('unsafe_workspace_alias')
    checked(target)  # Reject chains and linked ancestors before any target read.
    target_stat = target.lstat()
    if (not (stat.S_ISREG(target_stat.st_mode) or stat.S_ISDIR(target_stat.st_mode))
            or (stat.S_ISREG(target_stat.st_mode) and target_stat.st_nlink != 1)):
        raise ValueError('unsafe_workspace_alias')
    if file_identity(path.lstat()) != file_identity(before) or os.readlink(path) != original:
        raise ValueError('workspace_changed_during_retention')
    # Tar symlinks resolve from their containing directory. Absolute host paths
    # become canonical relative links whose normalized target remains in root.
    return InternalAlias(original, Path(os.path.relpath(target, path.parent)).as_posix(), target, target_stat)


def verify_alias(root, path, expected, alias):
    observed = internal_alias(root, path, expected)
    if (observed.original != alias.original or observed.linkname != alias.linkname
            or observed.target != alias.target
            or file_identity(observed.target_stat) != file_identity(alias.target_stat)):
        raise ValueError('workspace_changed_during_retention')


def archive_workspace(paths, attempt, status, context, head_reader=actual_head):
    directory = checked(paths.evidence / attempt)
    if not re.fullmatch(r'[a-z0-9-]{1,120}', attempt):
        raise ValueError('invalid_attempt_path')
    private_directory(directory)
    files, total = selected_files(paths.workspace)
    capacity(paths, total + len(files) * 2048 + 10240)
    identifier = uuid4().hex
    temporary = directory / (identifier + '.tar.partial')
    final = directory / (identifier + '.tar')
    # An interrupted .partial is retained; no cleanup may erase this attempt.
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    manifest = []
    aliases = []
    directories = []
    with os.fdopen(fd, 'wb') as output:
        with tarfile.open(fileobj=output, mode='w') as archive:
            for path, expected, alias in files:
                name = path.relative_to(paths.workspace).as_posix()
                if alias is not None:
                    verify_alias(paths.workspace, path, expected, alias)
                    entry = tarfile.TarInfo(name)
                    entry.type = tarfile.SYMTYPE
                    entry.linkname = alias.linkname
                    entry.mode = expected.st_mode & 0o777
                    entry.mtime = int(expected.st_mtime)
                    archive.addfile(entry)
                    verify_alias(paths.workspace, path, expected, alias)
                    aliases.append({'path': name, 'linkname': alias.linkname,
                                    'target': alias.target.relative_to(paths.workspace).as_posix()})
                    continue
                checked(path)
                if stat.S_ISDIR(expected.st_mode):
                    if file_identity(path.lstat()) != file_identity(expected):
                        raise ValueError('workspace_changed_during_retention')
                    entry = tarfile.TarInfo(name)
                    entry.type = tarfile.DIRTYPE
                    entry.mode = expected.st_mode & 0o777
                    entry.mtime = int(expected.st_mtime)
                    archive.addfile(entry)
                    directories.append(name)
                    continue
                source_fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(source_fd, 'rb') as source:
                    observed = os.fstat(source.fileno())
                    if file_identity(observed) != file_identity(expected) or not stat.S_ISREG(observed.st_mode):
                        raise ValueError('workspace_changed_during_retention')
                    entry = tarfile.TarInfo(name)
                    entry.size = observed.st_size
                    entry.mode = observed.st_mode & 0o777
                    entry.mtime = int(observed.st_mtime)
                    reader = HashingReader(source)
                    archive.addfile(entry, reader)
                    if file_identity(os.fstat(source.fileno())) != file_identity(observed):
                        raise ValueError('workspace_changed_during_retention')
                    manifest.append({'path': name, 'size': entry.size, 'sha256': reader.digest.hexdigest()})
            for path, expected, alias in files:
                if alias is not None:
                    verify_alias(paths.workspace, path, expected, alias)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, final)
    with final.open('rb') as handle:
        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
    record = {'schema_version': 1, 'status': status, 'captured_at_utc': now(),
              'context': context, 'actual_checkout_sha': head_reader(paths.workspace),
              'archive': final.name, 'archive_sha256': digest, 'files': manifest,
              'internal_aliases': aliases, 'alias_target_directories': directories,
              'excluded_dependency_names': sorted(EXCLUDED),
              'job_result': 'UNAVAILABLE_FROM_HOOK_USE_ACTIONS_RESULT',
              'cleanup': 'NO_DOCKER_OPERATIONS_PERFORMED'}
    atomic_json(directory / (identifier + '.json'), record)
    return record


def reset_directory(path):
    checked(path)
    if path.exists():
        if not path.is_dir():
            raise ValueError('reset_target_not_directory')
        if not shutil.rmtree.avoids_symlink_attacks:
            raise ValueError('safe_recursive_cleanup_unavailable')
        shutil.rmtree(path)


def run_hook(phase, paths, context, *, head_reader=actual_head):
    """Caller supplies validated Actions context; tests supply labelled fixtures."""
    validate_paths(paths)
    with hook_lock(paths):
        capacity(paths)
        state_path = paths.evidence / 'current-job.json'
        previous = read_json(state_path) if state_path.exists() else None
        if phase == 'complete':
            if not isinstance(previous, dict) or previous.get('context') != context or previous.get('phase') != 'started':
                raise ValueError('completion_context_mismatch')
            record = archive_workspace(paths, previous['attempt'], 'JOB_COMPLETED', context, head_reader)
            previous.update(phase='completed', completed_at_utc=now(), archive=record['archive'])
            atomic_json(state_path, previous)
            return previous
        if phase != 'start':
            raise ValueError('unknown_hook_phase')
        if previous is not None and (not isinstance(previous, dict) or previous.get('phase') not in {'started', 'completed'}):
            raise ValueError('invalid_previous_job_state')
        # Retain again immediately before reset, including post-hook changes.
        # No old successful archive is rebound or replaced by this snapshot.
        if previous is not None or paths.workspace.exists():
            attempt = previous['attempt'] if previous else 'orphan-' + uuid4().hex
            status = 'INTERRUPTED' if previous and previous['phase'] == 'started' else 'PRE_RESET_SNAPSHOT'
            archive_workspace(paths, attempt, status, previous['context'] if previous else {}, head_reader)
        reset_directory(paths.workspace)
        for name in ('venv', 'gitleaks'):
            reset_directory(paths.temporary / name)
        paths.workspace.mkdir(parents=True, exist_ok=True)
        attempt = 'job-' + uuid4().hex
        private_directory(paths.evidence / attempt)
        state = {'schema_version': 1, 'phase': 'started', 'attempt': attempt,
                 'context': context, 'started_at_utc': now()}
        atomic_json(paths.evidence / attempt / 'start.json', state)
        atomic_json(state_path, state)
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('start', 'complete'))
    args = parser.parse_args()
    try:
        if pwd.getpwuid(os.geteuid()).pw_name != 'zabalack-runner':
            raise ValueError('dedicated_service_account_required')
        paths = Paths()
        validate_paths(paths)
        context = actions_context(paths, os.environ)
        run_hook(args.phase, paths, context)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('Zabalack runner hook refused operation; retained state requires inspection.')
        return 1
    print('Zabalack runner hook completed; private retention is not a test result.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
