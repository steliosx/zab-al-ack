"""tools/lab_retention.py: preservation before a destructive lab reset (R26).

Synthetic files under a temporary root; nothing here is service evidence.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import lab_retention


def test_preserve_copies_existing_sources_privately_and_records_absent_ones(tmp_path):
    (tmp_path / 'evidence').mkdir()
    (tmp_path / '.lab').mkdir()
    report = tmp_path / 'evidence/native-run.json'
    report.write_bytes(b'{"status": "PASS"}\n')
    log = tmp_path / '.lab/postgres.log'
    log.write_bytes(b'synthetic postgres log\n')
    record = lab_retention.preserve(tmp_path, 'abc123def0', 'previous-run',
                                    [Path('evidence/native-run.json'), Path('.lab/postgres.log'), Path('.lab/initdb.log')])
    directory = tmp_path / record['directory']
    assert record['status'] == 'RETAINED' and record['retained'] == 2 and record['absent'] == 1
    assert record['directory'] == 'build/private/retained-phases/abc123def0/previous-run'
    manifest_bytes = (directory / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    assert manifest['absent'] == ['.lab/initdb.log']
    assert set(manifest['files']) == {'evidence__native-run.json', '.lab__postgres.log'}
    assert (directory / 'evidence__native-run.json').read_bytes() == report.read_bytes()
    assert (directory / '.lab__postgres.log').read_bytes() == log.read_bytes()
    entry = manifest['files']['evidence__native-run.json']
    assert entry == {'source': 'evidence/native-run.json', 'bytes': len(report.read_bytes()),
                     'sha256': hashlib.sha256(report.read_bytes()).hexdigest()}
    assert record['manifest_sha256'] == hashlib.sha256(manifest_bytes).hexdigest()
    assert 'synthetic postgres log' not in json.dumps(record)
    # A copy, never a move: the sources stay where the runner left them.
    assert report.exists() and log.exists()
    if os.name == 'posix':
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert stat.S_IMODE((directory / '.lab__postgres.log').stat().st_mode) == 0o600
        assert stat.S_IMODE((tmp_path / lab_retention.RETAINED).stat().st_mode) == 0o700
        # The shared build/private parent stays traversable for the pool's service user.
        assert stat.S_IMODE((tmp_path / 'build/private').stat().st_mode) & 0o011 == 0o011


def test_each_run_and_label_gets_its_own_directory_so_earlier_copies_are_never_replaced(tmp_path):
    source = tmp_path / 'evidence/native-run.json'
    source.parent.mkdir()
    source.write_text('first')
    first = lab_retention.preserve(tmp_path, 'aaaaaa1111', 'previous-run', [source])
    source.write_text('second')
    second = lab_retention.preserve(tmp_path, 'bbbbbb2222', 'previous-run', [source])
    assert (tmp_path / first['directory'] / 'evidence__native-run.json').read_text() == 'first'
    assert (tmp_path / second['directory'] / 'evidence__native-run.json').read_text() == 'second'
    with pytest.raises(lab_retention.RetentionError, match='already exists'):
        lab_retention.preserve(tmp_path, 'bbbbbb2222', 'previous-run', [source])
    assert (tmp_path / second['directory'] / 'evidence__native-run.json').read_text() == 'second'


@pytest.mark.parametrize('problem', ['symlink', 'dangling-symlink', 'oversized', 'fifo', 'bad-label', 'bad-run-id',
                                     'duplicate-source', 'unreadable'])
def test_preserve_fails_closed_and_never_seals_a_partial_manifest(tmp_path, monkeypatch, problem):
    source = tmp_path / 'evidence/native-run.json'
    source.parent.mkdir()
    source.write_text('{}')
    run_id, label, sources = 'abc123def0', 'phase-postgres', [source]
    if problem == 'symlink':
        link = tmp_path / 'evidence/native-postgres.xml'
        link.symlink_to(source)
        sources = [source, link]
    elif problem == 'dangling-symlink':
        link = tmp_path / 'evidence/native-postgres.xml'
        link.symlink_to(tmp_path / 'missing')
        sources = [source, link]
    elif problem == 'oversized':
        monkeypatch.setattr(lab_retention, 'LIMIT', 1)
    elif problem == 'fifo':
        if not hasattr(os, 'mkfifo'):
            pytest.skip('no FIFO support')
        os.mkfifo(tmp_path / 'evidence/native-postgres.log')
        sources = [source, tmp_path / 'evidence/native-postgres.log']
    elif problem == 'bad-label':
        label = 'Phase/../escape'
    elif problem == 'bad-run-id':
        run_id = '../escape'
    elif problem == 'duplicate-source':
        sources = [source, Path('evidence/native-run.json')]
    elif problem == 'unreadable':
        if os.geteuid() == 0:
            pytest.skip('root ignores file modes')
        source.chmod(0)
    with pytest.raises(lab_retention.RetentionError):
        lab_retention.preserve(tmp_path, run_id, label, sources)
    assert not list((tmp_path / 'build').rglob('manifest.json')) if (tmp_path / 'build').exists() else True
    assert source.exists()


def test_a_copy_that_does_not_match_its_source_is_refused(tmp_path, monkeypatch):
    source = tmp_path / 'evidence/native-run.json'
    source.parent.mkdir()
    source.write_bytes(b'{"status": "PASS"}\n')
    real = lab_retention.hashlib.file_digest

    def corrupted(handle, name):
        digest = real(handle, name)
        digest.update(b'tamper')
        return digest
    monkeypatch.setattr(lab_retention.hashlib, 'file_digest', corrupted)
    with pytest.raises(lab_retention.RetentionError, match='does not match'):
        lab_retention.preserve(tmp_path, 'abc123def0', 'phase-postgres', [source])
