"""Owned, never-rotated pytest scratch and its retained copy, using temporary local files only."""
import getpass
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
from types import SimpleNamespace

import pytest

import retained_temp
from retained_temp import OwnedScratch


@pytest.fixture
def temp_root(tmp_path, monkeypatch):
    root = tmp_path / 'system-temp'
    root.mkdir()
    monkeypatch.setattr(retained_temp, 'ROOT_PARENT', str(root))
    return root


def test_an_owned_scratch_binds_invocation_and_root_before_the_child_starts(tmp_path, temp_root):
    first = OwnedScratch('reference-unit-mocked', tmp_path / 'raw/basetemp')
    second = OwnedScratch('reference-unit-mocked', tmp_path / 'raw/basetemp')
    assert first.basetemp.parent != second.basetemp.parent
    for scratch in (first, second):
        # pytest empties an existing basetemp; a fresh root means nothing else is removed.
        assert not scratch.basetemp.exists() and scratch.basetemp.name == 'b'
        root = scratch.basetemp.parent
        assert root.parent == temp_root and root.name.startswith('zab-')
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        owner = json.loads((root / 'OWNER.json').read_text())
        custody = json.loads((scratch.destination / 'custody.json').read_text())
        # The pending custody record alone identifies the root, so a crashed parent is recoverable.
        assert custody == dict(owner, status='PENDING')
        assert (owner['device'], owner['inode']) == (root.stat().st_dev, root.stat().st_ino)
        assert owner['label'] == 'reference-unit-mocked' and owner['pid'] == os.getpid()
        assert stat.S_IMODE(scratch.destination.stat().st_mode) == 0o700
    first_owner = json.loads((first.basetemp.parent / 'OWNER.json').read_text())
    second_owner = json.loads((second.basetemp.parent / 'OWNER.json').read_text())
    assert len(first_owner['invocation']) == 32 and first_owner['invocation'] != second_owner['invocation']


def test_the_default_root_is_persistent_and_short_for_socket_fixtures(tmp_path):
    scratch = OwnedScratch('length', tmp_path / 'raw/basetemp')
    try:
        # Persistent disk where available, not the size-limited tmpfs /tmp.
        assert str(scratch.basetemp.parent.parent) == (retained_temp.ROOT_PARENT or tempfile.gettempdir())
        # Shorter than pytest's own /tmp/pytest-of-<user>/pytest-<n>, for socket fixture paths.
        assert len(str(scratch.basetemp)) < len('/tmp/pytest-of-' + getpass.getuser() + '/pytest-1')
    finally:
        (scratch.basetemp.parent / 'OWNER.json').unlink()
        scratch.basetemp.parent.rmdir()


def populate(tmp_path, scratch, unreadable_name='test_a0/unreadable.env'):
    basetemp = scratch.basetemp
    (basetemp / 'test_a0/nested').mkdir(parents=True)
    (basetemp / 'test_a0/nested/data.json').write_bytes(b'{"x": 1}\n')
    outside = tmp_path / 'outside.txt'
    outside.write_text('must not be copied through the link')
    (basetemp / 'test_a0/link').symlink_to(outside)
    os.mkfifo(basetemp / 'test_a0/pipe')
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(basetemp / 'test_a0/s.sock'))
    closed = basetemp / unreadable_name
    closed.parent.mkdir(parents=True, exist_ok=True)
    closed.write_text('fixture')
    closed.chmod(0)
    return server, closed


needs_modes = pytest.mark.skipif(os.geteuid() == 0, reason='root reads mode-0 files')


@needs_modes
def test_retain_copies_files_and_lists_links_and_special_entries(tmp_path, temp_root):
    scratch = OwnedScratch('synthetic', tmp_path / 'raw/basetemp')
    server, closed = populate(tmp_path, scratch, 'test_unreadable_environment_fi0/runner.env')
    try:
        with scratch:
            pass
    finally:
        server.close()
    record = json.loads((scratch.destination / 'retention.json').read_text())
    assert scratch.status == record['status'] == 'COMPLETE'
    copied = scratch.destination / 'scratch'
    assert (copied / 'test_a0/nested/data.json').read_bytes() == b'{"x": 1}\n'
    assert record['files']['test_a0/nested/data.json']['size'] == 9
    # Links are copied as links; the target is never read through them.
    assert (copied / 'test_a0/link').is_symlink()
    assert os.readlink(copied / 'test_a0/link') == str(tmp_path / 'outside.txt')
    assert set(record['special']) == {'test_a0/pipe', 'test_a0/s.sock'}
    # The suite's deliberately unreadable fixture is listed, not claimed as copied bytes.
    assert list(record['unreadable_expected']) == ['test_unreadable_environment_fi0/runner.env']
    assert record['unreadable'] == {}
    # The original keeps its permissions and stays in place; custody is finalised.
    assert stat.S_IMODE(closed.stat().st_mode) == 0 and scratch.basetemp.is_dir()
    assert json.loads((scratch.destination / 'custody.json').read_text())['status'] == 'COMPLETE'


@needs_modes
def test_an_unexpected_unreadable_file_or_directory_is_incomplete(tmp_path, temp_root):
    scratch = OwnedScratch('synthetic', tmp_path / 'raw/basetemp')
    server, _ = populate(tmp_path, scratch)
    server.close()
    hidden = scratch.basetemp / 'test_b0'
    hidden.mkdir()
    hidden.chmod(0)
    try:
        with scratch:
            pass
    finally:
        hidden.chmod(0o700)
    record = json.loads((scratch.destination / 'retention.json').read_text())
    assert scratch.status == 'INCOMPLETE'
    assert set(record['unreadable']) == {'test_a0/unreadable.env', 'test_b0'} and record['unreadable_expected'] == {}


def readable_scratch(tmp_path):
    scratch = OwnedScratch('synthetic', tmp_path / 'raw/basetemp')
    server, closed = populate(tmp_path, scratch)
    server.close()
    closed.chmod(0o600)
    return scratch


def swap_on_classification(monkeypatch, target_name, replace):
    """Replace an entry right after the walk classifies it: the race a no-follow open must survive."""
    real = os.stat

    def racing(path, *args, **kwargs):
        info = real(path, *args, **kwargs)
        if path == target_name and kwargs.get('dir_fd') is not None:
            replace()
        return info

    monkeypatch.setattr(retained_temp.os, 'stat', racing)


def test_a_file_swapped_for_a_link_after_listing_is_never_followed(tmp_path, temp_root, monkeypatch):
    scratch = readable_scratch(tmp_path)
    data = scratch.basetemp / 'test_a0/nested/data.json'
    sentinel = tmp_path / 'sentinel.txt'
    sentinel.write_text('x' * len(str(sentinel)))  # same length as the link text

    def replace():
        data.unlink()
        data.symlink_to(sentinel)

    swap_on_classification(monkeypatch, 'data.json', replace)
    with scratch:
        pass
    record = json.loads((scratch.destination / 'retention.json').read_text())
    assert scratch.status == 'INCOMPLETE' and 'test_a0/nested/data.json' in record['errors']
    copied = scratch.destination / 'scratch/test_a0/nested/data.json'
    assert not copied.exists()  # no byte from outside the scratch was read


def test_a_directory_swapped_for_a_link_after_listing_is_never_entered(tmp_path, temp_root, monkeypatch):
    scratch = readable_scratch(tmp_path)
    outside = tmp_path / 'outside-dir'
    outside.mkdir()
    (outside / 'private.txt').write_text('outside the scratch')
    nested = scratch.basetemp / 'test_a0/nested'

    def replace():
        nested.rename(tmp_path / 'moved-nested')
        nested.symlink_to(outside)

    swap_on_classification(monkeypatch, 'nested', replace)
    with scratch:
        pass
    record = json.loads((scratch.destination / 'retention.json').read_text())
    assert scratch.status == 'INCOMPLETE' and 'test_a0/nested' in record['errors']
    assert not list((scratch.destination / 'scratch').rglob('private.txt'))


def test_a_file_replaced_by_another_file_is_unstable(tmp_path, temp_root, monkeypatch):
    scratch = readable_scratch(tmp_path)
    data = scratch.basetemp / 'test_a0/nested/data.json'

    def replace():
        data.unlink()
        data.write_bytes(b'different inode\n')

    swap_on_classification(monkeypatch, 'data.json', replace)
    with scratch:
        pass
    record = json.loads((scratch.destination / 'retention.json').read_text())
    assert scratch.status == 'INCOMPLETE'
    assert 'entry_replaced_before_open' in record['errors']['test_a0/nested/data.json']


def test_a_file_changing_during_its_copy_is_incomplete(tmp_path, temp_root, monkeypatch):
    scratch = readable_scratch(tmp_path)
    real = os.fstat
    seen = {}

    def growing(fd):
        info = real(fd)
        if stat.S_ISREG(info.st_mode) and info.st_size == 9:  # data.json, the only 9-byte file
            seen[fd] = seen.get(fd, 0) + 1
            if seen[fd] == 2:  # the post-copy check: a writer appended meanwhile
                return SimpleNamespace(st_mode=info.st_mode, st_size=info.st_size + 1, st_mtime_ns=info.st_mtime_ns)
        return info

    monkeypatch.setattr(retained_temp.os, 'fstat', growing)
    with scratch:
        pass
    record = json.loads((scratch.destination / 'retention.json').read_text())
    assert scratch.status == 'INCOMPLETE'
    assert 'source_changed_during_copy' in record['errors']['test_a0/nested/data.json']


def test_a_copy_that_does_not_match_its_inventory_is_incomplete(tmp_path, temp_root, monkeypatch):
    scratch = readable_scratch(tmp_path)
    real = Path.read_bytes
    monkeypatch.setattr(retained_temp.Path, 'read_bytes',
                        lambda self: b'tampered' if self.name == 'data.json' and 'scratch' in self.parts else real(self))
    with scratch:
        pass
    record = json.loads((scratch.destination / 'retention.json').read_text())
    assert scratch.status == 'INCOMPLETE'
    assert 'copy_inventory_mismatch' in record['errors']['test_a0/nested/data.json']


def test_a_vanished_entry_is_incomplete(tmp_path, temp_root, monkeypatch):
    scratch = readable_scratch(tmp_path)
    data = scratch.basetemp / 'test_a0/nested/data.json'
    swap_on_classification(monkeypatch, 'data.json', data.unlink)
    with scratch:
        pass
    record = json.loads((scratch.destination / 'retention.json').read_text())
    assert scratch.status == 'INCOMPLETE' and list(record['errors']) == ['test_a0/nested/data.json']


def test_an_unused_basetemp_is_recorded_not_invented(tmp_path, temp_root):
    with OwnedScratch('unused', tmp_path / 'raw/basetemp') as scratch:
        pass
    record = json.loads((scratch.destination / 'retention.json').read_text())
    assert scratch.status == record['status'] == 'NOT_CREATED' and not record['files']


@pytest.mark.parametrize('change', ['root_replaced', 'owner_record_replaced', 'owner_record_linked'])
def test_a_replaced_root_or_owner_record_is_never_copied(tmp_path, temp_root, change):
    scratch = OwnedScratch('replaced', tmp_path / 'raw/basetemp')
    root = scratch.basetemp.parent
    owner = (root / 'OWNER.json').read_text()
    if change == 'root_replaced':
        root.rename(tmp_path / 'moved-away')
        root.mkdir(mode=0o700)
        (root / 'OWNER.json').write_text(owner)  # identical record, different directory
    elif change == 'owner_record_replaced':
        (root / 'OWNER.json').write_text(owner.replace(json.loads(owner)['invocation'], '0' * 32))
    else:
        (tmp_path / 'owner-copy.json').write_text(owner)
        (root / 'OWNER.json').unlink()
        (root / 'OWNER.json').symlink_to(tmp_path / 'owner-copy.json')
    scratch.basetemp.mkdir()
    (scratch.basetemp / 'secret.txt').write_text('not to be copied')
    with scratch:
        pass
    record = json.loads((scratch.destination / 'retention.json').read_text())
    assert scratch.status == 'NOT_OWNED' and not record['files']
    assert not (scratch.destination / 'scratch').exists()


def test_the_childs_failure_or_interruption_is_never_swallowed(tmp_path, temp_root):
    for error in (KeyboardInterrupt(), TimeoutError('child timed out')):
        with pytest.raises(type(error)):
            with OwnedScratch('interrupted', tmp_path / 'raw/basetemp') as scratch:
                scratch.basetemp.mkdir()
                (scratch.basetemp / 'partial.txt').write_text('written before the interruption')
                raise error
        custody = json.loads((scratch.destination / 'custody.json').read_text())
        assert scratch.status == custody['status'] == 'COMPLETE'
        assert (scratch.destination / 'scratch/partial.txt').read_text() == 'written before the interruption'


def test_a_pending_record_left_by_a_crashed_parent_is_recoverable(tmp_path, temp_root):
    scratch = OwnedScratch('crashed', tmp_path / 'raw/basetemp')
    scratch.basetemp.mkdir()
    (scratch.basetemp / 'out.txt').write_text('left behind')
    # No __exit__ ran: the record stays PENDING, which never counts as retained.
    assert json.loads((scratch.destination / 'custody.json').read_text())['status'] == 'PENDING'
    assert 'PENDING' in retained_temp.FAILED_RETENTION
    recovered = retained_temp.recover(scratch.destination)
    assert json.loads((recovered / 'custody.json').read_text())['status'] == 'COMPLETE'
    assert (recovered / 'scratch/out.txt').read_text() == 'left behind'
    # The original record is kept exactly as found.
    assert json.loads((scratch.destination / 'custody.json').read_text())['status'] == 'PENDING'
    with pytest.raises(ValueError):
        retained_temp.recover(recovered)


def test_retention_never_overwrites_an_earlier_copy(tmp_path, temp_root):
    with OwnedScratch('twice', tmp_path / 'raw/basetemp') as scratch:
        pass
    with pytest.raises(FileExistsError):
        retained_temp.retain(scratch.destination)
    assert retained_temp.retain_status(scratch.destination) == 'FAILED'
    assert 'NOT_CREATED' not in retained_temp.FAILED_RETENTION
