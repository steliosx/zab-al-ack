"""tools/collect_package_identities.py: observations, validation and the approval proposal (R29).

Host commands are mocked; the synthetic records name a conforming Ubuntu 24.04
amd64 host and every deviation the validator must name. Nothing is approved
from an assumed, synthetic or source-built identity.
"""
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
ZABBIX_KEY = REFERENCE['ubuntu_packages']['expected_signing_key']['long_id']
PGDG = REFERENCE['dependencies']['postgresql']['pgdg_key_fingerprint']
POLICY = '''zabbix-server-pgsql:
  Installed: 1:7.0.31-1+ubuntu24.04
  Candidate: 1:7.0.31-1+ubuntu24.04
  Version table:
 *** 1:7.0.31-1+ubuntu24.04 500
        500 https://repo.zabbix.com/zabbix/7.0/release/ubuntu noble/main amd64 Packages
        100 /var/lib/dpkg/status
     1:7.0.30-1+ubuntu24.04 500
        500 https://repo.zabbix.com/zabbix/7.0/release/ubuntu noble/main amd64 Packages
'''


def signature(host, long_id, fingerprint, verified=True):
    return {'uri': 'https://' + host + '/x', 'suite': 'noble/main', 'architecture': 'amd64', 'priority': 500,
            'signature': {'status': 'OBSERVED', 'inrelease': host + '_x_dists_noble_InRelease', 'verified': verified,
                          'keys': [{'long_id': long_id, 'fingerprint': fingerprint, 'user_id': 'synthetic', 'keyring': 'k.gpg'}] if verified else [],
                          'keyrings_tried': 1}}


def component(version_line, package, version, host, long_id, fingerprint, *, kind='dpkg', clean=True):
    item = {'executable': {'status': 'OBSERVED', 'path': '/x', 'real_path': '/x', 'sha256': 'a' * 64, 'version_line': version_line}}
    if kind == 'unpackaged':
        item['dpkg'] = {'status': 'OBSERVED', 'kind': 'unpackaged'}
        return item
    item['dpkg'] = {'status': 'OBSERVED', 'kind': 'dpkg', 'package': package, 'version': version, 'architecture': 'amd64',
                    'package_status': 'install ok installed',
                    'file_verification': {'status': 'OBSERVED', 'clean': clean, 'executables_verified': clean,
                                          'changed_files': [] if clean else [{'flags': '??5??????', 'path': '/usr/sbin/x', 'documentation': False}]}}
    item['apt'] = {'status': 'OBSERVED', 'installed': version, 'origins': [{'priority': 500, 'uri': 'https://' + host + '/x', 'suite': 'noble/main', 'architecture': 'amd64'}]}
    item['signatures'] = [signature(host, long_id, fingerprint)]
    return item


def conforming_record():
    zver = '1:' + PIN + '-1+ubuntu24.04'
    return {
        'schema_version': 1, 'label': 'synthetic-host', 'collected_at': '2026-09-22T00:00:00+00:00',
        'distribution': {'id': 'ubuntu', 'version_id': '24.04', 'architecture': 'amd64'},
        'components': {
            'postgres': component('postgres (PostgreSQL) 18.3 (Ubuntu 18.3-1.pgdg24.04+1)', 'postgresql-18', '18.3-1.pgdg24.04+1', 'apt.postgresql.org', PGDG[-16:], PGDG),
            'pgbouncer': component('PgBouncer 1.24.1', 'pgbouncer', '1.24.1-1.pgdg24.04+1', 'apt.postgresql.org', PGDG[-16:], PGDG),
            'zabbix_server': component('zabbix_server (Zabbix) ' + PIN, 'zabbix-server-pgsql', zver, 'repo.zabbix.com', ZABBIX_KEY, '4C3D6F2CC75F5146754FC374' + ZABBIX_KEY),
            'zabbix_frontend': component(PIN, 'zabbix-frontend-php', zver, 'repo.zabbix.com', ZABBIX_KEY, '4C3D6F2CC75F5146754FC374' + ZABBIX_KEY),
            'zabbix_sql_scripts': component(None, 'zabbix-sql-scripts', zver, 'repo.zabbix.com', ZABBIX_KEY, '4C3D6F2CC75F5146754FC374' + ZABBIX_KEY),
            'php': component('PHP 8.3.6 (cli) (built: Sep  2 2026 12:56:02) (NTS)', 'php8.3-cli', '8.3.6-0ubuntu0.24.04.11', 'archive.ubuntu.com', '871920D1991BC93C', 'F6ECB3762474EDA9D21B7022871920D1991BC93C'),
        },
        'missing_observations': [],
    }


def test_a_complete_conforming_record_conforms():
    assert cpi.evaluate(conforming_record(), REFERENCE, IDENTITIES) == ('CONFORMS', [], [])


@pytest.mark.parametrize('mutate,fragment', [
    (lambda r: r['components']['zabbix_server']['executable'].update(version_line='zabbix_server (Zabbix) 7.0.30'), 'not the pinned'),
    (lambda r: r['components']['zabbix_frontend']['executable'].update(version_line='7.0.30'), 'ZABBIX_VERSION 7.0.30'),
    (lambda r: r['components']['zabbix_sql_scripts']['dpkg'].update(version='1:7.0.30-1+ubuntu24.04'), 'different versions'),
    (lambda r: r['components']['zabbix_server']['dpkg'].update(version='1:7.0.31-1+ubuntu22.04'), 'does not match 1:' + PIN),
    (lambda r: r['components']['postgres']['executable'].update(version_line='postgres (PostgreSQL) 16.4'), 'major 16'),
    (lambda r: r['components']['pgbouncer']['executable'].update(version_line='PgBouncer 1.23.1'), 'pgbouncer: 1.23.1'),
    (lambda r: r['components']['php']['executable'].update(version_line='PHP 7.4.33 (cli)'), 'below the frontend minimum'),
    (lambda r: r['components'].__setitem__('postgres', component('postgres (PostgreSQL) 18.3', None, None, None, None, None, kind='unpackaged')), 'unpackaged executable'),
    (lambda r: r['components']['zabbix_server']['apt']['origins'].__setitem__(0, {'priority': 500, 'uri': 'https://mirror.example/zabbix', 'suite': 'noble/main', 'architecture': 'amd64'}), 'origin'),
    (lambda r: r['components']['zabbix_server']['signatures'].__setitem__(0, signature('repo.zabbix.com', '0000000000000000', '0' * 40)), 'expected ' + ZABBIX_KEY),
    (lambda r: r['components']['postgres']['signatures'].__setitem__(0, signature('apt.postgresql.org', '0000000000000000', '0' * 40)), 'PGDG key'),
    (lambda r: r['components']['php']['signatures'].__setitem__(0, signature('archive.ubuntu.com', '', '', verified=False)), 'no origin InRelease verified'),
    (lambda r: r['components']['php']['dpkg']['file_verification'].update(executables_verified=False, changed_files=[{'flags': '??5??????', 'path': '/usr/bin/php8.3', 'documentation': False}]), 'changed or missing package files: /usr/bin/php8.3'),
    (lambda r: r['components']['php']['dpkg'].update(package_status='deinstall ok config-files'), 'is not installed'),
    (lambda r: r['components']['php']['dpkg'].update(architecture='arm64'), 'expected amd64'),
    (lambda r: r['components']['zabbix_server']['apt'].update(installed='1:7.0.30-1+ubuntu24.04'), 'differs from dpkg'),
    (lambda r: r['distribution'].update(version_id='22.04'), 'expected Ubuntu 24.04 amd64'),
    (lambda r: r['components']['zabbix_frontend']['dpkg'].update(package='zabbix-frontend-nginx'), 'expected zabbix-frontend-php'),
], ids=['server-version', 'frontend-version', 'mixed-zabbix-versions', 'debian-revision-suite', 'postgres-major', 'pgbouncer-minor',
        'php-minimum', 'source-build', 'foreign-origin', 'zabbix-key', 'pgdg-key', 'unverified-inrelease', 'changed-executable',
        'not-installed', 'architecture', 'apt-dpkg-disagree', 'distribution', 'package-name'])
def test_every_deviation_is_named(mutate, fragment):
    record = conforming_record()
    mutate(record)
    result, deviations, incomplete = cpi.evaluate(record, REFERENCE, IDENTITIES)
    assert result == 'DEVIATES' and incomplete == []
    assert any(fragment in item for item in deviations), deviations


@pytest.mark.parametrize('mutate,fragment', [
    (lambda r: r['components'].pop('pgbouncer'), 'pgbouncer: not collected'),
    (lambda r: r['missing_observations'].append('postgres.executable: missing: /usr/lib/postgresql/18/bin/postgres'), 'postgres.executable'),
    (lambda r: r['components']['zabbix_server']['executable'].update(version_line=None), 'version not observed'),
    (lambda r: r['components']['php']['dpkg'].update(file_verification={'status': 'NOT_OBSERVED', 'reason': 'dpkg -V unavailable'}), 'file verification not observed'),
    (lambda r: r['components']['php'].__setitem__('signatures', []), 'no origin signature observation'),
    (lambda r: r['components']['php']['signatures'].__setitem__(0, dict(signature('archive.ubuntu.com', '', ''), signature={'status': 'NOT_OBSERVED', 'reason': 'gpgv unavailable'})), 'origin signature not observed (gpgv unavailable)'),
], ids=['component-missing', 'executable-missing', 'version-missing', 'dpkg-verify-missing', 'no-signature-observation', 'signature-not-observed'])
def test_a_missing_observation_is_incomplete_never_conforms(mutate, fragment):
    record = conforming_record()
    mutate(record)
    result, deviations, incomplete = cpi.evaluate(record, REFERENCE, IDENTITIES)
    assert result == 'INCOMPLETE'
    assert any(fragment in item for item in incomplete + deviations), (incomplete, deviations)


def test_documentation_only_dpkg_differences_are_recorded_but_do_not_unverify_the_executables():
    text = 'missing   /usr/share/man/man1/php8.3.1.gz\n??5?????? c /etc/php/8.3/cli/php.ini\n'
    record = cpi.verification_observation(text)
    assert record['clean'] is False and record['executables_verified'] is False
    assert [entry['path'] for entry in record['changed_files'] if entry['documentation']] == ['/usr/share/man/man1/php8.3.1.gz']
    docs_only = cpi.verification_observation('missing   /usr/share/doc/php8.3-cli/changelog.gz\n')
    assert docs_only['clean'] is False and docs_only['executables_verified'] is True
    assert cpi.verification_observation('') == {'status': 'OBSERVED', 'clean': True, 'executables_verified': True, 'changed_files': []}


def test_apt_policy_parsing_attributes_only_the_installed_version_origins():
    parsed = cpi.parse_policy(POLICY)
    assert parsed['installed'] == '1:7.0.31-1+ubuntu24.04'
    assert parsed['origins'] == [{'priority': 500, 'uri': 'https://repo.zabbix.com/zabbix/7.0/release/ubuntu', 'suite': 'noble/main', 'architecture': 'amd64'}]
    assert cpi.parse_policy(POLICY.replace('Installed: 1:7.0.31-1+ubuntu24.04', 'Installed: (none)'))['installed'] is None


def test_inrelease_list_file_name_follows_apt_mangling(tmp_path):
    path = cpi.inrelease_path('https://repo.zabbix.com/zabbix/7.0/release/ubuntu', 'noble/main', tmp_path)
    assert path == tmp_path / 'repo.zabbix.com_zabbix_7.0_release_ubuntu_dists_noble_InRelease'
    assert cpi.inrelease_path('http://apt.postgresql.org/pub/repos/apt/', 'noble-pgdg/main', tmp_path).name == 'apt.postgresql.org_pub_repos_apt_dists_noble-pgdg_InRelease'


def test_collect_records_dpkg_apt_and_signature_observations_for_a_packaged_component(tmp_path, monkeypatch):
    server = tmp_path / 'zabbix_server'
    server.write_bytes(b'#!/bin/sh\n')
    monkeypatch.setenv('ZABALACK_ZABBIX_SERVER', str(server))
    for key in ('ACK_LAB_PG_BIN', 'ACK_LAB_PGBOUNCER_BIN', 'ZABALACK_ZABBIX_UI', 'ZABALACK_ZABBIX_SCHEMA', 'ZABALACK_PHP'):
        monkeypatch.setenv(key, str(tmp_path / 'absent' / key))
    lists = tmp_path / 'lists'
    lists.mkdir()
    (lists / 'repo.zabbix.com_zabbix_7.0_release_ubuntu_dists_noble_InRelease').write_text('synthetic')
    monkeypatch.setattr(cpi, 'APT_LISTS', lists)
    keyring_dir = tmp_path / 'keyrings'
    keyring_dir.mkdir()
    (keyring_dir / 'zabbix.gpg').write_bytes(b'\x99binary')
    monkeypatch.setattr(cpi, 'KEYRING_DIRS', (keyring_dir,))
    monkeypatch.setattr(cpi, 'OS_RELEASE', tmp_path / 'os-release')
    (tmp_path / 'os-release').write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
    outputs = {
        ('dpkg', '-S', str(server)): 'zabbix-server-pgsql: ' + str(server) + '\n',
        ('dpkg-query', '-W', '-f', '${Package}\t${Version}\t${Architecture}\t${Status}', 'zabbix-server-pgsql'):
            'zabbix-server-pgsql\t1:7.0.31-1+ubuntu24.04\tamd64\tinstall ok installed',
        ('dpkg', '-V', 'zabbix-server-pgsql'): '',
        ('apt-cache', 'policy', 'zabbix-server-pgsql'): POLICY,
        ('dpkg', '--print-architecture'): 'amd64\n',
        (str(server), '-V'): 'zabbix_server (Zabbix) 7.0.31\nRevision fc62e96 22 September 2026\n',
    }
    seen = []

    def command(argv, timeout=60):
        seen.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, outputs[tuple(argv)], '')
    monkeypatch.setattr(cpi, 'command', command)
    monkeypatch.setattr(cpi.shutil, 'which', lambda name: '/usr/bin/' + name)
    gpgv_output = '[GNUPG:] GOODSIG ' + ZABBIX_KEY + ' Zabbix LLC <packager@zabbix.com>\n[GNUPG:] VALIDSIG 4C3D6F2CC75F5146754FC374' + ZABBIX_KEY + ' 2026-09-22 1 0 4 0 1 10 01 X\n'
    monkeypatch.setattr(cpi.subprocess, 'run', lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, gpgv_output, ''))
    record = cpi.collect('synthetic', tmp_path / 'scratch')
    server_record = record['components']['zabbix_server']
    assert server_record['executable']['version_line'] == 'zabbix_server (Zabbix) 7.0.31'
    assert server_record['dpkg'] == {'status': 'OBSERVED', 'kind': 'dpkg', 'package': 'zabbix-server-pgsql', 'version': '1:7.0.31-1+ubuntu24.04',
                                     'architecture': 'amd64', 'package_status': 'install ok installed',
                                     'file_verification': {'status': 'OBSERVED', 'clean': True, 'executables_verified': True, 'changed_files': []}}
    assert server_record['apt']['installed'] == '1:7.0.31-1+ubuntu24.04'
    (entry,) = server_record['signatures']
    assert entry['signature']['verified'] is True and entry['signature']['keys'][0]['long_id'] == ZABBIX_KEY
    assert record['distribution'] == {'id': 'ubuntu', 'version_id': '24.04', 'architecture': 'amd64'}
    # The other components are missing on this synthetic host and say so; nothing else is read.
    assert len(record['missing_observations']) == 5 and all('missing:' in item for item in record['missing_observations'])
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
    assert record['components']['postgres']['dpkg'] == {'status': 'OBSERVED', 'kind': 'unpackaged'}
    assert 'apt' not in record['components']['postgres'] and 'signatures' not in record['components']['postgres']
    assert 'apt-cache' not in seen
    result, deviations, _ = cpi.evaluate(dict(record, components={'postgres': record['components']['postgres']}), REFERENCE, IDENTITIES)
    assert result == 'INCOMPLETE' and any('unpackaged' in item for item in deviations)


def test_approve_writes_a_proposal_only_from_a_conforming_record_and_never_edits_policy_files(tmp_path, monkeypatch, capsys):
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
    assert proposal['native_service_identities.reviewed'] == {
        'postgres': [{'kind': 'dpkg', 'package': 'postgresql-18', 'version': '18.3-1.pgdg24.04+1', 'architecture': 'amd64'}],
        'pgbouncer': [{'kind': 'dpkg', 'package': 'pgbouncer', 'version': '1.24.1-1.pgdg24.04+1', 'architecture': 'amd64'}]}
    verified = proposal['zabbix_reference.ubuntu_packages.verified']
    assert verified['zabbix-server-pgsql']['version'] == '1:' + PIN + '-1+ubuntu24.04' and verified['origin']['origins'][0]['keys'][0]['long_id'] == ZABBIX_KEY
    assert proposal['reviewer'] == 'Reviewer' and proposal['amendment'] == 'R30'
    assert proposal['source_record']['sha256'] == __import__('hashlib').sha256(record_path.read_bytes()).hexdigest()
    assert (reference.read_text(), identities.read_text()) == before
    assert json.loads(capsys.readouterr().out)['policy_files_changed'] is False
    # A record that deviates or is incomplete is refused: no assumed or synthetic identity becomes approved.
    for mutate in (lambda r: r['components']['zabbix_server']['executable'].update(version_line='zabbix_server (Zabbix) 7.0.30'),
                   lambda r: r['components'].pop('php')):
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
