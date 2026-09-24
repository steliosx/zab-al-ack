"""tools/collect_attempt_evidence.py: every available record, diagnostic, manifest and log, privately, never a secret.

R5 review (evidence lifecycle): the service logs of an unprovisioned cycle were lost with its temporary checkout,
and a refused attempt kept only its driver summary. The collector runs before cleanup whatever the outcome.
"""
import hashlib
import json

import pytest

import collect_attempt_evidence as collector


def checkout_after(tmp_path, outcome):
    root = tmp_path / 'checkout'
    (root / 'evidence').mkdir(parents=True)
    (root / '.zabbix-lab').mkdir()
    (root / '.zabbix-lab/zabbix_server.log').write_text('synthetic server log\n')
    (root / '.zabbix-lab/trust-unit.owner.json').write_text('{}\n')
    (root / '.zabbix-lab/zabbix-db-password').write_text('synthetic secret\n')
    (root / '.zabbix-lab/pgdata').mkdir()
    (root / '.zabbix-lab/pgdata/postgresql.conf').write_text('not a lab record\n')
    (root / '.deployment-lab/logs').mkdir(parents=True)
    (root / '.deployment-lab/logs/postgres.log').write_text('synthetic postgres log\n')
    (root / '.deployment-lab/db-password.json').write_text('synthetic key\n')  # matches a pattern, so its name must stop it
    if outcome != 'not-run':
        (root / 'evidence/deployment-e2e-run.json').write_text(json.dumps({'status': outcome}) + '\n')
    if outcome == 'retention-refused':
        (root / 'evidence/deployment-e2e-run.retention-failure.json').write_text('{"result": "NOT_A_RESULT"}\n')
        phase = root / 'build/private/retained-phases/abc123/deployment-cycle'
        phase.mkdir(parents=True)
        (phase / 'manifest.json').write_text('{}\n')
        (phase / 'logs__server.log').write_text('retained log\n')
    return root


@pytest.mark.parametrize('outcome', ['PASS', 'FAIL', 'REFUSED', 'retention-refused', 'not-run'])
def test_every_outcome_is_collected_with_digests_and_nothing_secret(tmp_path, outcome):
    root = checkout_after(tmp_path, outcome)
    bundle = tmp_path / 'private-evidence'
    manifest = collector.collect(bundle, 'attempt-1', [('checkout', 'checkout', root)])
    copied = {entry['path'] for entry in manifest['files']}
    assert {'.zabbix-lab/zabbix_server.log', '.zabbix-lab/trust-unit.owner.json', '.deployment-lab/logs/postgres.log'} <= copied
    assert ('evidence/deployment-e2e-run.json' in copied) is (outcome != 'not-run')
    assert [entry['path'] for entry in manifest['absent']] == ([] if outcome != 'not-run' else ['evidence/deployment-e2e-run.json'])
    if outcome == 'retention-refused':
        assert {'evidence/deployment-e2e-run.retention-failure.json', 'build/private/retained-phases/abc123/deployment-cycle/manifest.json',
                'build/private/retained-phases/abc123/deployment-cycle/logs__server.log'} <= copied
    assert not any('password' in name or name.endswith('.key') or 'pgdata' in name for name in copied)
    assert {entry['path'] for entry in manifest['excluded']} == {'.deployment-lab/db-password.json'}  # listed by name, not copied
    for entry in manifest['files']:
        copy = bundle / entry['bundle_path']
        assert hashlib.sha256(copy.read_bytes()).hexdigest() == entry['sha256'] == hashlib.sha256((root / entry['path']).read_bytes()).hexdigest()
        assert (copy.stat().st_mode & 0o777) == 0o600
    assert (bundle.stat().st_mode & 0o777) == 0o700 and (bundle / 'attempt-1').stat().st_mode & 0o777 == 0o700
    assert 'PRIVATE' in (bundle / 'PRIVATE.txt').read_text()
    assert not any(b'synthetic secret' in path.read_bytes() or b'synthetic key' in path.read_bytes()
                   for path in bundle.rglob('*') if path.is_file())


def test_later_collections_add_to_the_same_attempt(tmp_path):
    root = checkout_after(tmp_path, 'FAIL')
    result = tmp_path / 'driver-result.json'
    result.write_text('{"summary": {}}\n')
    bundle = tmp_path / 'private-evidence'
    collector.collect(bundle, 'attempt-1', [('checkout', 'checkout', root)])
    manifest = collector.collect(bundle, 'attempt-1', [('file', 'result', result), ('tree', 'gateway-logs', tmp_path / 'absent-logs')])
    assert len(manifest['collections']) == 2 and 'driver-result.json' in {entry['path'] for entry in manifest['files']}
    assert {'source': 'gateway-logs', 'path': str(tmp_path / 'absent-logs')} in manifest['absent']


def test_the_bundle_is_never_inside_a_collected_source(tmp_path):
    root = checkout_after(tmp_path, 'PASS')
    with pytest.raises(collector.CollectionError, match='inside the collected source'):
        collector.collect(root / 'evidence/private', 'attempt-1', [('checkout', 'checkout', root)])
    assert not (root / 'evidence/private').exists()


def test_links_are_listed_not_followed(tmp_path):
    root = checkout_after(tmp_path, 'PASS')
    (tmp_path / 'outside.txt').write_text('outside the checkout\n')
    (root / '.zabbix-lab/linked.log').symlink_to(tmp_path / 'outside.txt')
    manifest = collector.collect(tmp_path / 'bundle', 'attempt-1', [('checkout', 'checkout', root)])
    assert {'source': 'checkout', 'path': '.zabbix-lab/linked.log', 'reason': 'symbolic link', 'target': str(tmp_path / 'outside.txt')} \
        in manifest['excluded']


def test_the_cli_reports_and_refuses(tmp_path, capsys):
    root = checkout_after(tmp_path, 'REFUSED')
    assert collector.main(['--bundle', str(tmp_path / 'bundle'), '--attempt', 'a1', '--checkout', f'checkout={root}']) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'COLLECTED'
    assert collector.main(['--bundle', str(tmp_path / 'bundle'), '--attempt', '../escape', '--checkout', f'checkout={root}']) == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'REFUSED'
