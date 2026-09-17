"""Preserve one owned native phase before retiring only its empty network.

The caller holds the shared lab lock throughout startup, tests, stop and this
operation. Containers and volumes remain forensic artifacts, not restartable
projects after their original network is retired. No old-project discovery.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import traceback
from uuid import uuid4
from evidence_utils import code_digests

LIMIT = 64 * 1024 * 1024
ID = re.compile(r'[0-9a-f]{64}')
PROJECT = re.compile(r'ack-alpha-[0-9a-f]{10}-(?:postgres|delivery_faults|worker|alerta|performance)')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def safe_path(path):
    require(path.is_absolute() and '..' not in path.parts
            and not any(p.is_symlink() for p in (path, *path.parents)), 'Unsafe retention path')
    return path


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def prepare_store(path: Path, checkout: Path) -> Path:
    """Require a durable owner-private directory outside disposable workspaces."""
    require(os.name == 'posix', 'Network retention requires POSIX storage')
    path, checkout = safe_path(path), checkout.absolute()
    forbidden = [checkout]
    for key in ('RUNNER_TEMP', 'GITHUB_WORKSPACE'):
        if os.environ.get(key):
            forbidden.append(Path(os.environ[key]).absolute())
    # The runner resets siblings under _work as well as its exact checkout.
    forbidden.extend(p for p in checkout.parents if p.name == '_work')
    require(not any(path == p or path.is_relative_to(p) for p in forbidden),
            'Retention must be outside disposable workspace and temp')
    for parent in path.parents:
        info = parent.stat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in {0, os.geteuid()}, 'Invalid retention ancestor')
        writable = stat.S_IMODE(info.st_mode) & 0o022
        require(not writable or (info.st_uid == 0 and info.st_mode & stat.S_ISVTX),
                'Writable retention ancestor')
    if not path.exists():
        path.mkdir(mode=0o700)
        sync_dir(path.parent)
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
            'Retention directory must be owned and private')
    # Prove writes/fsync before starting any service; keep the probe as evidence.
    probe = path / ('preflight-' + uuid4().hex + '.json')
    write_file(probe, b'{"status":"STORAGE_PREFLIGHT"}\n')
    sync_dir(path)
    return path


def write_file(path, data):
    safe_path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def digest_file(path):
    safe_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as f:
        info = os.fstat(f.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= LIMIT,
                'Unsafe or oversized retention file')
        return hashlib.file_digest(f, 'sha256').hexdigest()


def output(command, root, *, combine_stderr=False):
    data = subprocess.check_output(command, cwd=root,
                                   stderr=subprocess.STDOUT if combine_stderr else subprocess.PIPE, timeout=30)
    require(len(data) <= LIMIT, 'Docker inspection exceeds retention bound')
    return data


def inspect(kind, ids, root):
    if not ids:
        return {}
    require(len(ids) == len(set(ids)), 'Duplicate Docker identities')
    rows = json.loads(output(['docker', kind, 'inspect', *ids], root))
    key = 'Name' if kind == 'volume' else 'Id'
    require(isinstance(rows, list) and all(isinstance(r, dict) for r in rows), 'Malformed Docker inspection')
    mapped = {r.get(key): r for r in rows}
    require(len(rows) == len(mapped) and set(mapped) == set(ids), 'Docker identity mismatch')
    return mapped


def labels(row):
    value = row.get('Labels') or {}
    require(isinstance(value, dict), 'Malformed resource labels')
    return value


def network_valid(row, project):
    lab = labels(row)
    require(row.get('Name') == project + '_isolated' and row.get('Driver') == 'bridge'
            and row.get('Scope') == 'local' and row.get('Internal') is True
            and row.get('Ingress') is False and row.get('ConfigOnly') is False
            and lab.get('com.docker.compose.project') == project
            and lab.get('com.docker.compose.network') == 'isolated', 'Network ownership mismatch')


def mounts_valid(row, service, volume):
    mounts = row['Mounts']
    require(isinstance(mounts, list), 'Malformed native mounts')
    if service == 'postgres':
        require(len(mounts) == 1 and mounts[0].get('Type') == 'volume'
                and mounts[0].get('Name') == volume and mounts[0].get('Destination') == '/var/lib/postgresql'
                and mounts[0].get('RW') is True, 'Native volume mismatch')
    else:
        require(mounts == [], 'Unexpected pooler mount')


def capture_identity(compose: list[str], project: str, root: Path) -> dict:
    """Capture immutable identities while relays own the running phase."""
    require(PROJECT.fullmatch(project), 'Invalid generated native project')
    ids = output([*compose, 'ps', '--all', '--quiet'], root).decode().split()
    require(len(ids) == 2 and all(ID.fullmatch(i) for i in ids), 'Expected two exact container identities')
    containers = inspect('container', ids, root)
    services, networks = set(), set()
    volume = project + '_ack-lab-pg18'
    for row in containers.values():
        lab = labels(row['Config'])
        service = lab.get('com.docker.compose.service')
        require(lab.get('com.docker.compose.project') == project and service in {'postgres', 'pgbouncer'}
                and row['State'].get('Running') is True, 'Running container ownership mismatch')
        services.add(service)
        endpoints = row['NetworkSettings']['Networks']
        require(set(endpoints) == {project + '_isolated'}, 'Unexpected native container network')
        networks.add(endpoints[project + '_isolated']['NetworkID'])
        mounts_valid(row, service, volume)
    require(services == {'postgres', 'pgbouncer'} and len(networks) == 1, 'Ambiguous native ownership')
    network_id, = networks
    require(isinstance(network_id, str) and ID.fullmatch(network_id), 'Invalid network identity')
    network_valid(inspect('network', [network_id], root)[network_id], project)
    volume_record = inspect('volume', [volume], root)[volume]
    require(labels(volume_record).get('com.docker.compose.project') == project
            and labels(volume_record).get('com.docker.compose.volume') == 'ack-lab-pg18', 'Volume ownership mismatch')
    return {'project': project, 'network_id': network_id, 'container_ids': sorted(ids),
            'volume_name': volume, 'volume_record': volume_record}


def inventory(root):
    network_ids = output(['docker', 'network', 'ls', '--quiet', '--no-trunc'], root).decode().split()
    container_ids = output(['docker', 'ps', '--all', '--quiet', '--no-trunc'], root).decode().split()
    volumes = output(['docker', 'volume', 'ls', '--quiet'], root).decode().split()
    require(all(ID.fullmatch(i) for i in network_ids + container_ids), 'Invalid full Docker identity')
    return {'networks': inspect('network', network_ids, root),
            'containers': inspect('container', container_ids, root), 'volumes': inspect('volume', volumes, root)}


def validate_stopped(before, identity):
    project, network_id = identity['project'], identity['network_id']
    network = before['networks'][network_id]
    network_valid(network, project)
    require(network.get('Containers') == {}, 'Network has active endpoints')
    owned = {key: row for key, row in before['containers'].items()
             if labels(row['Config']).get('com.docker.compose.project') == project}
    require(set(owned) == set(identity['container_ids']), 'Native container set changed')
    services = set()
    for row in owned.values():
        state = row['State']
        require(state.get('Status') == 'exited' and state.get('Running') is False
                and not any(state.get(k) for k in ('Paused', 'Restarting', 'Dead'))
                and not row['HostConfig'].get('AutoRemove'), 'Native container not safely stopped')
        service = labels(row['Config']).get('com.docker.compose.service')
        services.add(service)
        mounts_valid(row, service, identity['volume_name'])
        endpoints = row['NetworkSettings']['Networks']
        require(set(endpoints) == {project + '_isolated'}
                and endpoints[project + '_isolated']['NetworkID'] == network_id, 'Container network changed')
    require(services == {'postgres', 'pgbouncer'}, 'Native service identities changed')
    volume = before['volumes'][identity['volume_name']]
    require(volume == identity['volume_record'], 'Volume identity changed')
    require({key for key, row in before['networks'].items()
             if labels(row).get('com.docker.compose.project') == project} == {network_id},
            'Unexpected owned network')
    require({key for key, row in before['volumes'].items()
             if labels(row).get('com.docker.compose.project') == project} == {identity['volume_name']},
            'Unexpected owned volume')
    for key, row in before['containers'].items():
        endpoints = row['NetworkSettings']['Networks']
        if any(name == project + '_isolated' or item.get('NetworkID') == network_id
               for name, item in endpoints.items()):
            require(key in owned, 'Foreign container references network')


def retire_network(root: Path, store: Path, identity: dict, binding: dict, diagnostics: list[Path]) -> dict:
    """Fail closed; an uncertain removal is never retried automatically."""
    prepare_store(store, root)
    attempt = store / (identity['project'] + '-' + uuid4().hex)
    attempt.mkdir(mode=0o700)
    sync_dir(store)
    record = {'status': 'PREFLIGHT', 'removal_requested': False, 'identity': identity, 'binding': binding,
              'removal_command': ['docker', 'network', 'rm', identity['network_id']]}

    def save(name, value):
        write_file(attempt / name, json.dumps(value, indent=2).encode() + b'\n')
        sync_dir(attempt)

    save('started.json', record)
    try:
        before = inventory(root)
        validate_stopped(before, identity)
        save('inventory-before.json', before)
        for index, source in enumerate(diagnostics):
            digest = digest_file(source)
            data = source.read_bytes()
            require(hashlib.sha256(data).hexdigest() == digest == digest_file(source), 'Diagnostic changed')
            write_file(attempt / f'phase-{index}.raw', data)
        for identifier in identity['container_ids']:
            write_file(attempt / (identifier + '.log'), output(
                ['docker', 'logs', '--timestamps', '--details', identifier], root, combine_stderr=True))
        files = {p.name: digest_file(p) for p in attempt.iterdir()}
        save('pre-removal.json', {**record, 'status': 'RETAINED', 'files': files})
        require(all(digest_file(attempt / name) == value for name, value in files.items()), 'Retention verification failed')
        # Revalidate the complete resource inventory immediately before the sole
        # mutation; shared-lock users cannot race this service/test interval.
        require(inventory(root) == before, 'Docker resources changed after retention')
        validate_stopped(before, identity)
        require(code_digests(root) == binding['code_digests'], 'Source or harness changed before removal')
        record.update(status='REMOVAL_REQUESTED', removal_requested=True)
        save('removal-requested.json', record)
        result = output(record['removal_command'], root)
        write_file(attempt / 'removal-output.log', result)
        after = inventory(root)
        save('inventory-after.json', after)
        expected = dict(before['networks'])
        del expected[identity['network_id']]
        require(after['networks'] == expected and after['containers'] == before['containers']
                and after['volumes'] == before['volumes'], 'Unexpected Docker resource change')
        record['status'] = 'RETIRED'
        save('result.json', record)
        manifest = {p.name: digest_file(p) for p in attempt.iterdir()}
        save('manifest.json', manifest)
        require(all(digest_file(attempt / name) == value for name, value in manifest.items()), 'Final retention verification failed')
        return {'status': 'RETIRED', 'manifest_sha256': digest_file(attempt / 'manifest.json'),
                'containers_preserved': True, 'volumes_preserved': True}
    except BaseException as error:
        # Keep original failure/partial records, even if post-mutation receipt
        # writing fails. Never expose inspection/log text via public reports.
        record.update(status='INCOMPLETE', error_type=type(error).__name__,
                      outcome='UNKNOWN' if record['removal_requested'] else 'NOT_REQUESTED')
        try:
            save('failure.json', record)
            write_file(attempt / 'failure-private.log', ''.join(traceback.format_exception(error)).encode())
            for name in ('stdout', 'stderr'):
                data = getattr(error, name, None)
                if data:
                    write_file(attempt / ('failure-' + name + '.log'), data if isinstance(data, bytes) else data.encode())
            sync_dir(attempt)
        except (OSError, ValueError):
            pass
        raise
