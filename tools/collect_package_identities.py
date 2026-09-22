#!/usr/bin/env python3
"""Collect, validate and propose the installed service package identities for the native labs (R29).

`collect` records, for the executables the native labs use on this host, what
dpkg, apt and the executables themselves report: package name, exact version,
architecture and status; the repository origin apt attributes to the installed
version; the signing key that verified that origin's InRelease file; dpkg's
verification of the package's installed files; the executable's SHA-256 and
its version line. Nothing else is read: no package inventory, no hostname, no
environment dump. Every item is OBSERVED or NOT_OBSERVED with a reason.

`validate` checks a collected record against the pinned Zabbix reference
(lab/zabbix_reference.json) and the service policy
(lab/native_service_identities.json) and reports CONFORMS, DEVIATES or
INCOMPLETE (exit 0, 1, 2). A missing observation is INCOMPLETE, never a pass;
a source build, an unexpected origin, an unverified signature, a failed file
verification or a version outside the pin is a named deviation.

`approve` turns a record that validates CONFORMS into the reviewed-identity
entries the two policy files accept and writes them to a separate proposal
file for review, with the record's digest, the reviewer and the amendment
that will carry the review. It never edits the policy files, and it refuses
anything but a complete, conforming record: no assumed version, synthetic
fixture or unreviewed source build becomes an approved identity.
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
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / 'lab/zabbix_reference.json'
IDENTITIES = ROOT / 'lab/native_service_identities.json'
APT_LISTS = Path('/var/lib/apt/lists')
KEYRING_DIRS = (Path('/etc/apt/trusted.gpg.d'), Path('/etc/apt/keyrings'), Path('/usr/share/keyrings'))
OS_RELEASE = Path('/etc/os-release')

# name -> how the executable (or file) is found and which version line it prints.
COMPONENTS = {
    'postgres': {'env': 'ACK_LAB_PG_BIN', 'default': '/usr/lib/postgresql/18/bin', 'member': 'postgres', 'version': ['--version'],
                 'origins': ('apt.postgresql.org',)},
    'pgbouncer': {'env': 'ACK_LAB_PGBOUNCER_BIN', 'default': '/usr/sbin/pgbouncer', 'version': ['--version'],
                  'origins': ('apt.postgresql.org', '.ubuntu.com')},
    'zabbix_server': {'env': 'ZABALACK_ZABBIX_SERVER', 'default': '/usr/sbin/zabbix_server', 'version': ['-V'],
                      'origins': ('repo.zabbix.com',)},
    'zabbix_frontend': {'env': 'ZABALACK_ZABBIX_UI', 'default': '/usr/share/zabbix', 'member': 'include/defines.inc.php',
                        'origins': ('repo.zabbix.com',)},
    'zabbix_sql_scripts': {'env': 'ZABALACK_ZABBIX_SCHEMA', 'default': '/usr/share/zabbix-sql-scripts/postgresql/server.sql.gz',
                           'origins': ('repo.zabbix.com',)},
    'php': {'env': 'ZABALACK_PHP', 'default': '/usr/bin/php8.3', 'version': ['-v'], 'origins': ('.ubuntu.com',)},
}
ZABBIX_PACKAGES = {'zabbix_server': 'zabbix-server-pgsql', 'zabbix_frontend': 'zabbix-frontend-php', 'zabbix_sql_scripts': 'zabbix-sql-scripts'}
POLICY_LINE = re.compile(r'^\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+Packages$')


def command(argv: list[str], timeout: int = 60) -> subprocess.CompletedProcess | None:
    """Run a read-only host command; None when the executable is unavailable."""
    if shutil.which(argv[0]) is None and not Path(argv[0]).is_file():
        return None
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env={'PATH': os.environ.get('PATH', ''), 'LC_ALL': 'C'})
    except (OSError, subprocess.SubprocessError):
        return None


def observed(**fields) -> dict:
    return {'status': 'OBSERVED', **fields}


def not_observed(reason: str) -> dict:
    return {'status': 'NOT_OBSERVED', 'reason': reason}


def component_path(name: str, spec: dict) -> Path:
    base = Path(os.environ.get(spec['env']) or spec['default'])
    return base / spec['member'] if spec.get('member') else base


DOCUMENTATION = ('/usr/share/doc/', '/usr/share/man/', '/usr/share/lintian/', '/usr/share/locale/', '/usr/share/info/')


def executable_observation(path: Path, spec: dict) -> dict:
    if path.is_dir():
        # A schema directory (schema.sql, images.sql, data.sql) instead of the packaged file.
        return observed(path=str(path), real_path=str(path.resolve()), sha256=None, directory=True, version_line=None)
    if not path.is_file():
        return not_observed('missing: ' + str(path))
    real = path.resolve()
    with real.open('rb') as handle:
        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
    record = observed(path=str(path), real_path=str(real), sha256=digest)
    if spec.get('version'):
        result = command([str(real), *spec['version']])
        if result is None or result.returncode != 0 or not result.stdout.strip():
            record['version_line'] = None
            record['version_reason'] = 'version command failed'
        else:
            record['version_line'] = result.stdout.strip().splitlines()[0]
    elif path.name == 'defines.inc.php':
        match = re.search(r"define\('ZABBIX_VERSION',\s*'([^']+)'\)", path.read_text(errors='replace'))
        record['version_line'] = match.group(1) if match else None
    return record


def dpkg_observation(real: Path) -> dict:
    owner = command(['dpkg', '-S', str(real)])
    if owner is None:
        return not_observed('dpkg unavailable')
    if owner.returncode != 0 or ': ' not in owner.stdout:
        return observed(kind='unpackaged')
    package = owner.stdout.strip().splitlines()[0].rsplit(': ', 1)[0].split(':')[0].split(',')[0].strip()
    query = command(['dpkg-query', '-W', '-f', '${Package}\t${Version}\t${Architecture}\t${Status}', package])
    if query is None or query.returncode != 0 or query.stdout.count('\t') != 3:
        return not_observed('dpkg-query failed for ' + package)
    name, version, architecture, status = query.stdout.split('\t')
    verify = command(['dpkg', '-V', package], timeout=300)
    verification = not_observed('dpkg -V unavailable') if verify is None else verification_observation(verify.stdout)
    return observed(kind='dpkg', package=name, version=version, architecture=architecture, package_status=status.strip(),
                    file_verification=verification)


def verification_observation(text: str) -> dict:
    """dpkg -V lines: `<flags> [c] <path>` for a changed file or `missing   <path>` for an absent one.

    Documentation paths (man pages, doc, locale) are commonly excluded by a
    host's dpkg path-exclude rules; they are recorded but do not make the
    package's executables unverified. Any other changed or missing file does.
    """
    entries = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        path = parts[-1]
        entries.append({'flags': parts[0], 'path': path, 'documentation': path.startswith(DOCUMENTATION)})
    return observed(clean=not entries, executables_verified=not any(not entry['documentation'] for entry in entries),
                    changed_files=entries[:50])


def parse_policy(text: str) -> dict:
    """The installed version and the origins apt attributes to it, from `apt-cache policy <package>`."""
    installed = None
    origins: list[dict] = []
    in_installed_block = False
    for line in text.splitlines():
        match = re.match(r'^\s+Installed:\s+(\S+)$', line)
        if match:
            installed = None if match.group(1) == '(none)' else match.group(1)
            continue
        version = re.match(r'^\s+(\*\*\*|)\s*(\S+)\s+(-?\d+)$', line)
        if version:
            in_installed_block = version.group(1) == '***'
            continue
        origin = POLICY_LINE.match(line)
        if origin and in_installed_block:
            origins.append({'priority': int(origin.group(1)), 'uri': origin.group(2), 'suite': origin.group(3),
                            'architecture': origin.group(4)})
    return {'installed': installed, 'origins': origins}


def apt_observation(package: str) -> dict:
    result = command(['apt-cache', 'policy', package])
    if result is None or result.returncode != 0:
        return not_observed('apt-cache policy unavailable for ' + package)
    parsed = parse_policy(result.stdout)
    if parsed['installed'] is None:
        return not_observed('apt-cache policy reports no installed version for ' + package)
    return observed(**parsed)


def inrelease_path(uri: str, suite: str, lists: Path | None = None) -> Path:
    """apt's list file name for an origin: scheme dropped, '/' mangled to '_', dists/<dist>/InRelease."""
    stripped = re.sub(r'^[a-z][a-z0-9+.-]*://', '', uri).rstrip('/')
    dist = suite.split('/', 1)[0]
    return (APT_LISTS if lists is None else lists) / (stripped.replace('/', '_') + '_dists_' + dist + '_InRelease')


def keyrings() -> list[Path]:
    found = []
    for directory in KEYRING_DIRS:
        if directory.is_dir():
            found.extend(sorted(p for p in directory.iterdir() if p.is_file() and p.suffix in ('.gpg', '.asc', '.pgp')))
    return found


def dearmored(keyring: Path, scratch: Path) -> Path | None:
    with keyring.open('rb') as handle:
        head = handle.read(16)
    if not head.startswith(b'-----BEGIN'):
        return keyring
    if shutil.which('gpg') is None:
        return None
    target = scratch / (keyring.name + '.gpg')
    result = subprocess.run(['gpg', '--batch', '--yes', '--dearmor', '-o', str(target), str(keyring)], capture_output=True, timeout=60)
    return target if result.returncode == 0 else None


def signature_observation(uri: str, suite: str, scratch: Path, lists: Path | None = None) -> dict:
    path = inrelease_path(uri, suite, lists)
    if not path.is_file():
        return not_observed('InRelease list file missing: ' + path.name)
    if shutil.which('gpgv') is None:
        return not_observed('gpgv unavailable')
    good: list[dict] = []
    tried = 0
    for keyring in keyrings():
        usable = dearmored(keyring, scratch)
        if usable is None:
            continue
        tried += 1
        result = subprocess.run(['gpgv', '--status-fd', '1', '--keyring', str(usable), str(path)], capture_output=True, text=True, timeout=60)
        long_ids = re.findall(r'^\[GNUPG:\] GOODSIG ([0-9A-F]{16}) (.*)$', result.stdout, re.M)
        fingerprints = re.findall(r'^\[GNUPG:\] VALIDSIG ([0-9A-F]{40}) ', result.stdout, re.M)
        for (long_id, user_id), fingerprint in zip(long_ids, fingerprints or [None] * len(long_ids), strict=False):
            good.append({'long_id': long_id, 'fingerprint': fingerprint, 'user_id': user_id, 'keyring': keyring.name})
    if not tried:
        return not_observed('no usable keyring under ' + ', '.join(str(d) for d in KEYRING_DIRS))
    if not good:
        return observed(inrelease=path.name, verified=False, keyrings_tried=tried)
    return observed(inrelease=path.name, verified=True, keys=good, keyrings_tried=tried)


def distribution() -> dict:
    record: dict = {}
    if OS_RELEASE.is_file():
        for line in OS_RELEASE.read_text().splitlines():
            if '=' in line:
                key, value = line.split('=', 1)
                if key in ('ID', 'VERSION_ID'):
                    record[key.lower()] = value.strip().strip('"')
    result = command(['dpkg', '--print-architecture'])
    record['architecture'] = result.stdout.strip() if result is not None and result.returncode == 0 else None
    return record


def collect(label: str, scratch: Path) -> dict:
    record: dict = {'schema_version': 1, 'tool': 'tools/collect_package_identities.py', 'label': label,
                    'collected_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'distribution': distribution(),
                    'components': {}, 'missing_observations': []}
    for name, spec in COMPONENTS.items():
        path = component_path(name, spec)
        item: dict = {'executable': executable_observation(path, spec)}
        if item['executable']['status'] == 'OBSERVED':
            item['dpkg'] = dpkg_observation(Path(item['executable']['real_path']))
            if item['dpkg']['status'] == 'OBSERVED' and item['dpkg'].get('kind') == 'dpkg':
                item['apt'] = apt_observation(item['dpkg']['package'])
                if item['apt']['status'] == 'OBSERVED':
                    item['signatures'] = [dict(origin, signature=signature_observation(origin['uri'], origin['suite'], scratch))
                                          for origin in item['apt']['origins'] if not origin['uri'].startswith('/')]
        record['components'][name] = item
        for key in ('executable', 'dpkg', 'apt'):
            if item.get(key, {}).get('status') == 'NOT_OBSERVED':
                record['missing_observations'].append(f'{name}.{key}: ' + item[key]['reason'])
        for entry in item.get('signatures', []):
            if entry['signature']['status'] == 'NOT_OBSERVED':
                record['missing_observations'].append(f'{name}.signature[{entry["uri"]}]: ' + entry['signature']['reason'])
    return record


def version_tuple(line: str | None) -> tuple[int, ...] | None:
    match = re.search(r'(\d+)\.(\d+)(?:\.(\d+))?', line or '')
    return tuple(int(part) for part in match.groups() if part is not None) if match else None


def origin_host(uri: str) -> str:
    return re.sub(r'^[a-z][a-z0-9+.-]*://', '', uri).split('/', 1)[0].lower()


def evaluate(record: dict, reference: dict, identities: dict) -> tuple[str, list[str], list[str]]:
    """CONFORMS / DEVIATES / INCOMPLETE with the named deviations and missing observations."""
    deviations: list[str] = []
    incomplete: list[str] = list(record.get('missing_observations', []))
    selected = reference['selected']['version']
    expected_key = reference['ubuntu_packages']['expected_signing_key']['long_id']
    pgdg_fingerprint = reference['dependencies']['postgresql']['pgdg_key_fingerprint']
    policy = identities['policy']
    distro = record.get('distribution', {})
    if (distro.get('id'), distro.get('version_id'), distro.get('architecture')) != ('ubuntu', '24.04', 'amd64'):
        deviations.append('distribution: expected Ubuntu 24.04 amd64, observed ' + json.dumps(distro, sort_keys=True))
    components = record.get('components', {})
    versions: dict[str, str | None] = {}
    for name, spec in COMPONENTS.items():
        item = components.get(name)
        if item is None:
            incomplete.append(name + ': not collected')
            continue
        executable = item.get('executable', {})
        if executable.get('status') != 'OBSERVED':
            continue  # already listed as missing
        versions[name] = executable.get('version_line')
        if ('version' in spec or spec.get('member') == 'include/defines.inc.php') and not versions[name]:
            incomplete.append(name + ': version not observed')
        dpkg = item.get('dpkg', {})
        if dpkg.get('status') != 'OBSERVED':
            continue
        if dpkg.get('kind') != 'dpkg':
            deviations.append(name + ': unpackaged executable (source build); packages are required for an approved identity')
            continue
        if dpkg.get('architecture') != 'amd64':
            deviations.append(f'{name}: architecture {dpkg.get("architecture")}, expected amd64')
        if str(dpkg.get('package_status', '')).split()[-1:] != ['installed']:
            deviations.append(f'{name}: dpkg status "{dpkg.get("package_status")}" is not installed')
        verification = dpkg.get('file_verification', {})
        if verification.get('status') != 'OBSERVED':
            incomplete.append(name + ': dpkg file verification not observed')
        elif not verification.get('executables_verified'):
            changed = [entry['path'] for entry in verification.get('changed_files', []) if not entry.get('documentation')]
            deviations.append(name + ': dpkg -V reports changed or missing package files: ' + ', '.join(changed[:5]))
        apt = item.get('apt', {})
        if apt.get('status') != 'OBSERVED':
            continue
        if apt.get('installed') != dpkg.get('version'):
            deviations.append(f'{name}: apt installed {apt.get("installed")} differs from dpkg {dpkg.get("version")}')
        hosts = [origin_host(origin['uri']) for origin in apt.get('origins', []) if not origin['uri'].startswith('/')]
        if not hosts:
            deviations.append(name + ': no repository origin attributed to the installed version (local install?)')
        elif not any(host == suffix or host.endswith(suffix) for host in hosts for suffix in spec['origins']):
            deviations.append(f'{name}: origin {hosts} is not one of {list(spec["origins"])}')
        signatures = item.get('signatures', [])
        verified = [entry for entry in signatures if entry['signature'].get('status') == 'OBSERVED' and entry['signature'].get('verified')]
        unobserved = [entry for entry in signatures if entry['signature'].get('status') != 'OBSERVED']
        if not signatures:
            incomplete.append(name + ': no origin signature observation')
        elif not verified and unobserved:
            incomplete.append(name + ': origin signature not observed (' + '; '.join(
                str(entry['signature'].get('reason')) for entry in unobserved) + ')')
        elif not verified:
            deviations.append(name + ': no origin InRelease verified against an installed keyring')
        for entry in verified:
            host = origin_host(entry['uri'])
            keys = entry['signature'].get('keys', [])
            if host == 'repo.zabbix.com' and not any(key['long_id'] == expected_key for key in keys):
                deviations.append(f'{name}: repo.zabbix.com InRelease signed by {[k["long_id"] for k in keys]}, expected {expected_key}')
            if host == 'apt.postgresql.org' and not any(key.get('fingerprint') == pgdg_fingerprint for key in keys):
                deviations.append(f'{name}: apt.postgresql.org InRelease not signed by the PGDG key {pgdg_fingerprint}')
    # Version policy.
    postgres = version_tuple(versions.get('postgres'))
    if postgres and postgres[0] != policy['postgres']['major']:
        deviations.append(f'postgres: major {postgres[0]}, policy requires {policy["postgres"]["major"]}')
    pool = version_tuple(versions.get('pgbouncer'))
    if pool and (pool[0] != policy['pgbouncer']['major'] or pool[1] < policy['pgbouncer']['minimum_minor']):
        deviations.append(f'pgbouncer: {".".join(map(str, pool))}, policy requires {policy["pgbouncer"]["major"]}.{policy["pgbouncer"]["minimum_minor"]} or newer')
    server = versions.get('zabbix_server')
    if server and not re.search(r'\(Zabbix\) ' + re.escape(selected) + r'\b', server):
        deviations.append(f'zabbix_server: "{server}" is not the pinned {selected}')
    frontend = versions.get('zabbix_frontend')
    if frontend and frontend != selected:
        deviations.append(f'zabbix_frontend: ZABBIX_VERSION {frontend} is not the pinned {selected}')
    php = version_tuple(versions.get('php'))
    minimum = version_tuple(reference['dependencies']['php']['minimum'])
    if php and minimum and php < minimum:
        deviations.append(f'php: {".".join(map(str, php))} is below the frontend minimum {reference["dependencies"]["php"]["minimum"]}')
    zabbix_versions = {name: components.get(name, {}).get('dpkg', {}).get('version') for name in ZABBIX_PACKAGES}
    pattern = re.compile(r'^1:' + re.escape(selected) + r'-\d+\+ubuntu24\.04$')
    for name, version in zabbix_versions.items():
        if version and not pattern.match(version):
            deviations.append(f'{name}: dpkg version {version} does not match 1:{selected}-<revision>+ubuntu24.04')
        expected_package = ZABBIX_PACKAGES[name]
        package = components.get(name, {}).get('dpkg', {}).get('package')
        if package and package != expected_package:
            deviations.append(f'{name}: package {package}, expected {expected_package}')
    distinct = {version for version in zabbix_versions.values() if version}
    if len(distinct) > 1:
        deviations.append('zabbix packages carry different versions: ' + ', '.join(sorted(distinct)))
    if incomplete:
        return 'INCOMPLETE', deviations, incomplete
    return ('DEVIATES' if deviations else 'CONFORMS'), deviations, incomplete


def proposal(record: dict, record_digest: str, reviewer: str, amendment: str) -> dict:
    """Reviewed-identity entries, taken verbatim from the record's observations."""
    def entry(name: str) -> dict:
        dpkg = record['components'][name]['dpkg']
        return {'kind': 'dpkg', 'package': dpkg['package'], 'version': dpkg['version'], 'architecture': dpkg['architecture']}

    def signer(name: str) -> dict:
        verified = [entry_ for entry_ in record['components'][name].get('signatures', []) if entry_['signature'].get('verified')]
        return {'origins': [{'uri': v['uri'], 'suite': v['suite'], 'keys': v['signature']['keys']} for v in verified]}
    return {
        'schema_version': 1, 'proposal': 'reviewed identities for lab/native_service_identities.json and lab/zabbix_reference.json',
        'source_record': {'label': record['label'], 'collected_at': record['collected_at'], 'sha256': record_digest},
        'reviewer': reviewer, 'amendment': amendment, 'prepared_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'native_service_identities.reviewed': {'postgres': [entry('postgres')], 'pgbouncer': [entry('pgbouncer')]},
        'native_service_identities.origins': {'postgres': signer('postgres'), 'pgbouncer': signer('pgbouncer')},
        'zabbix_reference.ubuntu_packages.verified': {
            **{ZABBIX_PACKAGES[name]: entry(name) for name in ZABBIX_PACKAGES},
            'origin': signer('zabbix_server'),
            'php': entry('php'),
        },
        'applies_to': 'the host the record was collected on; re-collect after any package change',
    }


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    collect_parser = sub.add_parser('collect', help='record the installed identities of the native lab services on this host')
    collect_parser.add_argument('--output', type=Path, required=True)
    collect_parser.add_argument('--label', default='runner-host', help='a short label for the record (no hostname is read)')
    validate_parser = sub.add_parser('validate', help='check a record against the pin and the policy')
    validate_parser.add_argument('record', type=Path)
    validate_parser.add_argument('--reference', type=Path, default=REFERENCE)
    validate_parser.add_argument('--identities', type=Path, default=IDENTITIES)
    approve_parser = sub.add_parser('approve', help='write the reviewed-identity proposal for a CONFORMS record')
    approve_parser.add_argument('record', type=Path)
    approve_parser.add_argument('--reviewer', required=True)
    approve_parser.add_argument('--amendment', required=True, help='the RELEASE_AMENDMENTS entry that will record the review')
    approve_parser.add_argument('--output', type=Path, required=True)
    approve_parser.add_argument('--reference', type=Path, default=REFERENCE)
    approve_parser.add_argument('--identities', type=Path, default=IDENTITIES)
    args = parser.parse_args(argv)
    if args.command == 'collect':
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', args.label):
            parser.error('label: letters, digits, dot, underscore and dash only')
        scratch = args.output.resolve().parent / ('.' + args.output.name + '.keyrings')
        scratch.mkdir(mode=0o700, exist_ok=True)
        try:
            record = collect(args.label, scratch)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
        print(json.dumps({'output': str(args.output), 'components': len(record['components']),
                          'missing_observations': record['missing_observations']}))
        return 0
    record = load(args.record)
    result, deviations, incomplete = evaluate(record, load(args.reference), load(args.identities))
    if args.command == 'validate':
        print(json.dumps({'result': result, 'deviations': deviations, 'incomplete': incomplete}, indent=2))
        return {'CONFORMS': 0, 'DEVIATES': 1, 'INCOMPLETE': 2}[result]
    if result != 'CONFORMS':
        print(json.dumps({'result': result, 'deviations': deviations, 'incomplete': incomplete}, indent=2))
        raise SystemExit(f'refusing to propose approved identities from a {result} record')
    digest = hashlib.sha256(args.record.read_bytes()).hexdigest()
    args.output.write_text(json.dumps(proposal(record, digest, args.reviewer, args.amendment), indent=2, sort_keys=True) + '\n')
    print(json.dumps({'output': str(args.output), 'source_record_sha256': digest, 'policy_files_changed': False}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
