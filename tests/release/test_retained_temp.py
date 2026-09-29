"""Owned, never-rotated pytest scratch and its retained copy, using temporary local files only."""
import json
import os
import socket
import stat
import tempfile

import pytest

import retained_temp


@pytest.fixture
def temp_root(tmp_path, monkeypatch):
    root = tmp_path / 'system-temp'
    root.mkdir()
    monkeypatch.setattr(tempfile, 'tempdir', str(root))
    return root


def test_fresh_basetemp_is_an_owned_absent_path_below_a_new_private_root(temp_root):
    first = retained_temp.fresh_basetemp('reference-unit-mocked')
    second = retained_temp.fresh_basetemp('reference-unit-mocked')
    assert first.parent != second.parent
    for basetemp in (first, second):
        # pytest empties an existing basetemp; a fresh root means nothing else is removed.
        assert not basetemp.exists() and basetemp.name == 'b'
        assert basetemp.parent.parent == temp_root and basetemp.parent.name.startswith('zab-')
        assert stat.S_IMODE(basetemp.parent.stat().st_mode) == 0o700
        owner = json.loads((basetemp.parent / 'OWNER.json').read_text())
        assert owner['label'] == 'reference-unit-mocked' and owner['pid'] == os.getpid()


def test_the_root_stays_short_for_socket_fixtures(monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', '/tmp')
    basetemp = retained_temp.fresh_basetemp('length')
    try:
        # Below today's /tmp/pytest-of-<user>/pytest-<n> for any user name.
        assert len(str(basetemp)) <= len('/tmp/zab-') + 8 + len('/b')
    finally:
        (basetemp.parent / 'OWNER.json').unlink()
        basetemp.parent.rmdir()


def scratch_tree(tmp_path):
    basetemp = tmp_path / 'zab-owned' / 'b'
    (basetemp / 'test_a0/nested').mkdir(parents=True)
    (basetemp.parent / 'OWNER.json').write_text('{"label": "synthetic"}')
    (basetemp / 'test_a0/nested/data.json').write_bytes(b'{"x": 1}\n')
    outside = tmp_path / 'outside.txt'
    outside.write_text('must not be copied through the link')
    (basetemp / 'test_a0/link').symlink_to(outside)
    os.mkfifo(basetemp / 'test_a0/pipe')
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(basetemp / 'test_a0/s.sock'))
    closed = basetemp / 'test_a0/unreadable.env'
    closed.write_text('fixture')
    closed.chmod(0)
    return basetemp, server, closed


@pytest.mark.skipif(os.geteuid() == 0, reason='root reads mode-0 files')
def test_retain_copies_files_and_lists_links_special_and_unreadable_entries(tmp_path):
    basetemp, server, closed = scratch_tree(tmp_path)
    try:
        record = retained_temp.retain(basetemp, tmp_path / 'raw/basetemp/phase')
    finally:
        server.close()
    assert record['status'] == 'COMPLETE' and record['owner'] == '{"label": "synthetic"}'
    scratch = tmp_path / 'raw/basetemp/phase/scratch'
    assert (scratch / 'test_a0/nested/data.json').read_bytes() == b'{"x": 1}\n'
    assert record['files']['test_a0/nested/data.json']['size'] == 9
    # Links are copied as links; the target is never read through them.
    assert (scratch / 'test_a0/link').is_symlink()
    assert os.readlink(scratch / 'test_a0/link') == str(tmp_path / 'outside.txt')
    assert set(record['special']) == {'test_a0/pipe', 'test_a0/s.sock'}
    assert list(record['unreadable']) == ['test_a0/unreadable.env']
    # The original keeps its permissions and stays in place.
    assert stat.S_IMODE(closed.stat().st_mode) == 0
    assert basetemp.is_dir()
    assert json.loads((tmp_path / 'raw/basetemp/phase/retention.json').read_text()) == record


def test_retain_marks_a_vanished_entry_incomplete(tmp_path, monkeypatch):
    basetemp, server, closed = scratch_tree(tmp_path)
    server.close()
    closed.chmod(0o600)
    original = retained_temp._copy_file

    def vanish(source, target):
        if source.name == 'data.json':
            raise FileNotFoundError(2, 'No such file or directory')
        return original(source, target)

    monkeypatch.setattr(retained_temp, '_copy_file', vanish)
    record = retained_temp.retain(basetemp, tmp_path / 'raw/basetemp/phase')
    assert record['status'] == 'INCOMPLETE'
    assert list(record['errors']) == ['test_a0/nested/data.json']


def test_an_unused_basetemp_is_recorded_not_invented(tmp_path):
    record = retained_temp.retain(tmp_path / 'zab-owned/b', tmp_path / 'raw/basetemp/phase')
    assert record['status'] == 'NOT_CREATED' and not record['files']


def test_retention_never_overwrites_an_earlier_copy(tmp_path):
    destination = tmp_path / 'raw/basetemp/phase'
    retained_temp.retain(tmp_path / 'absent/b', destination)
    with pytest.raises(FileExistsError):
        retained_temp.retain(tmp_path / 'absent/b', destination)
