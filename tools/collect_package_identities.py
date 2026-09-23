#!/usr/bin/env python3
"""Collect, validate and propose the installed service package identities for the native labs (R29).

`collect` records, for the executables the native labs use on this host, what
dpkg, apt, the signed repository indexes and the executables themselves
report: package name, exact version, architecture and status; every
repository origin apt attributes to the installed version; the signing key
that verified each origin's InRelease file; the signed index entry of the
installed version (the `.deb` digest and file name, with the Packages index
bound to the InRelease digest list); the cached `.deb` when apt still holds
it; dpkg's verification of the package's installed files, with conffiles and
documentation classified apart from the payload; the executable's SHA-256 and
its version line (for PostgreSQL every required binary). Nothing else is read:
no package inventory, no hostname, no environment dump. Every item is OBSERVED
or NOT_OBSERVED with a reason.

`validate` checks a record against the pinned Zabbix reference
(lab/zabbix_reference.json) and the service policy
(lab/native_service_identities.json) and reports CONFORMS, DEVIATES or
INCOMPLETE (exit 0, 1, 2). It derives the missing observations itself and
never trusts a record's own claim. Every origin supplying the installed
version must be an allowed repository whose InRelease was verified by the
pinned full key fingerprint; `Architecture: all` is accepted only for the
packages the signed index publishes as such; a failed `dpkg -V` is a missing
observation, a changed payload file a deviation, a changed conffile a
recorded difference; a directory is never an executable identity; the
installed version must be the signed index's entry.

`approve` turns a record that validates CONFORMS into the reviewed-identity
entries the two policy files accept and writes them to a separate proposal
file for review, carrying the record's digest, each package's `.deb` digest
and origin fingerprint, the reviewer and the amendment. It never edits the
policy files, and it refuses anything but a complete, conforming record.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
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
APT_CACHE = Path('/var/cache/apt/archives')
KEYRING_DIRS = (Path('/etc/apt/trusted.gpg.d'), Path('/etc/apt/keyrings'), Path('/usr/share/keyrings'))
OS_RELEASE = Path('/etc/os-release')

# name -> how the executable (or file) is found, its version line, the origins allowed, the key class that must sign them.
COMPONENTS = {
    'postgres': {'env': 'ACK_LAB_PG_BIN', 'default': '/usr/lib/postgresql/18/bin', 'member': 'postgres', 'version': ['--version'],
                 'origins': ('apt.postgresql.org',), 'members': ('pg_ctl', 'initdb', 'psql')},
    'pgbouncer': {'env': 'ACK_LAB_PGBOUNCER_BIN', 'default': '/usr/sbin/pgbouncer', 'version': ['--version'],
                  'origins': ('apt.postgresql.org', 'archive.ubuntu.com', 'security.ubuntu.com')},
    'zabbix_server': {'env': 'ZABALACK_ZABBIX_SERVER', 'default': '/usr/sbin/zabbix_server', 'version': ['-V'],
                      'origins': ('repo.zabbix.com',)},
    'zabbix_frontend': {'env': 'ZABALACK_ZABBIX_UI', 'default': '/usr/share/zabbix', 'member': 'include/defines.inc.php',
                        'origins': ('repo.zabbix.com',)},
    'zabbix_sql_scripts': {'env': 'ZABALACK_ZABBIX_SCHEMA', 'default': '/usr/share/zabbix-sql-scripts/postgresql/server.sql.gz',
                           'origins': ('repo.zabbix.com',)},
    'php': {'env': 'ZABALACK_PHP', 'default': '/usr/bin/php8.3', 'version': ['-v'], 'origins': ('archive.ubuntu.com', 'security.ubuntu.com')},
}
ZABBIX_PACKAGES = {'zabbix_server': 'zabbix-server-pgsql', 'zabbix_frontend': 'zabbix-frontend-php', 'zabbix_sql_scripts': 'zabbix-sql-scripts'}
# Packages the signed Zabbix index publishes as Architecture: all (observed 2026-09-23); everything else must be amd64.
ARCH_ALL_PACKAGES = ('zabbix-frontend-php', 'zabbix-sql-scripts')
EXPECTED_PACKAGES = {'postgres': ('postgresql-18',), 'pgbouncer': ('pgbouncer',), 'zabbix_server': ('zabbix-server-pgsql',),
                     'zabbix_frontend': ('zabbix-frontend-php',), 'zabbix_sql_scripts': ('zabbix-sql-scripts',), 'php': ('php8.3-cli',)}
POSTGRES_MEMBER_PACKAGES = ('postgresql-18', 'postgresql-client-18')
DOCUMENTATION = ('/usr/share/doc/', '/usr/share/man/', '/usr/share/lintian/', '/usr/share/locale/', '/usr/share/info/')
POLICY_LINE = re.compile(r'^\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+Packages$')
HEX64 = re.compile(r'[0-9a-f]{64}')


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


def sha256_of(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


# ------------------------------------------------------------------ executables

def executable_observation(path: Path, spec: dict) -> dict:
    if path.is_dir():
        # A directory (for example a schema source tree) is not an identity anyone can sign.
        return not_observed('directory paths are not executable identities: ' + str(path))
    if not path.is_file():
        return not_observed('missing: ' + str(path))
    real = path.resolve()
    record = observed(path=str(path), real_path=str(real), sha256=sha256_of(real))
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


def member_observation(path: Path) -> dict:
    """A required companion binary: presence, digest and owning package."""
    if not path.is_file():
        return not_observed('missing: ' + str(path))
    real = path.resolve()
    owner = dpkg_owner(real)
    record = observed(path=str(path), real_path=str(real), sha256=sha256_of(real))
    if owner is None:
        record['package'] = None
        record['kind'] = 'unpackaged'
        return record
    query = dpkg_query(owner)
    if query is None:
        return not_observed('dpkg-query failed for ' + owner)
    record.update(kind='dpkg', package=query['package'], version=query['version'], architecture=query['architecture'])
    return record


# ------------------------------------------------------------------ dpkg

def dpkg_owner(real: Path) -> str | None:
    """The package owning a path, or None when unpackaged; raises RuntimeError when dpkg is unavailable."""
    owner = command(['dpkg', '-S', str(real)])
    if owner is None:
        raise RuntimeError('dpkg unavailable')
    if owner.returncode != 0 or ': ' not in owner.stdout:
        return None
    return owner.stdout.strip().splitlines()[0].rsplit(': ', 1)[0].split(':')[0].split(',')[0].strip()


def dpkg_query(package: str) -> dict | None:
    query = command(['dpkg-query', '-W', '-f', '${Package}\t${Version}\t${Architecture}\t${Status}', package])
    if query is None or query.returncode != 0 or query.stdout.count('\t') != 3:
        return None
    name, version, architecture, status = query.stdout.split('\t')
    return {'package': name, 'version': version, 'architecture': architecture, 'package_status': status.strip()}


def verification_observation(text: str, returncode: int = 0, stderr: str = '') -> dict:
    """dpkg -V: exit 0 clean; exit 1 with lines means differences; anything else is a failed verification.

    Lines are `<flags> [c] <path>`: `c` marks a conffile, `missing` an absent
    file. Conffiles and documentation are recorded apart; the payload (every
    other file, the executables among them) must be unchanged.
    """
    if returncode not in (0, 1) or stderr.strip() or (returncode == 1 and not text.strip()):
        detail = stderr.strip().splitlines()[0] if stderr.strip() else f'exit {returncode} without output'
        return not_observed('dpkg -V failed: ' + detail[:200])
    entries = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        path = parts[-1]
        conffile = len(parts) >= 3 and parts[1] == 'c'
        kind = 'conffile' if conffile else ('documentation' if path.startswith(DOCUMENTATION) else 'payload')
        entries.append({'flags': parts[0], 'path': path, 'kind': kind})
    return observed(clean=not entries, payload_verified=not any(entry['kind'] == 'payload' for entry in entries),
                    conffiles_changed=[entry['path'] for entry in entries if entry['kind'] == 'conffile'][:50],
                    changed_files=entries[:50])


def dpkg_observation(real: Path) -> dict:
    try:
        package = dpkg_owner(real)
    except RuntimeError as error:
        return not_observed(str(error))
    if package is None:
        return observed(kind='unpackaged')
    query = dpkg_query(package)
    if query is None:
        return not_observed('dpkg-query failed for ' + package)
    verify = command(['dpkg', '-V', package], timeout=300)
    verification = (not_observed('dpkg -V unavailable') if verify is None
                    else verification_observation(verify.stdout, verify.returncode, verify.stderr))
    return observed(kind='dpkg', **query, file_verification=verification)


# ------------------------------------------------------------------ apt origins, signatures, signed index

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


def list_prefix(uri: str, suite: str) -> tuple[str, str, str]:
    """apt's list-file prefix for an origin: scheme dropped, '/' mangled to '_'; plus the dist and component."""
    stripped = re.sub(r'^[a-z][a-z0-9+.-]*://', '', uri).rstrip('/')
    dist, _, component = suite.partition('/')
    return stripped.replace('/', '_'), dist, component or 'main'


def inrelease_path(uri: str, suite: str, lists: Path | None = None) -> Path:
    prefix, dist, _ = list_prefix(uri, suite)
    return (APT_LISTS if lists is None else lists) / (prefix + '_dists_' + dist + '_InRelease')


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


def read_index(path: Path) -> bytes:
    data = path.read_bytes()
    return gzip.decompress(data) if data[:2] == b'\x1f\x8b' else data


def inrelease_digests(text: str) -> dict[str, str]:
    """`SHA256:` block of an InRelease file: relative index path -> digest."""
    digests: dict[str, str] = {}
    active = False
    for line in text.splitlines():
        if re.match(r'^[A-Za-z0-9-]+:', line):
            active = line.startswith('SHA256:')
            continue
        if active and line.startswith(' '):
            parts = line.split()
            if len(parts) == 3 and HEX64.fullmatch(parts[0]):
                digests[parts[2]] = parts[0]
    return digests


def index_stanza(text: str, package: str, version: str) -> dict | None:
    for stanza in text.split('\n\n'):
        fields: dict[str, str] = {}
        for line in stanza.splitlines():
            key, sep, value = line.partition(':')
            if sep and not line.startswith(' '):
                fields[key] = value.strip()
        if fields.get('Package') == package and fields.get('Version') == version:
            return fields
    return None


def index_observation(origin: dict, package: str, version: str, host_architecture: str, lists: Path | None = None) -> dict:
    """The signed index entry of the installed version: the Packages file digest must be listed in the InRelease.

    Binds the `.deb` digest and file name to the origin's signature: InRelease
    (signed) -> SHA256 of `<component>/binary-<arch>/Packages` -> the stanza's
    SHA256 of the package file. Arch:all packages live in binary-all or in the
    host architecture's index; both are searched.
    """
    lists = APT_LISTS if lists is None else lists
    prefix, dist, component = list_prefix(origin['uri'], origin['suite'])
    inrelease = lists / (prefix + '_dists_' + dist + '_InRelease')
    if not inrelease.is_file():
        return not_observed('InRelease list file missing: ' + inrelease.name)
    digests = inrelease_digests(inrelease.read_text(errors='replace'))
    if not digests:
        return not_observed('InRelease carries no SHA256 digest list: ' + inrelease.name)
    tried = []
    for arch in (host_architecture, 'all'):
        relative = f'{component}/binary-{arch}/Packages'
        listed = digests.get(relative)
        candidate = lists / (prefix + '_dists_' + dist + '_' + component + '_binary-' + arch + '_Packages')
        tried.append(candidate.name)
        if listed is None or not candidate.is_file():
            continue
        data = read_index(candidate)
        signed = hashlib.sha256(data).hexdigest() == listed
        stanza = index_stanza(data.decode('utf-8', 'replace'), package, version)
        if stanza is None:
            continue
        if not signed:
            return not_observed('Packages index digest differs from the InRelease digest: ' + candidate.name)
        record = observed(list_file=candidate.name, index_signed=True, index_relative_path=relative, stanza_architecture=stanza.get('Architecture'),
                          deb_sha256=stanza.get('SHA256'), filename=stanza.get('Filename'), size=stanza.get('Size'))
        if not (isinstance(record['deb_sha256'], str) and HEX64.fullmatch(record['deb_sha256'])):
            return not_observed('index entry carries no SHA256 for ' + package + ' ' + version)
        return record
    return not_observed(f'no signed index entry for {package} {version} in ' + ', '.join(tried))


def cached_deb_observation(index: dict, cache: Path | None = None) -> dict:
    cache = APT_CACHE if cache is None else cache
    filename = index.get('filename')
    if not filename:
        return not_observed('no file name in the index entry')
    candidate = cache / Path(filename).name
    if not candidate.is_file():
        return observed(present=False)
    return observed(present=True, matches_index=sha256_of(candidate) == index.get('deb_sha256'))


# ------------------------------------------------------------------ collection

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
    record: dict = {'schema_version': 2, 'tool': 'tools/collect_package_identities.py', 'label': label,
                    'collected_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'distribution': distribution(),
                    'components': {}}
    host_architecture = record['distribution'].get('architecture') or 'amd64'
    for name, spec in COMPONENTS.items():
        path = component_path(name, spec)
        item: dict = {'executable': executable_observation(path, spec)}
        if spec.get('members'):
            item['members'] = {member: member_observation(path.parent / member) for member in spec['members']}
        if item['executable']['status'] == 'OBSERVED':
            item['dpkg'] = dpkg_observation(Path(item['executable']['real_path']))
            if item['dpkg']['status'] == 'OBSERVED' and item['dpkg'].get('kind') == 'dpkg':
                item['apt'] = apt_observation(item['dpkg']['package'])
                if item['apt']['status'] == 'OBSERVED':
                    origins = [origin for origin in item['apt']['origins'] if not origin['uri'].startswith('/')]
                    item['signatures'] = [dict(origin, signature=signature_observation(origin['uri'], origin['suite'], scratch)) for origin in origins]
                    item['index'] = not_observed('no repository origin for the installed version')
                    for origin in origins:
                        index = index_observation(origin, item['dpkg']['package'], item['dpkg']['version'], host_architecture)
                        item['index'] = dict(index, origin_uri=origin['uri'], origin_suite=origin['suite'])
                        if index['status'] == 'OBSERVED':
                            break
                    if item['index']['status'] == 'OBSERVED':
                        item['cached_deb'] = cached_deb_observation(item['index'])
        record['components'][name] = item
    record['missing_observations'] = missing_observations(record)
    return record


# ------------------------------------------------------------------ validation

def missing_observations(record: dict) -> list[str]:
    """Every required observation a record lacks, derived from the record itself, never from its own claim."""
    missing: list[str] = []
    components = record.get('components') if isinstance(record.get('components'), dict) else {}
    for name, spec in COMPONENTS.items():
        item = components.get(name)
        if not isinstance(item, dict):
            missing.append(name + ': not collected')
            continue
        executable = item.get('executable') if isinstance(item.get('executable'), dict) else None
        if executable is None or executable.get('status') != 'OBSERVED':
            missing.append(f'{name}.executable: ' + (executable.get('reason', 'not observed') if executable else 'absent'))
            continue
        if not (isinstance(executable.get('sha256'), str) and HEX64.fullmatch(executable['sha256'])):
            missing.append(name + '.executable: digest not observed')
        if ('version' in spec or spec.get('member') == 'include/defines.inc.php') and not executable.get('version_line'):
            missing.append(name + ': version not observed')
        for member in spec.get('members', ()):
            observation = (item.get('members') or {}).get(member) if isinstance(item.get('members'), dict) else None
            if not isinstance(observation, dict) or observation.get('status') != 'OBSERVED':
                missing.append(f'{name}.members.{member}: ' + (observation.get('reason', 'not observed') if isinstance(observation, dict) else 'absent'))
        dpkg = item.get('dpkg') if isinstance(item.get('dpkg'), dict) else None
        if dpkg is None or dpkg.get('status') != 'OBSERVED':
            missing.append(f'{name}.dpkg: ' + (dpkg.get('reason', 'not observed') if dpkg else 'absent'))
            continue
        if dpkg.get('kind') != 'dpkg':
            continue  # a source build is a deviation, not a missing observation
        verification = dpkg.get('file_verification') if isinstance(dpkg.get('file_verification'), dict) else None
        if verification is None or verification.get('status') != 'OBSERVED':
            missing.append(f'{name}.dpkg.file_verification: ' + (verification.get('reason', 'not observed') if verification else 'absent'))
        apt = item.get('apt') if isinstance(item.get('apt'), dict) else None
        if apt is None or apt.get('status') != 'OBSERVED':
            missing.append(f'{name}.apt: ' + (apt.get('reason', 'not observed') if apt else 'absent'))
            continue
        origins = [origin for origin in apt.get('origins', []) if isinstance(origin, dict) and not str(origin.get('uri', '/')).startswith('/')]
        signatures = item.get('signatures') if isinstance(item.get('signatures'), list) else []
        by_uri = {entry.get('uri'): entry for entry in signatures if isinstance(entry, dict)}
        if not origins:
            missing.append(name + '.apt: no repository origin for the installed version')
        for origin in origins:
            entry = by_uri.get(origin.get('uri'))
            signature = entry.get('signature') if isinstance(entry, dict) and isinstance(entry.get('signature'), dict) else None
            if signature is None or signature.get('status') != 'OBSERVED':
                missing.append(f'{name}.signature[{origin.get("uri")}]: ' + (signature.get('reason', 'not observed') if signature else 'absent'))
        index = item.get('index') if isinstance(item.get('index'), dict) else None
        if index is None or index.get('status') != 'OBSERVED':
            missing.append(f'{name}.index: ' + (index.get('reason', 'not observed') if index else 'absent'))
    return missing


def version_tuple(line: str | None) -> tuple[int, ...] | None:
    match = re.search(r'(\d+)\.(\d+)(?:\.(\d+))?', line or '')
    return tuple(int(part) for part in match.groups() if part is not None) if match else None


def origin_host(uri: str) -> str:
    return re.sub(r'^[a-z][a-z0-9+.-]*://', '', uri).split('/', 1)[0].lower()


def allowed_fingerprints(host: str, reference: dict) -> list[str]:
    """The full key fingerprints that may sign an origin's InRelease, by repository."""
    if host == 'repo.zabbix.com':
        return [reference['ubuntu_packages']['expected_signing_key']['fingerprint']]
    if host == 'apt.postgresql.org':
        return [reference['dependencies']['postgresql']['pgdg_key_fingerprint']]
    if host.endswith('ubuntu.com'):
        return list(reference['dependencies']['php']['ubuntu_archive_key_fingerprints'])
    return []


def evaluate(record: dict, reference: dict, identities: dict) -> tuple[str, list[str], list[str]]:
    """CONFORMS / DEVIATES / INCOMPLETE with the named deviations and the independently derived missing observations."""
    deviations: list[str] = []
    incomplete = missing_observations(record)
    selected = reference['selected']['version']
    policy = identities['policy']
    distro = record.get('distribution', {}) if isinstance(record.get('distribution'), dict) else {}
    if (distro.get('id'), distro.get('version_id'), distro.get('architecture')) != ('ubuntu', '24.04', 'amd64'):
        deviations.append('distribution: expected Ubuntu 24.04 amd64, observed ' + json.dumps(distro, sort_keys=True))
    components = record.get('components', {}) if isinstance(record.get('components'), dict) else {}
    versions: dict[str, str | None] = {}
    for name, spec in COMPONENTS.items():
        item = components.get(name)
        if not isinstance(item, dict):
            continue
        executable = item.get('executable') if isinstance(item.get('executable'), dict) else {}
        if executable.get('status') != 'OBSERVED':
            continue
        versions[name] = executable.get('version_line')
        dpkg = item.get('dpkg') if isinstance(item.get('dpkg'), dict) else {}
        if dpkg.get('status') != 'OBSERVED':
            continue
        if dpkg.get('kind') != 'dpkg':
            deviations.append(name + ': unpackaged executable (source build); packages are required for an approved identity')
            continue
        package = dpkg.get('package')
        if package not in EXPECTED_PACKAGES[name]:
            deviations.append(f'{name}: package {package}, expected ' + ' or '.join(EXPECTED_PACKAGES[name]))
        expected_arch = 'all' if package in ARCH_ALL_PACKAGES else 'amd64'
        if dpkg.get('architecture') != expected_arch:
            deviations.append(f'{name}: architecture {dpkg.get("architecture")}, expected {expected_arch}')
        if str(dpkg.get('package_status', '')).split() not in (['install', 'ok', 'installed'], ['hold', 'ok', 'installed']):
            deviations.append(f'{name}: dpkg status "{dpkg.get("package_status")}" is not installed')
        verification = dpkg.get('file_verification') if isinstance(dpkg.get('file_verification'), dict) else {}
        if verification.get('status') == 'OBSERVED' and not verification.get('payload_verified'):
            changed = [entry['path'] for entry in verification.get('changed_files', []) if entry.get('kind') == 'payload']
            deviations.append(name + ': dpkg -V reports changed or missing package payload files: ' + ', '.join(changed[:5]))
        for member, observation in (item.get('members') or {}).items() if isinstance(item.get('members'), dict) else []:
            if isinstance(observation, dict) and observation.get('status') == 'OBSERVED':
                if observation.get('kind') != 'dpkg':
                    deviations.append(f'{name}.members.{member}: unpackaged executable')
                elif observation.get('package') not in POSTGRES_MEMBER_PACKAGES or observation.get('version') != dpkg.get('version'):
                    deviations.append(f'{name}.members.{member}: package {observation.get("package")} {observation.get("version")} '
                                      f'does not belong to the installed PostgreSQL {dpkg.get("version")}')
        apt = item.get('apt') if isinstance(item.get('apt'), dict) else {}
        if apt.get('status') != 'OBSERVED':
            continue
        if apt.get('installed') != dpkg.get('version'):
            deviations.append(f'{name}: apt installed {apt.get("installed")} differs from dpkg {dpkg.get("version")}')
        origins = [origin for origin in apt.get('origins', []) if isinstance(origin, dict) and not str(origin.get('uri', '/')).startswith('/')]
        signatures = {entry.get('uri'): entry for entry in item.get('signatures', []) if isinstance(entry, dict)} if isinstance(item.get('signatures'), list) else {}
        for origin in origins:
            host = origin_host(str(origin.get('uri')))
            allowed = allowed_fingerprints(host, reference)
            if host not in spec['origins']:
                deviations.append(f'{name}: origin {host} is not one of {list(spec["origins"])}')
                continue
            signature = (signatures.get(origin.get('uri')) or {}).get('signature')
            if not isinstance(signature, dict) or signature.get('status') != 'OBSERVED':
                continue  # listed as missing
            fingerprints = [key.get('fingerprint') for key in signature.get('keys', []) if isinstance(key, dict)] if signature.get('verified') else []
            if not any(fingerprint in allowed for fingerprint in fingerprints):
                deviations.append(f'{name}: origin {host} InRelease not verified by the pinned key '
                                  f'(observed {fingerprints or "no valid signature"}, expected {allowed})')
        index = item.get('index') if isinstance(item.get('index'), dict) else {}
        if index.get('status') == 'OBSERVED':
            if not index.get('index_signed'):
                deviations.append(f'{name}: Packages index not bound to the InRelease digest')
            if origin_host(str(index.get('origin_uri', ''))) not in spec['origins']:
                deviations.append(f'{name}: signed index entry comes from {index.get("origin_uri")}, not an allowed origin')
            if index.get('stanza_architecture') not in (expected_arch, None) or (index.get('stanza_architecture') is None):
                deviations.append(f'{name}: signed index architecture {index.get("stanza_architecture")}, expected {expected_arch}')
        cached = item.get('cached_deb') if isinstance(item.get('cached_deb'), dict) else {}
        if cached.get('status') == 'OBSERVED' and cached.get('present') and cached.get('matches_index') is False:
            deviations.append(f'{name}: cached .deb digest differs from the signed index entry')
    # Version policy.
    postgres = version_tuple(versions.get('postgres'))
    if postgres and postgres[0] != policy['postgres']['major']:
        deviations.append(f'postgres: major {postgres[0]}, policy requires {policy["postgres"]["major"]}')
    pool = version_tuple(versions.get('pgbouncer'))
    if pool and (pool[0] != policy['pgbouncer']['major'] or pool[1] < policy['pgbouncer']['minimum_minor']):
        deviations.append(f'pgbouncer: {".".join(map(str, pool))}, policy requires {policy["pgbouncer"]["major"]}.{policy["pgbouncer"]["minimum_minor"]} or newer')
    server = versions.get('zabbix_server')
    if server and not re.search(r'\(Zabbix\) ' + re.escape(selected) + r'(?![0-9.])', server):
        deviations.append(f'zabbix_server: "{server}" is not the pinned {selected}')
    frontend = versions.get('zabbix_frontend')
    if frontend and frontend != selected:
        deviations.append(f'zabbix_frontend: ZABBIX_VERSION {frontend} is not the pinned {selected}')
    php = version_tuple(versions.get('php'))
    minimum = version_tuple(reference['dependencies']['php']['minimum'])
    if php and minimum and php < minimum:
        deviations.append(f'php: {".".join(map(str, php))} is below the frontend minimum {reference["dependencies"]["php"]["minimum"]}')
    zabbix_versions = {name: (components.get(name, {}).get('dpkg') or {}).get('version') if isinstance(components.get(name), dict) else None
                       for name in ZABBIX_PACKAGES}
    pattern = re.compile(r'^1:' + re.escape(selected) + r'-\d+\+ubuntu24\.04$')
    for name, version in zabbix_versions.items():
        if version and not pattern.match(version):
            deviations.append(f'{name}: dpkg version {version} does not match 1:{selected}-<revision>+ubuntu24.04')
    distinct = {version for version in zabbix_versions.values() if version}
    if len(distinct) > 1:
        deviations.append('zabbix packages carry different versions: ' + ', '.join(sorted(distinct)))
    if deviations:
        return 'DEVIATES', deviations, incomplete
    return ('INCOMPLETE' if incomplete else 'CONFORMS'), deviations, incomplete


# ------------------------------------------------------------------ approval proposal

def proposal(record: dict, record_digest: str, reviewer: str, amendment: str) -> dict:
    """Reviewed-identity entries, taken verbatim from the record's observations, with their provenance chain."""
    def entry(name: str) -> dict:
        item = record['components'][name]
        dpkg, index, executable = item['dpkg'], item['index'], item['executable']
        signature = next((e['signature'] for e in item.get('signatures', []) if e.get('uri') == index.get('origin_uri')), {})
        return {'kind': 'dpkg', 'package': dpkg['package'], 'version': dpkg['version'], 'architecture': dpkg['architecture'],
                'deb_sha256': index['deb_sha256'], 'deb_filename': index.get('filename'),
                'origin': {'uri': index.get('origin_uri'), 'suite': index.get('origin_suite'), 'index': index.get('index_relative_path'),
                           'inrelease': signature.get('inrelease'), 'signed_by': [key.get('fingerprint') for key in signature.get('keys', [])]},
                'executable_sha256': executable['sha256'],
                **({'members': {member: {'sha256': obs['sha256'], 'package': obs['package'], 'version': obs['version']}
                                for member, obs in item['members'].items()}} if item.get('members') else {})}
    return {
        'schema_version': 2, 'proposal': 'reviewed identities for lab/native_service_identities.json and lab/zabbix_reference.json',
        'source_record': {'label': record['label'], 'collected_at': record['collected_at'], 'sha256': record_digest},
        'reviewer': reviewer, 'amendment': amendment, 'prepared_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'native_service_identities.reviewed': {'postgres': [entry('postgres')], 'pgbouncer': [entry('pgbouncer')]},
        'zabbix_reference.ubuntu_packages.verified': {**{ZABBIX_PACKAGES[name]: entry(name) for name in ZABBIX_PACKAGES}, 'php': entry('php')},
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
