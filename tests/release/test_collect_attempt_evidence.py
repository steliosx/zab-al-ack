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
    assert manifest['failed'] == [] and collector.verdict(manifest) == 'COLLECTED'  # an intentional exclusion is not a failure
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


# Review of 97925db (evidence lifecycle): a copy error was listed as an exclusion and the collection still read
# COLLECTED; a linked .zabbix-lab was followed outside the checkout; a reused label overwrote an earlier copy.
def test_links_anywhere_on_the_path_are_failures_and_never_followed(tmp_path):
    root = checkout_after(tmp_path, 'PASS')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'outside.log').write_text('outside the checkout\n')
    (root / '.zabbix-lab/linked.log').symlink_to(outside / 'outside.log')
    (root / '.deployment-lab/logs').rename(tmp_path / 'real-logs')
    (root / '.deployment-lab/logs').symlink_to(tmp_path / 'real-logs', target_is_directory=True)  # a linked ancestor
    manifest = collector.collect(tmp_path / 'bundle', 'attempt-1', [('checkout', 'checkout', root)])
    failed = {entry['path']: entry['reason'] for entry in manifest['failed']}
    assert set(failed) == {'.zabbix-lab/linked.log', '.deployment-lab/logs'}
    assert all('link on the path, not followed' in reason for reason in failed.values())
    assert manifest['complete'] is False and collector.verdict(manifest) == 'INCOMPLETE'
    copied = [path for path in (tmp_path / 'bundle').rglob('*') if path.is_file()]
    assert not any(b'outside the checkout' in path.read_bytes() or b'synthetic postgres log' in path.read_bytes() for path in copied)


def test_a_linked_source_root_is_refused(tmp_path):
    root = checkout_after(tmp_path, 'PASS')
    (tmp_path / 'link').symlink_to(root, target_is_directory=True)
    manifest = collector.collect(tmp_path / 'bundle', 'attempt-1', [('checkout', 'checkout', tmp_path / 'link')])
    assert manifest['files'] == [] and manifest['failed'][0]['reason'] == 'the source root is a link'


def test_a_copy_failure_is_incomplete_not_collected(tmp_path, monkeypatch, capsys):
    root = checkout_after(tmp_path, 'FAIL')
    def failing_fsync(fd):
        raise OSError(5, 'synthetic destination I/O failure')
    monkeypatch.setattr(collector.os, 'fsync', failing_fsync)
    manifest = collector.collect(tmp_path / 'bundle', 'attempt-1', [('checkout', 'checkout', root)])
    assert manifest['files'] == [] and len(manifest['failed']) == 4 and collector.verdict(manifest) == 'INCOMPLETE'
    assert all('synthetic destination I/O failure' in entry['reason'] for entry in manifest['failed'])
    assert not [path for path in (tmp_path / 'bundle/attempt-1').rglob('*.partial')]  # no half copies left behind
    assert collector.main(['--bundle', str(tmp_path / 'bundle2'), '--attempt', 'a2', '--checkout', f'checkout={root}']) == 3
    assert json.loads(capsys.readouterr().out)['status'] == 'INCOMPLETE'


def test_an_unusable_bundle_is_a_collection_error(tmp_path):
    root = checkout_after(tmp_path, 'FAIL')
    (tmp_path / 'bundle').write_text('a regular file where the bundle should be\n')
    with pytest.raises(collector.CollectionError, match='cannot be prepared'):
        collector.collect(tmp_path / 'bundle', 'attempt-1', [('checkout', 'checkout', root)])


def test_a_repeated_label_never_overwrites_a_retained_copy(tmp_path):
    root = checkout_after(tmp_path, 'FAIL')
    record = root / 'evidence/deployment-e2e-run.json'
    first = collector.collect(tmp_path / 'bundle', 'same', [('checkout', 'checkout', root)])
    entry = next(item for item in first['files'] if item['path'] == 'evidence/deployment-e2e-run.json')
    again = collector.collect(tmp_path / 'bundle', 'same', [('checkout', 'checkout', root)])  # the same bytes: accepted once
    assert again['complete'] is True and len(again['files']) == len(first['files'])
    record.write_text('{"status": "a different attempt"}\n')
    changed = collector.collect(tmp_path / 'bundle', 'same', [('checkout', 'checkout', root)])
    assert changed['complete'] is False
    assert changed['failed'] == [{'source': 'checkout', 'path': 'evidence/deployment-e2e-run.json',
                                  'reason': 'not retained: CollectionError: a different file is already retained at deployment-e2e-run.json'}]
    assert collector.sha256(tmp_path / 'bundle' / entry['bundle_path']) == entry['sha256']  # the first copy is intact
    with pytest.raises(collector.CollectionError, match='cannot be prepared'):
        collector.collect(tmp_path / 'bundle', 'same', [('checkout', 'checkout', root)], new=True)


def test_attempt_labels_are_fresh():
    labels = {collector.attempt_label('s2-acceptance') for _ in range(200)}
    assert len(labels) == 200 and all(collector.LABEL.fullmatch(label) for label in labels)


def test_the_cli_reports_and_refuses(tmp_path, capsys):
    root = checkout_after(tmp_path, 'REFUSED')
    assert collector.main(['--bundle', str(tmp_path / 'bundle'), '--attempt', 'a1', '--checkout', f'checkout={root}']) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'COLLECTED'
    assert collector.main(['--bundle', str(tmp_path / 'bundle'), '--attempt', '../escape', '--checkout', f'checkout={root}']) == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'REFUSED'
