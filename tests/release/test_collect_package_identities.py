"""tools/collect_package_identities.py: observations, validation and the approval proposal (R29, corrected).

Host commands are mocked; the synthetic records name a conforming Ubuntu 24.04
amd64 host as the signed indexes describe it (Zabbix frontend and SQL scripts
are Architecture: all) and every deviation and missing observation the
validator must name on its own. Nothing is approved from an assumed, synthetic
or source-built identity.
"""
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import collect_package_identities as cpi  # noqa: E402

REFERENCE = json.loads((ROOT / 'lab/zabbix_reference.json').read_text())
IDENTITIES = json.loads((ROOT / 'lab/native_service_identities.json').read_text())
PIN = REFERENCE['selected']['version']
ZABBIX_FPR = REFERENCE['ubuntu_packages']['expected_signing_key']['fingerprint']
PGDG = REFERENCE['dependencies']['postgresql']['pgdg_key_fingerprint']
UBUNTU = REFERENCE['dependencies']['php']['ubuntu_archive_key_fingerprints'][0]
ZVER = '1:' + PIN + '-1+ubuntu24.04'
POLICY = '''zabbix-server-pgsql:
  Installed: 1:7.0.31-1+ubuntu24.04
  Candidate: 1:7.0.31-1+ubuntu24.04
  Version table:
 *** 1:7.0.31-1+ubuntu24.04 500
        500 https://repo.zabbix.com/zabbix/7.0/ubuntu noble/main amd64 Packages
        100 /var/lib/dpkg/status
     1:7.0.30-1+ubuntu24.04 500
        500 https://repo.zabbix.com/zabbix/7.0/ubuntu noble/main amd64 Packages
'''


def signature(host, fingerprint, verified=True):
    return {'uri': 'https://' + host + '/x', 'suite': 'noble/main', 'architecture': 'amd64', 'priority': 500,
            'signature': {'status': 'OBSERVED', 'inrelease': host + '_x_dists_noble_InRelease', 'verified': verified,
                          'keys': [{'long_id': fingerprint[-16:], 'fingerprint': fingerprint, 'user_id': 'synthetic', 'keyring': 'k.gpg'}] if verified else [],
                          'keyrings_tried': 1}}


def component(version_line, package, version, host, fingerprint, *, kind='dpkg', architecture='amd64', members=None):
    item = {'executable': {'status': 'OBSERVED', 'path': '/x', 'real_path': '/x', 'sha256': 'a' * 64, 'version_line': version_line}}
    if members:
        item['members'] = {name: {'status': 'OBSERVED', 'path': '/x/' + name, 'real_path': '/x/' + name, 'sha256': 'b' * 64, 'kind': 'dpkg',
                                  'package': pkg, 'version': version, 'architecture': 'amd64'} for name, pkg in members.items()}
    if kind == 'unpackaged':
        item['dpkg'] = {'status': 'OBSERVED', 'kind': 'unpackaged'}
        return item
    item['dpkg'] = {'status': 'OBSERVED', 'kind': 'dpkg', 'package': package, 'version': version, 'architecture': architecture,
                    'package_status': 'install ok installed',
                    'file_verification': {'status': 'OBSERVED', 'clean': True, 'payload_verified': True, 'conffiles_changed': [], 'changed_files': []}}
    item['apt'] = {'status': 'OBSERVED', 'installed': version, 'origins': [{'priority': 500, 'uri': 'https://' + host + '/x', 'suite': 'noble/main', 'architecture': 'amd64'}]}
    item['signatures'] = [signature(host, fingerprint)]
    item['index'] = {'status': 'OBSERVED', 'origin_uri': 'https://' + host + '/x', 'origin_suite': 'noble/main', 'list_file': host + '_x_dists_noble_main_binary-' + architecture + '_Packages',
                     'index_signed': True, 'index_relative_path': 'main/binary-' + architecture + '/Packages', 'stanza_architecture': architecture,
                     'deb_sha256': 'c' * 64, 'filename': 'pool/main/x/' + package + '_' + version + '_' + architecture + '.deb', 'size': '1'}
    item['cached_deb'] = {'status': 'OBSERVED', 'present': False}
    return item


def conforming_record():
    return {
        'schema_version': 2, 'label': 'synthetic-host', 'collected_at': '2026-09-23T00:00:00+00:00',
        'distribution': {'id': 'ubuntu', 'version_id': '24.04', 'architecture': 'amd64'},
        'components': {
            'postgres': component('postgres (PostgreSQL) 18.6 (Ubuntu 18.6-1.pgdg24.04+2)', 'postgresql-18', '18.6-1.pgdg24.04+2', 'apt.postgresql.org', PGDG,
                                  members={'pg_ctl': 'postgresql-18', 'initdb': 'postgresql-18', 'psql': 'postgresql-client-18'}),
            'pgbouncer': component('PgBouncer 1.25.2', 'pgbouncer', '1.25.2-1.pgdg24.04+1', 'apt.postgresql.org', PGDG),
            'zabbix_server': component('zabbix_server (Zabbix) ' + PIN, 'zabbix-server-pgsql', ZVER, 'repo.zabbix.com', ZABBIX_FPR),
            'zabbix_frontend': component(PIN, 'zabbix-frontend-php', ZVER, 'repo.zabbix.com', ZABBIX_FPR, architecture='all'),
            'zabbix_sql_scripts': component(None, 'zabbix-sql-scripts', ZVER, 'repo.zabbix.com', ZABBIX_FPR, architecture='all'),
            'php': component('PHP 8.3.6 (cli) (built: Sep  2 2026 12:56:02) (NTS)', 'php8.3-cli', '8.3.6-0ubuntu0.24.04.11', 'archive.ubuntu.com', UBUNTU),
        },
        'missing_observations': [],
    }


def evaluate(record):
    return cpi.evaluate(record, REFERENCE, IDENTITIES)


def test_a_complete_conforming_record_conforms():
    assert evaluate(conforming_record()) == ('CONFORMS', [], [])


@pytest.mark.parametrize('mutate,fragment', [
    (lambda r: r['components']['zabbix_server']['executable'].update(version_line='zabbix_server (Zabbix) 7.0.30'), 'not the pinned'),
    (lambda r: r['components']['zabbix_server']['executable'].update(version_line='zabbix_server (Zabbix) 7.0.310'), 'not the pinned'),
    (lambda r: r['components']['zabbix_frontend']['executable'].update(version_line='7.0.30'), 'ZABBIX_VERSION 7.0.30'),
    (lambda r: r['components']['zabbix_sql_scripts']['dpkg'].update(version='1:7.0.30-1+ubuntu24.04'), 'different versions'),
    (lambda r: r['components']['zabbix_server']['dpkg'].update(version='1:7.0.31-1+ubuntu22.04'), 'does not match 1:' + PIN),
    (lambda r: r['components']['postgres']['executable'].update(version_line='postgres (PostgreSQL) 16.4'), 'major 16'),
    (lambda r: r['components']['pgbouncer']['executable'].update(version_line='PgBouncer 1.23.1'), 'pgbouncer: 1.23.1'),
    (lambda r: r['components']['php']['executable'].update(version_line='PHP 7.4.33 (cli)'), 'below the frontend minimum'),
    (lambda r: r['components'].__setitem__('postgres', component('postgres (PostgreSQL) 18.6', None, None, None, None, kind='unpackaged')), 'unpackaged executable'),
    (lambda r: r['components']['zabbix_server']['apt']['origins'].__setitem__(0, {'priority': 500, 'uri': 'https://mirror.example/zabbix', 'suite': 'noble/main', 'architecture': 'amd64'}), 'origin mirror.example is not one of'),
    (lambda r: r['components']['zabbix_server']['signatures'].__setitem__(0, signature('repo.zabbix.com', '0' * 40)), 'not verified by the pinned key'),
    (lambda r: r['components']['zabbix_server']['signatures'][0]['signature']['keys'][0].update(fingerprint='F' * 24 + ZABBIX_FPR[-16:]), 'not verified by the pinned key'),
    (lambda r: r['components']['postgres']['signatures'].__setitem__(0, signature('apt.postgresql.org', '0' * 40)), 'not verified by the pinned key'),
    (lambda r: r['components']['php']['signatures'].__setitem__(0, signature('archive.ubuntu.com', '1' * 40)), 'not verified by the pinned key'),
    (lambda r: r['components']['php']['signatures'].__setitem__(0, signature('archive.ubuntu.com', UBUNTU, verified=False)), 'no valid signature'),
    (lambda r: r['components']['php']['dpkg']['file_verification'].update(payload_verified=False, changed_files=[{'flags': '??5??????', 'path': '/usr/bin/php8.3', 'kind': 'payload'}]), 'changed or missing package payload files: /usr/bin/php8.3'),
    (lambda r: r['components']['php']['dpkg'].update(package_status='deinstall ok config-files'), 'is not installed'),
    (lambda r: r['components']['php']['dpkg'].update(architecture='arm64'), 'expected amd64'),
    (lambda r: r['components']['zabbix_server']['dpkg'].update(architecture='all'), 'zabbix_server: architecture all, expected amd64'),
    (lambda r: r['components']['zabbix_frontend']['dpkg'].update(architecture='amd64'), 'zabbix_frontend: architecture amd64, expected all'),
    (lambda r: r['components']['zabbix_server']['apt'].update(installed='1:7.0.30-1+ubuntu24.04'), 'differs from dpkg'),
    (lambda r: r['distribution'].update(version_id='22.04'), 'expected Ubuntu 24.04 amd64'),
    (lambda r: r['components']['zabbix_frontend']['dpkg'].update(package='zabbix-frontend-nginx'), 'expected zabbix-frontend-php'),
    (lambda r: r['components']['zabbix_server']['index'].update(index_signed=False), 'not bound to the InRelease digest'),
    (lambda r: r['components']['zabbix_server']['index'].update(origin_uri='https://mirror.example/x'), 'signed index entry comes from'),
    (lambda r: r['components']['zabbix_server']['index'].update(stanza_architecture='all'), 'signed index architecture all'),
    (lambda r: r['components']['zabbix_server'].__setitem__('cached_deb', {'status': 'OBSERVED', 'present': True, 'matches_index': False}), 'cached .deb digest differs'),
    (lambda r: r['components']['postgres']['members']['psql'].update(package='postgresql-client-16', version='16.4-1'), 'does not belong to the installed PostgreSQL'),
    (lambda r: r['components']['postgres']['members']['pg_ctl'].update(kind='unpackaged', package=None), 'members.pg_ctl: unpackaged'),
], ids=['server-version', 'server-version-prefix', 'frontend-version', 'mixed-zabbix-versions', 'debian-revision-suite', 'postgres-major',
        'pgbouncer-minor', 'php-minimum', 'source-build', 'foreign-origin', 'zabbix-wrong-key', 'zabbix-long-id-only', 'pgdg-wrong-key',
        'ubuntu-wrong-key', 'ubuntu-unverified', 'changed-executable', 'not-installed', 'architecture', 'server-arch-all', 'frontend-arch-amd64',
        'apt-dpkg-disagree', 'distribution', 'package-name', 'index-unsigned', 'index-foreign-origin', 'index-arch', 'cached-deb-differs',
        'member-other-package', 'member-unpackaged'])
def test_every_deviation_is_named(mutate, fragment):
    record = conforming_record()
    mutate(record)
    result, deviations, incomplete = evaluate(record)
    assert result == 'DEVIATES', (deviations, incomplete)
    assert any(fragment in item for item in deviations), deviations


def test_arch_all_is_accepted_only_for_the_packages_the_signed_index_publishes_as_such():
    """R7: the genuine host installs zabbix-frontend-php and zabbix-sql-scripts as Architecture: all."""
    record = conforming_record()
    assert evaluate(record) == ('CONFORMS', [], [])
    assert record['components']['zabbix_frontend']['dpkg']['architecture'] == 'all'
    assert record['components']['zabbix_sql_scripts']['dpkg']['architecture'] == 'all'
    for name in ('postgres', 'pgbouncer', 'zabbix_server', 'php'):
        broken = conforming_record()
        broken['components'][name]['dpkg']['architecture'] = 'all'
        assert evaluate(broken)[0] == 'DEVIATES'


def test_conffile_and_documentation_differences_are_recorded_but_only_payload_changes_deviate():
    """R7/R18: a conffile the runner account edited or cannot read is not a changed executable."""
    text = '??5?????? c /etc/zabbix/zabbix_server.conf\nmissing   /usr/share/man/man8/zabbix_server.8.gz\n'
    record = cpi.verification_observation(text, returncode=1)
    assert record['status'] == 'OBSERVED' and record['clean'] is False and record['payload_verified'] is True
    assert record['conffiles_changed'] == ['/etc/zabbix/zabbix_server.conf']
    assert [entry['kind'] for entry in record['changed_files']] == ['conffile', 'documentation']
    payload = cpi.verification_observation('??5?????? /usr/sbin/zabbix_server\n', returncode=1)
    assert payload['payload_verified'] is False and payload['changed_files'][0]['kind'] == 'payload'
    assert cpi.verification_observation('', returncode=0) == {'status': 'OBSERVED', 'clean': True, 'payload_verified': True, 'conffiles_changed': [], 'changed_files': []}
    full = conforming_record()
    full['components']['zabbix_server']['dpkg']['file_verification'] = record
    assert evaluate(full) == ('CONFORMS', [], [])


@pytest.mark.parametrize('returncode,stdout,stderr', [(2, '', 'dpkg: error: cannot access archive / database locked\n'),
                                                       (1, '', ''), (0, '', 'dpkg-query: warning: something\n'), (1, '??5?????? /usr/bin/x\n', 'error\n')],
                         ids=['exit-2', 'exit-1-silent', 'stderr-with-exit-0', 'stderr-with-lines'])
def test_a_failed_dpkg_verification_is_never_clean(returncode, stdout, stderr):
    """R18: the return code and stderr of dpkg -V decide; a failure is a missing observation."""
    record = cpi.verification_observation(stdout, returncode, stderr)
    assert record['status'] == 'NOT_OBSERVED' and record['reason'].startswith('dpkg -V failed')
    full = conforming_record()
    full['components']['php']['dpkg']['file_verification'] = record
    result, deviations, incomplete = evaluate(full)
    assert result == 'INCOMPLETE' and any('php.dpkg.file_verification' in item for item in incomplete)


@pytest.mark.parametrize('mutate,fragment', [
    (lambda r: r['components'].pop('pgbouncer'), 'pgbouncer: not collected'),
    (lambda r: [r['components'].__setitem__(n, {}) for n in list(r['components'])], 'postgres.executable'),
    (lambda r: r['components']['postgres'].__setitem__('executable', {'status': 'NOT_OBSERVED', 'reason': 'missing: /usr/lib/postgresql/18/bin/postgres'}), 'postgres.executable: missing'),
    (lambda r: r['components']['zabbix_server']['executable'].update(version_line=None), 'zabbix_server: version not observed'),
    (lambda r: r['components']['zabbix_server']['executable'].update(sha256=None), 'digest not observed'),
    (lambda r: r['components']['zabbix_server'].__setitem__('dpkg', {'status': 'NOT_OBSERVED', 'reason': 'dpkg unavailable'}), 'zabbix_server.dpkg: dpkg unavailable'),
    (lambda r: r['components']['zabbix_server'].__setitem__('apt', {'status': 'NOT_OBSERVED', 'reason': 'apt-cache policy unavailable'}), 'zabbix_server.apt'),
    (lambda r: r['components']['zabbix_server'].pop('apt'), 'zabbix_server.apt: absent'),
    (lambda r: r['components']['php']['dpkg'].update(file_verification={'status': 'NOT_OBSERVED', 'reason': 'dpkg -V unavailable'}), 'php.dpkg.file_verification'),
    (lambda r: r['components']['php'].__setitem__('signatures', []), 'php.signature[https://archive.ubuntu.com/x]: absent'),
    (lambda r: r['components']['php']['signatures'][0].__setitem__('signature', {'status': 'NOT_OBSERVED', 'reason': 'gpgv unavailable'}), 'gpgv unavailable'),
    (lambda r: r['components']['zabbix_server'].pop('index'), 'zabbix_server.index: absent'),
    (lambda r: r['components']['zabbix_server'].__setitem__('index', {'status': 'NOT_OBSERVED', 'reason': 'no signed index entry'}), 'no signed index entry'),
    (lambda r: r['components']['postgres']['members'].pop('psql'), 'postgres.members.psql: absent'),
    (lambda r: r['components']['postgres']['members'].__setitem__('initdb', {'status': 'NOT_OBSERVED', 'reason': 'missing: /x/initdb'}), 'postgres.members.initdb: missing'),
    (lambda r: r['components']['zabbix_sql_scripts'].__setitem__('executable', {'status': 'NOT_OBSERVED', 'reason': 'directory paths are not executable identities: /src/database/postgresql'}), 'directory paths are not executable identities'),
    (lambda r: r['components']['zabbix_server']['apt'].update(origins=[{'priority': 100, 'uri': '/var/lib/dpkg/status', 'suite': 'now', 'architecture': 'amd64'}]), 'no repository origin'),
], ids=['component-missing', 'all-components-empty', 'executable-missing', 'version-missing', 'digest-missing', 'dpkg-not-observed',
        'apt-not-observed', 'apt-absent', 'dpkg-verify-missing', 'no-signature-entries', 'signature-not-observed', 'index-absent',
        'index-not-observed', 'member-absent', 'member-missing', 'directory-identity', 'local-install-only'])
def test_missing_observations_are_derived_by_the_validator_never_taken_from_the_record(mutate, fragment):
    """R4: a partial or edited record cannot validate by claiming an empty missing_observations list."""
    record = conforming_record()
    mutate(record)
    record['missing_observations'] = []  # the record's own claim is ignored
    result, deviations, incomplete = evaluate(record)
    assert result == 'INCOMPLETE', (deviations, incomplete)
    assert any(fragment in item for item in incomplete), incomplete
    assert cpi.missing_observations(record) == incomplete


def test_every_origin_of_the_installed_version_must_be_allowed_and_verified_by_the_pinned_key():
    """R5: an unverified repo.zabbix.com plus a mirror signed by any key never conforms."""
    record = conforming_record()
    server = record['components']['zabbix_server']
    server['apt']['origins'].append({'priority': 500, 'uri': 'https://mirror.example/x', 'suite': 'noble/main', 'architecture': 'amd64'})
    server['signatures'] = [signature('repo.zabbix.com', ZABBIX_FPR, verified=False), signature('mirror.example', 'D' * 40)]
    result, deviations, _ = evaluate(record)
    assert result == 'DEVIATES'
    assert any('origin mirror.example is not one of' in item for item in deviations)
    assert any('origin repo.zabbix.com InRelease not verified by the pinned key' in item for item in deviations)
    # Two allowed origins, both verified: conforms. One of them unverified: deviates, whichever it is.
    record = conforming_record()
    php = record['components']['php']
    php['apt']['origins'].append({'priority': 500, 'uri': 'https://security.ubuntu.com/x', 'suite': 'noble-security/main', 'architecture': 'amd64'})
    php['signatures'].append(signature('security.ubuntu.com', UBUNTU))
    assert evaluate(record) == ('CONFORMS', [], [])
    php['signatures'][1] = signature('security.ubuntu.com', UBUNTU, verified=False)
    assert evaluate(record)[0] == 'DEVIATES'


def test_apt_policy_parsing_attributes_only_the_installed_version_origins():
    parsed = cpi.parse_policy(POLICY)
    assert parsed['installed'] == '1:7.0.31-1+ubuntu24.04'
    assert parsed['origins'] == [{'priority': 500, 'uri': 'https://repo.zabbix.com/zabbix/7.0/ubuntu', 'suite': 'noble/main', 'architecture': 'amd64'}]
    assert cpi.parse_policy(POLICY.replace('Installed: 1:7.0.31-1+ubuntu24.04', 'Installed: (none)'))['installed'] is None


def test_inrelease_list_file_name_follows_apt_mangling(tmp_path):
    path = cpi.inrelease_path('https://repo.zabbix.com/zabbix/7.0/ubuntu', 'noble/main', tmp_path)
    assert path == tmp_path / 'repo.zabbix.com_zabbix_7.0_ubuntu_dists_noble_InRelease'
    assert cpi.inrelease_path('http://apt.postgresql.org/pub/repos/apt/', 'noble-pgdg/main', tmp_path).name == 'apt.postgresql.org_pub_repos_apt_dists_noble-pgdg_InRelease'


def signed_lists(tmp_path, stanza_arch='all', tamper=False):
    """A synthetic apt list directory: InRelease digest list + Packages indexes for binary-amd64 and binary-all."""
    lists = tmp_path / 'lists'
    lists.mkdir(parents=True, exist_ok=True)
    prefix = 'repo.zabbix.com_zabbix_7.0_ubuntu_dists_noble_'
    amd64 = 'Package: zabbix-server-pgsql\nVersion: 1:7.0.31-1+ubuntu24.04\nArchitecture: amd64\nFilename: pool/main/z/zabbix/zabbix-server-pgsql_7.0.31-1+ubuntu24.04_amd64.deb\nSize: 10\nSHA256: ' + '1' * 64 + '\n\n'
    allidx = 'Package: zabbix-sql-scripts\nVersion: 1:7.0.31-1+ubuntu24.04\nArchitecture: ' + stanza_arch + '\nFilename: pool/main/z/zabbix/zabbix-sql-scripts_7.0.31-1+ubuntu24.04_all.deb\nSize: 10\nSHA256: ' + '2' * 64 + '\n\n'
    (lists / (prefix + 'main_binary-amd64_Packages')).write_text(amd64)
    (lists / (prefix + 'main_binary-all_Packages')).write_bytes(gzip.compress(allidx.encode()))
    digests = {'main/binary-amd64/Packages': hashlib.sha256(amd64.encode()).hexdigest(),
               'main/binary-all/Packages': hashlib.sha256(allidx.encode()).hexdigest() if not tamper else '0' * 64}
    (lists / (prefix + 'InRelease')).write_text('Origin: Zabbix\nSHA256:\n' + ''.join(f' {value} 10 {path}\n' for path, value in digests.items()) + 'Date: x\n')
    return lists


def test_the_signed_index_entry_binds_the_deb_digest_to_the_inrelease(tmp_path):
    """R19: the Packages index must hash to the InRelease entry; Arch:all packages are found in binary-all."""
    lists = signed_lists(tmp_path)
    origin = {'uri': 'https://repo.zabbix.com/zabbix/7.0/ubuntu', 'suite': 'noble/main'}
    server = cpi.index_observation(origin, 'zabbix-server-pgsql', '1:7.0.31-1+ubuntu24.04', 'amd64', lists)
    assert server['status'] == 'OBSERVED' and server['index_signed'] is True and server['deb_sha256'] == '1' * 64
    assert server['index_relative_path'] == 'main/binary-amd64/Packages' and server['stanza_architecture'] == 'amd64'
    scripts = cpi.index_observation(origin, 'zabbix-sql-scripts', '1:7.0.31-1+ubuntu24.04', 'amd64', lists)
    assert scripts['status'] == 'OBSERVED' and scripts['deb_sha256'] == '2' * 64 and scripts['stanza_architecture'] == 'all'
    assert cpi.index_observation(origin, 'zabbix-server-pgsql', '1:7.0.30-1+ubuntu24.04', 'amd64', lists)['status'] == 'NOT_OBSERVED'
    tampered = signed_lists(tmp_path / 'tampered', tamper=True)
    assert 'differs from the InRelease digest' in cpi.index_observation(origin, 'zabbix-sql-scripts', '1:7.0.31-1+ubuntu24.04', 'amd64', tampered)['reason']
    assert cpi.index_observation(origin, 'zabbix-server-pgsql', '1:7.0.31-1+ubuntu24.04', 'amd64', tmp_path / 'nowhere')['status'] == 'NOT_OBSERVED'
    cache = tmp_path / 'cache'
    cache.mkdir()
    (cache / 'zabbix-server-pgsql_7.0.31-1+ubuntu24.04_amd64.deb').write_bytes(b'deb')
    assert cpi.cached_deb_observation(server, cache) == {'status': 'OBSERVED', 'present': True, 'matches_index': False}
    assert cpi.cached_deb_observation(scripts, cache) == {'status': 'OBSERVED', 'present': False}


def test_a_directory_is_refused_as_an_executable_identity(tmp_path):
    """R19: a schema source tree carries no identity anyone can sign."""
    (tmp_path / 'schemadir').mkdir()
    record = cpi.executable_observation(tmp_path / 'schemadir', cpi.COMPONENTS['zabbix_sql_scripts'])
    assert record['status'] == 'NOT_OBSERVED' and 'directory paths are not executable identities' in record['reason']


def test_collect_records_dpkg_apt_signature_and_index_observations_for_a_packaged_component(tmp_path, monkeypatch):
    server = tmp_path / 'zabbix_server'
    server.write_bytes(b'#!/bin/sh\n')
    monkeypatch.setenv('ZABALACK_ZABBIX_SERVER', str(server))
    for key in ('ACK_LAB_PG_BIN', 'ACK_LAB_PGBOUNCER_BIN', 'ZABALACK_ZABBIX_UI', 'ZABALACK_ZABBIX_SCHEMA', 'ZABALACK_PHP'):
        monkeypatch.setenv(key, str(tmp_path / 'absent' / key))
    lists = signed_lists(tmp_path)
    monkeypatch.setattr(cpi, 'APT_LISTS', lists)
    monkeypatch.setattr(cpi, 'APT_CACHE', tmp_path / 'cache')
    keyring_dir = tmp_path / 'keyrings'
    keyring_dir.mkdir()
    (keyring_dir / 'zabbix.gpg').write_bytes(b'\x99binary')
    monkeypatch.setattr(cpi, 'KEYRING_DIRS', (keyring_dir,))
    monkeypatch.setattr(cpi, 'OS_RELEASE', tmp_path / 'os-release')
    (tmp_path / 'os-release').write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
    outputs = {
        ('dpkg', '-S', str(server)): (0, 'zabbix-server-pgsql: ' + str(server) + '\n', ''),
        ('dpkg-query', '-W', '-f', '${Package}\t${Version}\t${Architecture}\t${Status}', 'zabbix-server-pgsql'):
            (0, 'zabbix-server-pgsql\t1:7.0.31-1+ubuntu24.04\tamd64\tinstall ok installed', ''),
        ('dpkg', '-V', 'zabbix-server-pgsql'): (1, '??5?????? c /etc/zabbix/zabbix_server.conf\n', ''),
        ('apt-cache', 'policy', 'zabbix-server-pgsql'): (0, POLICY, ''),
        ('dpkg', '--print-architecture'): (0, 'amd64\n', ''),
        (str(server), '-V'): (0, 'zabbix_server (Zabbix) 7.0.31\nRevision fc62e96 22 September 2026\n', ''),
    }
    seen = []

    def command(argv, timeout=60):
        seen.append(tuple(argv))
        code, out, err = outputs[tuple(argv)]
        return subprocess.CompletedProcess(argv, code, out, err)
    monkeypatch.setattr(cpi, 'command', command)
    monkeypatch.setattr(cpi.shutil, 'which', lambda name: '/usr/bin/' + name)
    gpgv_output = '[GNUPG:] GOODSIG ' + ZABBIX_FPR[-16:] + ' Zabbix LLC (Apr 2024) <packager@zabbix.com>\n[GNUPG:] VALIDSIG ' + ZABBIX_FPR + ' 2026-09-22 1 0 4 0 1 10 01 X\n'
    monkeypatch.setattr(cpi.subprocess, 'run', lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, gpgv_output, ''))
    record = cpi.collect('synthetic', tmp_path / 'scratch')
    item = record['components']['zabbix_server']
    assert item['executable']['version_line'] == 'zabbix_server (Zabbix) 7.0.31'
    assert item['dpkg']['package'] == 'zabbix-server-pgsql' and item['dpkg']['architecture'] == 'amd64'
    assert item['dpkg']['file_verification'] == {'status': 'OBSERVED', 'clean': False, 'payload_verified': True,
                                                 'conffiles_changed': ['/etc/zabbix/zabbix_server.conf'],
                                                 'changed_files': [{'flags': '??5??????', 'path': '/etc/zabbix/zabbix_server.conf', 'kind': 'conffile'}]}
    assert item['apt']['installed'] == '1:7.0.31-1+ubuntu24.04'
    (entry,) = item['signatures']
    assert entry['signature']['verified'] is True and entry['signature']['keys'][0]['fingerprint'] == ZABBIX_FPR
    assert item['index']['status'] == 'OBSERVED' and item['index']['deb_sha256'] == '1' * 64 and item['index']['index_signed'] is True
    assert item['index']['origin_uri'] == 'https://repo.zabbix.com/zabbix/7.0/ubuntu' and item['cached_deb'] == {'status': 'OBSERVED', 'present': False}
    assert record['distribution'] == {'id': 'ubuntu', 'version_id': '24.04', 'architecture': 'amd64'}
    # The other components are missing on this synthetic host; the derived list says so and nothing else is read.
    assert any(item.startswith('postgres.executable: missing') for item in record['missing_observations'])
    assert record['missing_observations'] == cpi.missing_observations(record)
    assert not any(argv[0] in ('env', 'hostname', 'apt', 'apt-get') for argv in seen)
    assert 'hostname' not in json.dumps(record) and 'PATH' not in json.dumps(record)


def test_collect_marks_a_source_build_unpackaged_without_apt_or_signature_lookups(tmp_path, monkeypatch):
    binary = tmp_path / 'postgres'
    binary.write_bytes(b'#!/bin/sh\n')
    monkeypatch.setenv('ACK_LAB_PG_BIN', str(tmp_path))
    for key in ('ACK_LAB_PGBOUNCER_BIN', 'ZABALACK_ZABBIX_SERVER', 'ZABALACK_ZABBIX_UI', 'ZABALACK_ZABBIX_SCHEMA', 'ZABALACK_PHP'):
        monkeypatch.setenv(key, str(tmp_path / 'absent' / key))
    monkeypatch.setattr(cpi, 'OS_RELEASE', tmp_path / 'none')
    seen = []

    def command(argv, timeout=60):
        seen.append(argv[0])
        if argv[0] == 'dpkg' and argv[1] == '-S':
            return subprocess.CompletedProcess(argv, 1, '', 'dpkg-query: no path found matching pattern')
        if argv[0] == str(binary):
            return subprocess.CompletedProcess(argv, 0, 'postgres (PostgreSQL) 18.3\n', '')
        return subprocess.CompletedProcess(argv, 0, 'amd64\n', '')
    monkeypatch.setattr(cpi, 'command', command)
    record = cpi.collect('synthetic', tmp_path / 'scratch')
    postgres = record['components']['postgres']
    assert postgres['dpkg'] == {'status': 'OBSERVED', 'kind': 'unpackaged'}
    assert 'apt' not in postgres and 'signatures' not in postgres and 'index' not in postgres
    assert all(postgres['members'][name]['status'] == 'NOT_OBSERVED' for name in ('pg_ctl', 'initdb', 'psql'))
    assert 'apt-cache' not in seen
    result, deviations, incomplete = evaluate(dict(record, components={'postgres': postgres}))
    assert result == 'DEVIATES' and any('unpackaged' in item for item in deviations)
    assert any('postgres.members.psql' in item for item in incomplete)


def test_approve_writes_a_proposal_with_the_provenance_chain_only_from_a_conforming_record(tmp_path, monkeypatch, capsys):
    record_path = tmp_path / 'record.json'
    record_path.write_text(json.dumps(conforming_record()))
    reference = tmp_path / 'zabbix_reference.json'
    identities = tmp_path / 'native_service_identities.json'
    reference.write_text(json.dumps(REFERENCE))
    identities.write_text(json.dumps(IDENTITIES))
    before = (reference.read_text(), identities.read_text())
    output = tmp_path / 'proposal.json'
    assert cpi.main(['approve', str(record_path), '--reviewer', 'Reviewer', '--amendment', 'R30', '--output', str(output),
                     '--reference', str(reference), '--identities', str(identities)]) == 0
    proposal = json.loads(output.read_text())
    postgres = proposal['native_service_identities.reviewed']['postgres'][0]
    assert postgres['package'] == 'postgresql-18' and postgres['version'] == '18.6-1.pgdg24.04+2' and postgres['deb_sha256'] == 'c' * 64
    assert postgres['origin']['signed_by'] == [PGDG] and postgres['executable_sha256'] == 'a' * 64
    assert set(postgres['members']) == {'pg_ctl', 'initdb', 'psql'} and postgres['members']['psql']['package'] == 'postgresql-client-18'
    verified = proposal['zabbix_reference.ubuntu_packages.verified']
    assert verified['zabbix-frontend-php']['architecture'] == 'all' and verified['zabbix-server-pgsql']['origin']['signed_by'] == [ZABBIX_FPR]
    assert verified['php']['origin']['signed_by'] == [UBUNTU]
    assert proposal['source_record']['sha256'] == hashlib.sha256(record_path.read_bytes()).hexdigest()
    assert (reference.read_text(), identities.read_text()) == before
    assert json.loads(capsys.readouterr().out)['policy_files_changed'] is False
    for mutate in (lambda r: r['components']['zabbix_server']['executable'].update(version_line='zabbix_server (Zabbix) 7.0.30'),
                   lambda r: r['components'].pop('php'),
                   lambda r: r['components']['zabbix_server'].pop('index')):
        record = conforming_record()
        mutate(record)
        record_path.write_text(json.dumps(record))
        output.unlink(missing_ok=True)
        with pytest.raises(SystemExit, match='refusing to propose approved identities'):
            cpi.main(['approve', str(record_path), '--reviewer', 'Reviewer', '--amendment', 'R30', '--output', str(output),
                      '--reference', str(reference), '--identities', str(identities)])
        assert not output.exists()


@pytest.mark.parametrize('mutate,code', [(None, 0), (lambda r: r['distribution'].update(architecture='arm64'), 1), (lambda r: r['components'].pop('php'), 2)])
def test_validate_exit_codes(tmp_path, mutate, code, capsys):
    record = conforming_record()
    if mutate:
        mutate(record)
    path = tmp_path / 'record.json'
    path.write_text(json.dumps(record))
    assert cpi.main(['validate', str(path)]) == code
    assert json.loads(capsys.readouterr().out)['result'] == {0: 'CONFORMS', 1: 'DEVIATES', 2: 'INCOMPLETE'}[code]


def test_reference_and_policy_files_still_carry_no_approved_identities():
    """The x64 host's identities are collected there and reviewed; nothing here pre-populates them."""
    assert IDENTITIES['reviewed'] == {'postgres': [], 'pgbouncer': []}
    assert 'verified' not in REFERENCE['ubuntu_packages']
    assert REFERENCE['selected']['fallback']['version'] is None
    assert len(ZABBIX_FPR) == 40 and ZABBIX_FPR.endswith(REFERENCE['ubuntu_packages']['expected_signing_key']['long_id'])
