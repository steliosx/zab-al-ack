#!/usr/bin/env python3
"""Read-only verification of the dedicated runner account's host capabilities (R28).

Prepared for the Docker-removal operational handoff. The tool changes nothing
on the host and contacts no Docker daemon or remote endpoint. It observes:

  account          the configured account: uid, gid, home, configured groups
  runner_process   the RUNNING runner's identity from /proc: real/effective uid
                   and gid, supplementary groups, capability sets, no_new_privs;
                   selected by the service unit's MainPID, --runner-pid or the
                   --runner-process name (default Runner.Listener)
  sudo             sudo rules for the account (sudo -n -l -U, root only) and
                   mentions in /etc/sudoers and /etc/sudoers.d (root only)
  sockets          each container socket: type, mode, owner, group, POSIX ACL
                   (system.posix_acl_access), the permission INFERRED for the
                   runner identity, and the access DEMONSTRATED by connecting
                   under that identity (uid, gid and groups switched in a child;
                   nothing is sent)
  service          the runner service's effective configuration: unit fragment,
                   drop-ins, User/Group/SupplementaryGroups, Environment= and
                   EnvironmentFile= (DOCKER_* keys only), the runner's .env, and
                   the running process's DOCKER_* environment (root or same user)
  docker_config    the intended account's Docker configuration (DOCKER_CONFIG
                   from the effective service environment, else ~/.docker):
                   currentContext, number of stored auths (never their content),
                   and the context store's endpoints read from meta.json files
  docker_workflows workflows in this checkout whose commands invoke Docker

Every observation is OBSERVED or NOT_OBSERVED with a reason. A required
observation that could not be made (missing permission, unreadable file,
absent or ambiguous runner process, no way to assume the runner identity)
makes the verdict INCOMPLETE; it never becomes "absent" and CONFORMS.

Policies: docker-free (routine CI needs no Docker; the running runner must
hold no docker group, no writable container socket, no daemon settings, no
sudo rule, no capabilities) or docker-allowed (the pre-R26 host; docker group
membership and socket access are expected and reported, the rest as above).
Exit 0 CONFORMS, 1 DEVIATES, 2 INCOMPLETE. The report contains no secrets.
"""
from __future__ import annotations

import argparse
import grp
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import stat
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
HOST_GROUPS = ('docker', 'lxd', 'libvirt', 'kvm', 'sudo', 'admin', 'wheel', 'adm', 'disk', 'root')
SOCKETS = ('/var/run/docker.sock', '/run/docker.sock', '/run/podman/podman.sock')
DOCKER_VARIABLES = ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_CONFIG', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH')
DOCKER_TOOLS = ('tools/native_ci.py', 'tools/run_deployment_e2e.py')
CAPABILITIES = {0: 'CAP_CHOWN', 1: 'CAP_DAC_OVERRIDE', 2: 'CAP_DAC_READ_SEARCH', 3: 'CAP_FOWNER', 4: 'CAP_FSETID', 5: 'CAP_KILL',
                6: 'CAP_SETGID', 7: 'CAP_SETUID', 8: 'CAP_SETPCAP', 12: 'CAP_NET_ADMIN', 13: 'CAP_NET_RAW', 18: 'CAP_SYS_CHROOT',
                19: 'CAP_SYS_PTRACE', 21: 'CAP_SYS_ADMIN', 24: 'CAP_SYS_RESOURCE', 27: 'CAP_MKNOD', 38: 'CAP_PERFMON',
                39: 'CAP_BPF', 40: 'CAP_CHECKPOINT_RESTORE'}
ACL_TAGS = {1: 'user_obj', 2: 'user', 4: 'group_obj', 8: 'group', 16: 'mask', 32: 'other'}
SYSTEMD_DIRECTORIES = ('/etc/systemd/system', '/run/systemd/system', '/lib/systemd/system', '/usr/lib/systemd/system')


def observed(**data):
    return {'status': 'OBSERVED', **data}


def not_observed(reason, **data):
    return {'status': 'NOT_OBSERVED', 'reason': reason, **data}


# ----------------------------------------------------------------- account

def group_name(gid):
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return str(gid)


def account_observation(name):
    try:
        entry = pwd.getpwnam(name)
    except KeyError:
        return not_observed('account does not exist: ' + name)
    ids = sorted(set(os.getgrouplist(name, entry.pw_gid)))
    return observed(name=name, uid=entry.pw_uid, gid=entry.pw_gid, home=entry.pw_dir, shell=entry.pw_shell,
                    group_ids=ids, groups=sorted(group_name(gid) for gid in ids))


# ----------------------------------------------------------------- runner process

def parse_status(text):
    fields = {}
    for line in text.splitlines():
        key, _, value = line.partition(':')
        fields[key.strip()] = value.strip()
    identity = {}
    for key in ('Uid', 'Gid'):
        parts = fields.get(key, '').split()
        if len(parts) != 4 or not all(part.isdigit() for part in parts):
            raise ValueError('malformed ' + key)
        identity[key.lower()] = {'real': int(parts[0]), 'effective': int(parts[1]), 'saved': int(parts[2]), 'fs': int(parts[3])}
    groups = fields.get('Groups', '').split()
    if not all(item.isdigit() for item in groups):
        raise ValueError('malformed Groups')
    identity['supplementary_group_ids'] = sorted(int(item) for item in groups)
    for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'):
        value = fields.get(key, '')
        if not re.fullmatch(r'[0-9a-fA-F]{16}', value):
            raise ValueError('malformed ' + key)
        identity[key] = value.lower()
    identity['no_new_privs'] = fields.get('NoNewPrivs') == '1'
    identity['name'] = fields.get('Name', '')
    return identity


def capability_names(mask_hex):
    mask = int(mask_hex, 16)
    return [CAPABILITIES.get(bit, f'cap_{bit}') for bit in range(64) if mask >> bit & 1]


def process_candidates(pattern):
    """PIDs whose executable name or first argument names the runner process."""
    found = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / 'cmdline').read_bytes().split(b'\0')[0].decode('utf-8', 'replace')
            comm = (entry / 'comm').read_text().strip()
        except OSError:
            continue
        if pattern in comm or pattern in Path(command).name or pattern in command:
            found.append(int(entry.name))
    return sorted(found)


def process_observation(pids, account):
    """The running runner's identity; ambiguous or unreadable identities fail closed."""
    if not pids:
        return not_observed('runner process not running or not found; the running identity cannot be verified')
    identities = []
    for pid in pids:
        try:
            identity = parse_status(Path(f'/proc/{pid}/status').read_text())
            command = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')[0].decode('utf-8', 'replace')
        except PermissionError:
            return not_observed(f'insufficient permission to read process {pid} status')
        except (OSError, ValueError) as error:
            return not_observed(f'process {pid} status unreadable: {type(error).__name__}')
        identity['pid'] = pid
        identity['argv0'] = command
        identities.append(identity)
    keys = {(item['uid']['effective'], item['gid']['effective'], tuple(item['supplementary_group_ids'])) for item in identities}
    if len(keys) != 1:
        return not_observed('ambiguous process identity: candidate processes run under different identities',
                            pids=[item['pid'] for item in identities])
    first = identities[0]
    record = observed(pids=[item['pid'] for item in identities], argv0=first['argv0'], name=first['name'],
                      uid=first['uid'], gid=first['gid'], supplementary_group_ids=first['supplementary_group_ids'],
                      supplementary_groups=sorted(group_name(gid) for gid in first['supplementary_group_ids']),
                      capabilities={key: capability_names(first[key]) for key in ('CapEff', 'CapPrm', 'CapAmb', 'CapInh')},
                      capability_bounding_set_size=len(capability_names(first['CapBnd'])), no_new_privs=first['no_new_privs'])
    if account['status'] == 'OBSERVED':
        record['matches_configured_account'] = (first['uid']['effective'] == account['uid'] and first['uid']['real'] == account['uid'])
        record['groups_not_configured'] = sorted(group_name(gid) for gid in set(first['supplementary_group_ids']) - set(account['group_ids']))
        record['configured_groups_not_held'] = sorted(group_name(gid) for gid in set(account['group_ids']) - set(first['supplementary_group_ids']) - {account['gid']})
    return record


def process_environment(pid):
    """DOCKER_* keys of the running process; readable only as root or the same user."""
    try:
        raw = Path(f'/proc/{pid}/environ').read_bytes()
    except PermissionError:
        return not_observed('insufficient permission to read the process environment')
    except OSError as error:
        return not_observed('process environment unreadable: ' + type(error).__name__)
    settings = {}
    for item in raw.split(b'\0'):
        key, _, value = item.partition(b'=')
        if key.decode('utf-8', 'replace') in DOCKER_VARIABLES:
            settings[key.decode()] = value.decode('utf-8', 'replace')[:200]
    return observed(settings=settings)


# ----------------------------------------------------------------- sudo

def sudo_observation(name, held_groups=()):
    """Rules listed for the account and sudoers lines naming it or a group it holds."""
    if os.geteuid() != 0:
        return not_observed('sudo rules require root to enumerate')
    record = {}
    if shutil.which('sudo'):
        result = subprocess.run(['sudo', '-n', '-l', '-U', name], capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            rules = [line.strip() for line in result.stdout.splitlines() if line.strip().startswith('(')]
            record['listed'] = rules
        elif 'not allowed' in (result.stdout + result.stderr):
            record['listed'] = []
        else:
            return not_observed('sudo -l failed', exit_code=result.returncode)
    else:
        record['listed'] = []
        record['sudo_binary'] = 'absent'
    mentions = []
    try:
        files = [Path('/etc/sudoers'), *sorted(Path('/etc/sudoers.d').glob('*'))] if Path('/etc/sudoers').exists() else []
        for path in files:
            for number, line in enumerate(path.read_text().splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith('#') or not stripped:
                    continue
                names_user = re.search(r'(^|[\s,])' + re.escape(name) + r'(\s|,|$)', stripped) is not None
                names_group = any(re.search(r'%' + re.escape(group) + r'(\s|,|$)', stripped) for group in held_groups)
                if names_user or names_group:
                    mentions.append({'file': str(path), 'line': number, 'group_rule': names_group and not names_user})
    except PermissionError:
        return not_observed('sudoers files unreadable')
    record['file_mentions'] = mentions
    record['any_rule'] = bool(record['listed'])
    return observed(**record)


# ----------------------------------------------------------------- sockets and ACLs

def parse_posix_acl(raw):
    """Decode system.posix_acl_access: version 2 header, then (tag, perm, id) entries."""
    if len(raw) < 4 or (len(raw) - 4) % 8:
        raise ValueError('malformed ACL')
    version, = struct.unpack('<I', raw[:4])
    if version != 2:
        raise ValueError('unsupported ACL version')
    entries = []
    for offset in range(4, len(raw), 8):
        tag, perm, ident = struct.unpack('<HHI', raw[offset:offset + 8])
        entries.append({'tag': ACL_TAGS.get(tag, str(tag)), 'perm': perm, 'id': None if ident == 0xFFFFFFFF else ident})
    return entries


def acl_observation(path):
    if not hasattr(os, 'getxattr'):
        return not_observed('extended attributes unsupported on this platform')
    try:
        raw = os.getxattr(path, 'system.posix_acl_access', follow_symlinks=False)
    except OSError as error:
        if error.errno in (61, 95):  # ENODATA: no ACL beyond the mode; ENOTSUP: filesystem without ACLs
            return observed(entries=None, note='no extended ACL')
        if error.errno == 13:
            return not_observed('ACL unreadable: permission denied')
        return not_observed('ACL unreadable: errno %d' % error.errno)
    try:
        return observed(entries=parse_posix_acl(raw))
    except ValueError as error:
        return not_observed(str(error))


def inferred_write(info, acl, identity):
    """Whether identity may write, from mode bits and any extended ACL (mask applied)."""
    mode = stat.S_IMODE(info.st_mode)
    uid, gid, groups = identity['uid'], identity['gid'], set(identity['groups'])
    if uid == 0:
        return True
    entries = acl.get('entries') if acl.get('status') == 'OBSERVED' else None
    if not entries:
        if uid == info.st_uid:
            return bool(mode & 0o200)
        if gid == info.st_gid or info.st_gid in groups:
            return bool(mode & 0o020)
        return bool(mode & 0o002)
    mask = next((entry['perm'] for entry in entries if entry['tag'] == 'mask'), 7)
    if uid == info.st_uid:
        return bool(next(entry['perm'] for entry in entries if entry['tag'] == 'user_obj') & 2)
    for entry in entries:
        if entry['tag'] == 'user' and entry['id'] == uid:
            return bool(entry['perm'] & mask & 2)
    group_hits = [entry['perm'] for entry in entries if (entry['tag'] == 'group_obj' and (gid == info.st_gid or info.st_gid in groups))
                  or (entry['tag'] == 'group' and entry['id'] in groups | {gid})]
    if group_hits:
        return any(perm & mask & 2 for perm in group_hits)
    return bool(next(entry['perm'] for entry in entries if entry['tag'] == 'other') & 2)


def demonstrate_connect(path, identity):
    """Connect to the socket as the runner identity in a child; send nothing."""
    current = (os.geteuid(), os.getegid())
    same = current[0] == identity['uid'] and current[1] == identity['gid'] and set(os.getgroups()) == set(identity['groups'])
    if os.geteuid() != 0 and not same:
        return not_observed('cannot assume the runner identity without root')
    read_end, write_end = os.pipe()
    pid = os.fork()
    if pid == 0:  # child: drop to the runner identity, connect, report errno
        try:
            os.close(read_end)
            if not same:
                os.setgroups(identity['groups'])
                os.setgid(identity['gid'])
                os.setuid(identity['uid'])
            with socket.socket(socket.AF_UNIX) as sock:
                sock.settimeout(3)
                sock.connect(path)
            os.write(write_end, b'0')
        except OSError as error:
            os.write(write_end, str(error.errno or 1).encode())
        finally:
            os._exit(0)
    os.close(write_end)
    result = os.read(read_end, 32).decode() or '1'
    os.close(read_end)
    os.waitpid(pid, 0)
    if result == '0':
        return observed(connected=True, permission_denied=False, no_listener=False)
    code = int(result)
    # EACCES/EPERM: the filesystem refused the identity. ECONNREFUSED/ENOENT: the
    # path was opened but no daemon listens, so the permission itself exists.
    return observed(connected=False, errno=code, permission_denied=code in (1, 13), no_listener=code in (2, 111))


def socket_observation(path, identity):
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return observed(present=False)
    except PermissionError:
        return not_observed('socket path unreadable: permission denied')
    if stat.S_ISLNK(info.st_mode):
        return observed(present=True, socket=False, symlink=True)
    if not stat.S_ISSOCK(info.st_mode):
        return observed(present=True, socket=False)
    acl = acl_observation(path)
    record = observed(present=True, socket=True, mode=oct(stat.S_IMODE(info.st_mode)), owner_uid=info.st_uid,
                      group=group_name(info.st_gid), acl=acl)
    if identity is None:
        record['inferred_write'] = None
        record['demonstrated'] = not_observed('runner identity not established')
        return record
    record['inferred_write'] = inferred_write(info, acl, identity) if acl['status'] == 'OBSERVED' else None
    record['demonstrated'] = demonstrate_connect(path, identity)
    return record


# ----------------------------------------------------------------- service configuration

def env_settings_from_lines(lines):
    found = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        key, _, value = stripped.partition('=')
        if key.strip() in DOCKER_VARIABLES:
            found[key.strip()] = value.strip().strip('"')[:200]
    return found


def unit_files(unit, systemd_root):
    roots = [Path(systemd_root)] if systemd_root else [Path(d) for d in SYSTEMD_DIRECTORIES]
    fragments, dropins = [], []
    for root in roots:
        for path in sorted(root.glob(unit + '.service')) if '*' in unit else [root / (unit + '.service')]:
            if path.is_file():
                fragments.append(path)
        for directory in sorted(root.glob(unit + '.service.d')):
            dropins.extend(sorted(directory.glob('*.conf')))
    return fragments, dropins


def service_observation(unit, systemd_root, runner_root):
    """Effective unit configuration from files (fragment, drop-ins, environment files) plus the runner .env."""
    fragments, dropins = unit_files(unit, systemd_root)
    if not fragments:
        return not_observed('runner service unit not found: ' + unit + '.service')
    record = {'fragments': [str(p) for p in fragments], 'drop_ins': [str(p) for p in dropins], 'environment': {},
              'environment_files': [], 'user': None, 'group': None, 'supplementary_groups': [], 'exec_start': None}
    for path in [*fragments, *dropins]:
        try:
            lines = path.read_text().splitlines()
        except PermissionError:
            return not_observed('unit file unreadable: ' + str(path))
        except OSError as error:
            return not_observed('unit file unreadable: ' + type(error).__name__)
        for line in lines:
            stripped = line.strip()
            key, _, value = stripped.partition('=')
            key = key.strip()
            value = value.strip()
            if key == 'User':
                record['user'] = value
            elif key == 'Group':
                record['group'] = value
            elif key == 'SupplementaryGroups':
                record['supplementary_groups'] = [] if not value else record['supplementary_groups'] + value.split()
            elif key == 'ExecStart':
                record['exec_start'] = value.split()[0] if value else None
            elif key == 'Environment':
                record['environment'].update(env_settings_from_lines(re.findall(r'"[^"]*"|\S+', value)))
            elif key == 'EnvironmentFile':
                optional = value.startswith('-')
                target = Path(value.lstrip('-'))
                try:
                    settings = env_settings_from_lines(target.read_text().splitlines())
                    record['environment_files'].append({'path': str(target), 'settings': settings})
                    record['environment'].update(settings)
                except FileNotFoundError:
                    if not optional:
                        return not_observed('environment file missing: ' + str(target))
                    record['environment_files'].append({'path': str(target), 'absent': True})
                except PermissionError:
                    return not_observed('environment file unreadable: ' + str(target))
    if runner_root is not None:
        env_path = Path(runner_root) / '.env'
        try:
            settings = env_settings_from_lines(env_path.read_text().splitlines())
            record['runner_env_file'] = {'path': str(env_path), 'settings': settings}
            record['environment'].update(settings)
        except FileNotFoundError:
            record['runner_env_file'] = {'path': str(env_path), 'absent': True}
        except PermissionError:
            return not_observed('runner .env unreadable: ' + str(env_path))
    return observed(**record)


def systemctl_main_pid(unit):
    if not shutil.which('systemctl'):
        return None
    result = subprocess.run(['systemctl', 'show', '--property=MainPID', unit + '.service'], capture_output=True, text=True, timeout=20)
    match = re.search(r'MainPID=(\d+)', result.stdout) if result.returncode == 0 else None
    return int(match.group(1)) if match and int(match.group(1)) > 0 else None


# ----------------------------------------------------------------- docker configuration of the intended account

def docker_config_observation(config_dir):
    record = {'directory': str(config_dir)}
    path = Path(config_dir) / 'config.json'
    try:
        data = json.loads(path.read_text())
        record['config_json'] = {'present': True, 'currentContext': data.get('currentContext') if isinstance(data, dict) else None,
                                 'stored_auths': len(data.get('auths', {})) if isinstance(data, dict) and isinstance(data.get('auths'), dict) else 0}
    except FileNotFoundError:
        record['config_json'] = {'present': False}
    except PermissionError:
        return not_observed('docker config.json unreadable: permission denied', **record)
    except (OSError, ValueError):
        return not_observed('docker config.json unreadable or malformed', **record)
    contexts = []
    meta_root = Path(config_dir) / 'contexts/meta'
    try:
        if meta_root.is_dir():
            for meta in sorted(meta_root.glob('*/meta.json')):
                data = json.loads(meta.read_text())
                host = data.get('Endpoints', {}).get('docker', {}).get('Host') if isinstance(data, dict) else None
                contexts.append({'name': data.get('Name') if isinstance(data, dict) else None, 'endpoint': host})
    except PermissionError:
        return not_observed('docker context store unreadable: permission denied', **record)
    except (OSError, ValueError):
        return not_observed('docker context store unreadable or malformed', **record)
    record['contexts'] = contexts
    record['remote_context'] = any(isinstance(c['endpoint'], str) and not c['endpoint'].startswith('unix://') for c in contexts)
    record['cli'] = {'present': shutil.which('docker') is not None or any(Path(p).exists() for p in ('/usr/bin/docker', '/usr/local/bin/docker'))}
    return observed(**record)


# ----------------------------------------------------------------- workflows

def docker_workflows(root):
    found = []
    for path in sorted((root / '.github/workflows').glob('*.yml')):
        text = path.read_text()
        commands = [line for line in text.splitlines() if not line.strip().startswith('#')
                    and ('docker' in line.lower() or any(tool in line for tool in DOCKER_TOOLS))]
        if commands:
            found.append({'workflow': path.name, 'docker_lines': len(commands),
                          'manual_and_variable_gated': 'workflow_dispatch' in text and 'vars.' in text})
    return found


# ----------------------------------------------------------------- evaluation

def evaluate(report, policy):
    deviations, incomplete = [], []
    account, process = report['account'], report['runner_process']
    if account['status'] != 'OBSERVED':
        incomplete.append('account: ' + account['reason'])
    if process['status'] != 'OBSERVED':
        incomplete.append('runner_process: ' + process['reason'])
    else:
        if account['status'] == 'OBSERVED' and not process.get('matches_configured_account', False):
            deviations.append('running runner identity is not the configured account')
        held = set(process['supplementary_groups']) | {group_name(process['gid']['effective'])}
        if 'docker' in held and policy == 'docker-free':
            deviations.append('running runner holds the docker group')
        other = sorted(g for g in held if g in HOST_GROUPS and g != 'docker')
        if other:
            deviations.append('running runner holds host-capable groups: ' + ', '.join(other))
        if process.get('groups_not_configured'):
            deviations.append('running runner holds groups the account is not configured with: ' + ', '.join(process['groups_not_configured']))
        held_caps = sorted(set(process['capabilities']['CapEff']) | set(process['capabilities']['CapPrm']) | set(process['capabilities']['CapAmb']))
        if held_caps:
            deviations.append('running runner holds capabilities: ' + ', '.join(held_caps))
    if account['status'] == 'OBSERVED':
        if 'docker' in account['groups'] and policy == 'docker-free':
            deviations.append('configured account is a member of the docker group')
        other = sorted(g for g in account['groups'] if g in HOST_GROUPS and g != 'docker')
        if other:
            deviations.append('configured account holds host-capable groups: ' + ', '.join(other))
    sudo = report['sudo']
    if sudo['status'] != 'OBSERVED':
        incomplete.append('sudo: ' + sudo['reason'])
    elif sudo['any_rule'] or sudo['file_mentions']:
        deviations.append('account has sudo rules or sudoers mentions')
    for path, item in report['sockets'].items():
        if item['status'] != 'OBSERVED':
            incomplete.append(f'socket {path}: ' + item['reason'])
            continue
        if not item.get('socket'):
            continue
        if item['acl']['status'] != 'OBSERVED':
            incomplete.append(f'socket {path} ACL: ' + item['acl']['reason'])
        demonstrated = item['demonstrated']
        if demonstrated['status'] != 'OBSERVED':
            incomplete.append(f'socket {path} access not demonstrated: ' + demonstrated['reason'])
            continue
        reachable = not demonstrated.get('permission_denied', not demonstrated.get('connected'))
        if policy == 'docker-free' and demonstrated.get('connected'):
            deviations.append('runner identity can connect to the container socket ' + path)
        elif policy == 'docker-free' and reachable and demonstrated.get('no_listener'):
            deviations.append('runner identity may open the container socket ' + path + ' (no daemon listening now)')
        if item.get('inferred_write') is not None and bool(item['inferred_write']) != reachable:
            deviations.append(f'socket {path}: inferred permission and demonstrated access disagree')
    service = report['service']
    if service['status'] != 'OBSERVED':
        incomplete.append('service: ' + service['reason'])
    else:
        if service['environment']:
            deviations.append('runner service environment carries Docker daemon settings: ' + ', '.join(sorted(service['environment'])))
        if service['supplementary_groups']:
            groups = [g for g in service['supplementary_groups'] if g == 'docker' and policy == 'docker-free' or g in HOST_GROUPS and g != 'docker']
            if groups:
                deviations.append('runner unit grants host-capable groups: ' + ', '.join(sorted(groups)))
        if account['status'] == 'OBSERVED' and service['user'] not in (None, account['name'], str(account['uid'])):
            deviations.append('runner unit runs as a different user than the configured account')
    process_env = report['runner_process_environment']
    if process_env['status'] != 'OBSERVED':
        incomplete.append('runner_process_environment: ' + process_env['reason'])
    elif process_env['settings']:
        deviations.append('running runner environment carries Docker daemon settings: ' + ', '.join(sorted(process_env['settings'])))
    config = report['docker_config']
    if config['status'] != 'OBSERVED':
        incomplete.append('docker_config: ' + config['reason'])
    else:
        if config['remote_context']:
            deviations.append('the account docker context store names a remote daemon')
        current = config['config_json'].get('currentContext') if config['config_json'].get('present') else None
        if current not in (None, 'default'):
            deviations.append('the account docker config selects a non-default context')
    if deviations:
        return 'DEVIATES', deviations, incomplete
    return ('INCOMPLETE' if incomplete else 'CONFORMS'), deviations, incomplete


def build_report(args, environment=None):
    account = account_observation(args.account)
    pids = [args.runner_pid] if args.runner_pid else []
    main_pid = None if args.systemd_root else systemctl_main_pid(args.unit)
    if not pids and main_pid:
        pids = [main_pid]
    if not pids:
        pids = process_candidates(args.runner_process)
    process = process_observation(pids, account)
    if process['status'] == 'OBSERVED':
        identity = {'uid': process['uid']['effective'], 'gid': process['gid']['effective'], 'groups': process['supplementary_group_ids']}
        identity_source = 'running runner process'
        process_env = process_environment(process['pids'][0])
    elif account['status'] == 'OBSERVED':
        identity = {'uid': account['uid'], 'gid': account['gid'], 'groups': account['group_ids']}
        identity_source = 'configured account (runner process not observed)'
        process_env = not_observed('runner process not observed')
    else:
        identity, identity_source, process_env = None, None, not_observed('runner process not observed')
    service = service_observation(args.unit, args.systemd_root, args.runner_root)
    effective_env = dict(service.get('environment', {})) if service['status'] == 'OBSERVED' else {}
    if process_env['status'] == 'OBSERVED':
        effective_env.update(process_env['settings'])
    config_dir = effective_env.get('DOCKER_CONFIG') or (Path(account['home']) / '.docker' if account['status'] == 'OBSERVED' else None)
    report = {
        'schema_version': 2, 'scope': 'read-only host capability verification; changes nothing; contacts no daemon',
        'policy': args.policy, 'verifier': {'euid': os.geteuid(), 'root': os.geteuid() == 0},
        'account': account, 'runner_process': process, 'identity_used_for_socket_checks': identity_source,
        'runner_process_environment': process_env,
        'sudo': sudo_observation(args.account, tuple(sorted(set(process.get('supplementary_groups', [])) | set(account.get('groups', []))))
                                 if account['status'] == 'OBSERVED' else ()),
        'sockets': {path: socket_observation(path, identity) for path in args.sockets},
        'service': service,
        'docker_config': docker_config_observation(config_dir) if config_dir is not None else not_observed('account home unknown'),
        'docker_workflows': docker_workflows(ROOT),
    }
    report['result'], report['deviations'], report['incomplete'] = evaluate(report, args.policy)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--account', default='zabalack-runner')
    parser.add_argument('--unit', default='actions.runner.*', help='systemd unit name without .service (glob allowed)')
    parser.add_argument('--systemd-root', default=None, help='parse unit files under this directory instead of the system directories (tests)')
    parser.add_argument('--runner-root', type=Path, default=Path('/opt/zabalack-actions'))
    parser.add_argument('--runner-process', default='Runner.Listener', help='process name selecting the runner when no unit MainPID or --runner-pid is available')
    parser.add_argument('--runner-pid', type=int, default=None)
    parser.add_argument('--socket', dest='sockets', action='append', default=None, help='container socket to check (repeatable; default: the Docker and Podman sockets)')
    parser.add_argument('--policy', choices=['docker-free', 'docker-allowed'], default='docker-free')
    args = parser.parse_args(argv)
    if args.sockets is None:
        args.sockets = list(SOCKETS)
    report = build_report(args)
    print(json.dumps(report, indent=2, sort_keys=True))
    return {'CONFORMS': 0, 'DEVIATES': 1, 'INCOMPLETE': 2}[report['result']]


if __name__ == '__main__':
    sys.exit(main())
