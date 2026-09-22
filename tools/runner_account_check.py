#!/usr/bin/env python3
"""Read-only verification of the dedicated runner account's host capabilities.

Prepared for the Docker-removal operational handoff (R28). It changes nothing:
it reads the account's effective groups, its sudo rules (when run as root),
the Docker socket's ownership and mode, the Docker CLI and its contexts, every
DOCKER_HOST/DOCKER_CONTEXT/DOCKER_CONFIG setting the runner could inherit (the
current environment, the runner's `.env`, its systemd unit and the account's
`~/.docker/config.json`), other host-capable groups, and the workflows in this
checkout that still invoke Docker. It compares the findings with a policy:

  docker-free      routine CI needs no Docker: the account must not be in the
                   docker group, must not reach a Docker socket or a remote
                   daemon, and must hold no sudo rule
  docker-allowed   the pre-R26 host: Docker group membership is expected and
                   reported, everything else as above

Exit 0 when every completed check conforms, 1 on a deviation, 2 when a check
could not be completed (for example sudo rules without root). The JSON report
names each check, its evidence and its verdict; it contains no secrets.
"""
from __future__ import annotations

import argparse
import grp
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
HOST_GROUPS = ('docker', 'lxd', 'libvirt', 'kvm', 'sudo', 'admin', 'wheel', 'adm', 'disk')
SOCKETS = ('/var/run/docker.sock', '/run/docker.sock', '/run/podman/podman.sock')
DOCKER_VARIABLES = ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_CONFIG', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH')


def account_groups(name: str) -> dict:
    entry = pwd.getpwnam(name)
    ids = sorted(set(os.getgrouplist(name, entry.pw_gid)))
    names = []
    for gid in ids:
        try:
            names.append(grp.getgrgid(gid).gr_name)
        except KeyError:
            names.append(str(gid))
    return {'uid': entry.pw_uid, 'gid': entry.pw_gid, 'home': entry.pw_dir, 'shell': entry.pw_shell, 'groups': sorted(names)}


def sudo_rules(name: str) -> dict:
    if os.geteuid() != 0 or not shutil.which('sudo'):
        return {'status': 'NOT_CHECKED', 'reason': 'requires root and sudo'}
    result = subprocess.run(['sudo', '-n', '-l', '-U', name], capture_output=True, text=True, timeout=30)
    if result.returncode:
        return {'status': 'NOT_CHECKED', 'reason': 'sudo -l failed', 'exit_code': result.returncode}
    text = result.stdout
    rules = [line.strip() for line in text.splitlines() if line.strip().startswith('(')]
    return {'status': 'CHECKED', 'rules': rules, 'any_rule': bool(rules) or 'may run the following' in text
            and 'is not allowed' not in text}


def socket_access(path: str, groups: dict) -> dict:
    try:
        info = os.stat(path)
    except OSError:
        return {'present': False}
    if not stat.S_ISSOCK(info.st_mode):
        return {'present': True, 'socket': False}
    mode = stat.S_IMODE(info.st_mode)
    try:
        group = grp.getgrgid(info.st_gid).gr_name
    except KeyError:
        group = str(info.st_gid)
    reachable = (bool(mode & 0o002) or (group in groups['groups'] and bool(mode & 0o020))
                 or (info.st_uid == groups['uid'] and bool(mode & 0o200)))
    return {'present': True, 'socket': True, 'mode': oct(mode), 'owner_uid': info.st_uid, 'group': group,
            'account_can_write': reachable}


def env_file_settings(path: Path) -> dict:
    found = {}
    try:
        for line in path.read_text().splitlines():
            key, _, value = line.partition('=')
            if key.strip() in DOCKER_VARIABLES:
                found[key.strip()] = value.strip()
    except OSError:
        return {'present': False}
    return {'present': True, 'settings': found}


def unit_settings(pattern: str = 'actions.runner') -> dict:
    units = []
    for directory in ('/etc/systemd/system', '/lib/systemd/system', '/usr/lib/systemd/system'):
        base = Path(directory)
        if not base.is_dir():
            continue
        for unit in sorted(base.glob(f'{pattern}*.service')):
            found = {}
            try:
                for line in unit.read_text().splitlines():
                    if line.startswith('Environment=') or line.startswith('EnvironmentFile='):
                        for key in DOCKER_VARIABLES:
                            if key in line:
                                found[key] = line.strip()
                units.append({'unit': str(unit), 'docker_settings': found})
            except OSError:
                units.append({'unit': str(unit), 'unreadable': True})
    return {'units': units}


def docker_cli() -> dict:
    binary = shutil.which('docker')
    record: dict = {'present': binary is not None, 'path': binary}
    if binary is None:
        return record
    contexts = subprocess.run([binary, 'context', 'ls', '--format', 'json'], capture_output=True, text=True, timeout=20)
    if contexts.returncode == 0:
        rows = []
        for line in contexts.stdout.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append({key: row.get(key) for key in ('Name', 'Current', 'DockerEndpoint')})
        record['contexts'] = rows
        record['remote_context'] = any(isinstance(r.get('DockerEndpoint'), str) and not r['DockerEndpoint'].startswith('unix://')
                                       for r in rows)
    else:
        record['contexts'] = 'NOT_LISTED'
    return record


def docker_config(home: str) -> dict:
    path = Path(home) / '.docker/config.json'
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {'present': False}
    return {'present': True, 'currentContext': data.get('currentContext') if isinstance(data, dict) else None}


DOCKER_TOOLS = ('tools/native_ci.py', 'tools/run_deployment_e2e.py')


def docker_workflows(root: Path) -> list[dict]:
    """Workflows whose commands (not comments) invoke Docker or a Docker-requiring tool."""
    found = []
    for path in sorted((root / '.github/workflows').glob('*.yml')):
        text = path.read_text()
        commands = [line for line in text.splitlines() if not line.strip().startswith('#')
                    and ('docker' in line.lower() or any(tool in line for tool in DOCKER_TOOLS))]
        if commands:
            gated = 'workflow_dispatch' in text and 'vars.' in text
            found.append({'workflow': path.name, 'docker_lines': len(commands), 'manual_and_variable_gated': gated})
    return found


def evaluate(report: dict, policy: str) -> tuple[str, list[str]]:
    findings = []
    groups = report['account']['groups']
    if 'docker' in groups and policy == 'docker-free':
        findings.append('account is a member of the docker group')
    other = [g for g in groups if g in HOST_GROUPS and g != 'docker']
    if other:
        findings.append('account holds host-capable groups: ' + ', '.join(other))
    if report['sudo'].get('status') == 'CHECKED' and report['sudo'].get('any_rule'):
        findings.append('account has sudo rules')
    for path, item in report['sockets'].items():
        if item.get('socket') and item.get('account_can_write') and policy == 'docker-free':
            findings.append('account can write the container socket ' + path)
    settings = {key: value for scope in ('environment', 'runner_env_file') for key, value in
                (report[scope].get('settings') or {}).items()}
    if settings:
        findings.append('Docker daemon settings present: ' + ', '.join(sorted(settings)))
    if any(unit.get('docker_settings') for unit in report['runner_units']['units']):
        findings.append('runner unit carries Docker daemon settings')
    if report['docker_cli'].get('remote_context'):
        findings.append('a Docker context names a remote daemon')
    if report['docker_config'].get('currentContext') not in (None, 'default'):
        findings.append('account docker config selects a non-default context')
    incomplete = report['sudo'].get('status') == 'NOT_CHECKED'
    if findings:
        return 'DEVIATES', findings
    return ('INCOMPLETE' if incomplete else 'CONFORMS'), findings


def build_report(account: str, runner_root: Path, policy: str, environment: dict) -> dict:
    groups = account_groups(account)
    report = {
        'schema_version': 1, 'scope': 'read-only host capability verification; changes nothing', 'policy': policy,
        'account': {'name': account, **groups},
        'sudo': sudo_rules(account),
        'sockets': {path: socket_access(path, groups) for path in SOCKETS},
        'environment': {'settings': {key: environment[key] for key in DOCKER_VARIABLES if key in environment}},
        'runner_env_file': env_file_settings(runner_root / '.env'),
        'runner_units': unit_settings(),
        'docker_cli': docker_cli(),
        'docker_config': docker_config(groups['home']),
        'docker_workflows': docker_workflows(ROOT),
    }
    report['result'], report['findings'] = evaluate(report, policy)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--account', default='zabalack-runner')
    parser.add_argument('--runner-root', type=Path, default=Path('/opt/zabalack-actions'))
    parser.add_argument('--policy', choices=['docker-free', 'docker-allowed'], default='docker-free')
    args = parser.parse_args(argv)
    try:
        report = build_report(args.account, args.runner_root, args.policy, dict(os.environ))
    except KeyError:
        print(json.dumps({'result': 'INCOMPLETE', 'findings': ['account does not exist: ' + args.account]}))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return {'CONFORMS': 0, 'DEVIATES': 1, 'INCOMPLETE': 2}[report['result']]


if __name__ == '__main__':
    sys.exit(main())
