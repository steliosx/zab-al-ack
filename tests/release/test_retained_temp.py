"""Owned, never-rotated pytest scratch and its retained copy, using temporary local files only."""
import json
import os
import socket
import stat
import tempfile
from types import SimpleNamespace

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
        info = basetemp.parent.stat()
        assert owner['label'] == 'reference-unit-mocked' and owner['pid'] == os.getpid()
        assert (owner['device'], owner['inode']) == (info.st_dev, info.st_ino)
    first_owner = json.loads((first.parent / 'OWNER.json').read_text())
    second_owner = json.loads((second.parent / 'OWNER.json').read_text())
    assert len(first_owner['invocation']) == 32 and first_owner['invocation'] != second_owner['invocation']


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
    basetemp = retained_temp.fresh_basetemp('synthetic')
    (basetemp / 'test_a0/nested').mkdir(parents=True)
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
def test_retain_copies_files_and_lists_links_special_and_unreadable_entries(tmp_path, temp_root):
    basetemp, server, closed = scratch_tree(tmp_path)
    try:
        record = retained_temp.retain(basetemp, tmp_path / 'raw/basetemp/phase')
    finally:
        server.close()
    assert record['status'] == 'COMPLETE' and json.loads(record['owner'])['label'] == 'synthetic'
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


def owned_scratch(tmp_path):
    basetemp, server, closed = scratch_tree(tmp_path)
    server.close()
    closed.chmod(0o600)
    return basetemp


def test_retain_marks_a_vanished_entry_incomplete(tmp_path, temp_root, monkeypatch):
    basetemp = owned_scratch(tmp_path)
    original = retained_temp._copy_file

    def vanish(source, target):
        if source.name == 'data.json':
            raise FileNotFoundError(2, 'No such file or directory')
        return original(source, target)

    monkeypatch.setattr(retained_temp, '_copy_file', vanish)
    record = retained_temp.retain(basetemp, tmp_path / 'raw/basetemp/phase')
    assert record['status'] == 'INCOMPLETE'
    assert list(record['errors']) == ['test_a0/nested/data.json']


def test_a_file_changing_during_its_copy_is_incomplete(tmp_path, temp_root, monkeypatch):
    basetemp = owned_scratch(tmp_path)
    real_lstat = os.lstat
    seen = []

    def racing(path, *args, **kwargs):
        info = real_lstat(path, *args, **kwargs)
        if str(path).endswith('data.json'):
            seen.append(path)
            if len(seen) == 3:  # after the copy: the writer appended meanwhile
                return SimpleNamespace(st_mode=info.st_mode, st_ino=info.st_ino, st_size=info.st_size + 1,
                                       st_mtime_ns=info.st_mtime_ns)
        return info

    monkeypatch.setattr(retained_temp.os, 'lstat', racing)
    record = retained_temp.retain(basetemp, tmp_path / 'raw/basetemp/phase')
    assert record['status'] == 'INCOMPLETE'
    assert 'source_changed_during_copy' in record['errors']['test_a0/nested/data.json']


def test_a_copy_that_does_not_match_its_inventory_is_incomplete(tmp_path, temp_root, monkeypatch):
    basetemp = owned_scratch(tmp_path)
    real = retained_temp.Path.read_bytes

    def corrupted(self):
        data = real(self)
        return b'tampered' if 'raw/basetemp' in str(self) and self.name == 'data.json' else data

    monkeypatch.setattr(retained_temp.Path, 'read_bytes', corrupted)
    record = retained_temp.retain(basetemp, tmp_path / 'raw/basetemp/phase')
    assert record['status'] == 'INCOMPLETE'
    assert 'copy_inventory_mismatch' in record['errors']['test_a0/nested/data.json']


def test_an_unused_basetemp_is_recorded_not_invented(tmp_path, temp_root):
    record = retained_temp.retain(retained_temp.fresh_basetemp('unused'), tmp_path / 'raw/basetemp/phase')
    assert record['status'] == 'NOT_CREATED' and not record['files']


def test_a_root_this_invocation_did_not_create_is_never_copied(tmp_path, temp_root):
    foreign = tmp_path / 'zab-foreign/b'
    foreign.mkdir(parents=True)
    (foreign / 'secret.txt').write_text('not ours')
    record = retained_temp.retain(foreign, tmp_path / 'raw/basetemp/phase')
    assert record['status'] == 'NOT_OWNED' and not record['files']
    assert not (tmp_path / 'raw/basetemp/phase/scratch').exists()


@pytest.mark.parametrize('change', ['root_replaced', 'owner_record_replaced'])
def test_a_replaced_root_or_owner_record_is_not_owned(tmp_path, temp_root, change):
    basetemp = retained_temp.fresh_basetemp('replaced')
    root = basetemp.parent
    owner = (root / 'OWNER.json').read_text()
    if change == 'root_replaced':
        root.rename(tmp_path / 'moved-away')
        root.mkdir(mode=0o700)
        (root / 'OWNER.json').write_text(owner)  # identical record, different directory
    else:
        (root / 'OWNER.json').write_text(owner.replace(json.loads(owner)['invocation'], '0' * 32))
    basetemp.mkdir()
    record = retained_temp.retain(basetemp, tmp_path / 'raw/basetemp/phase')
    assert record['status'] == 'NOT_OWNED' and record['errors'] == {'.': change}


def test_retention_never_overwrites_an_earlier_copy(tmp_path, temp_root):
    destination = tmp_path / 'raw/basetemp/phase'
    basetemp = retained_temp.fresh_basetemp('twice')
    retained_temp.retain(basetemp, destination)
    with pytest.raises(FileExistsError):
        retained_temp.retain(basetemp, destination)
    assert retained_temp.retain_status(basetemp, destination) == 'FAILED'
    assert 'FAILED' in retained_temp.FAILED_RETENTION and 'NOT_CREATED' not in retained_temp.FAILED_RETENTION
