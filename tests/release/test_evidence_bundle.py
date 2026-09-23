"""tools/evidence_bundle.py: manifests never list themselves; archives carry a detached checksum; every archive
member sits under the one root that holds the manifest; duplicates and symlinks are refused."""
import hashlib
import io
import os
from pathlib import Path
import sys
import tarfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import evidence_bundle as bundle


def make(tmp_path):
    directory = tmp_path / 'delivery'
    (directory / 'patches').mkdir(parents=True)
    (directory / 'README.md').write_text('readme\n')
    (directory / 'patches/0001.patch').write_bytes(b'patch bytes')
    return directory


def test_manifest_lists_every_file_except_itself_and_the_archive_verifies(tmp_path):
    directory = make(tmp_path)
    result = bundle.build(directory)
    manifest = (directory / 'SHA256SUMS').read_text().splitlines()
    assert sorted(line.split('  ')[1] for line in manifest) == ['README.md', 'patches/0001.patch']
    assert 'SHA256SUMS' not in (directory / 'SHA256SUMS').read_text()
    assert manifest[0].split('  ')[0] == hashlib.sha256(b'readme\n').hexdigest()
    archive = Path(result['archive'])
    assert archive.name == 'delivery.tar.gz' and result['files'] == 2
    assert Path(result['detached']).read_text() == result['sha256'] + '  delivery.tar.gz\n'
    assert bundle.verify_directory(directory) and bundle.verify_archive(archive)['files'] == 2
    with tarfile.open(archive) as tar:
        assert sorted(m.name for m in tar.getmembers() if m.isfile()) == ['delivery/README.md', 'delivery/SHA256SUMS', 'delivery/patches/0001.patch']


def test_a_manifest_written_the_old_way_fails_verification_on_its_self_entry(tmp_path):
    """The delivered archives were built with `find | xargs sha256sum > SHA256SUMS`, which lists the
    empty manifest itself; that manifest must be refused, not accepted."""
    directory = make(tmp_path)
    manifest = directory / 'SHA256SUMS'
    manifest.write_text('')
    lines = [f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(directory).as_posix()}\n'
             for path in sorted(directory.rglob('*')) if path.is_file()]
    manifest.write_text(''.join(lines))
    with pytest.raises(bundle.BundleError, match='lists itself'):
        bundle.verify_directory(directory)


@pytest.mark.parametrize('problem', ['modified', 'unlisted', 'missing', 'symlink', 'symlinked-directory', 'duplicate'])
def test_directory_verification_fails_closed(tmp_path, problem):
    directory = make(tmp_path)
    bundle.build(directory)
    if problem == 'modified':
        (directory / 'README.md').write_text('changed\n')
    elif problem == 'unlisted':
        (directory / 'extra.txt').write_text('x')
    elif problem == 'missing':
        (directory / 'patches/0001.patch').unlink()
    elif problem == 'symlink':
        os.symlink('/etc/hostname', directory / 'link')
    elif problem == 'symlinked-directory':
        (tmp_path / 'elsewhere').mkdir()
        (tmp_path / 'elsewhere/secret.txt').write_text('s')
        os.symlink(tmp_path / 'elsewhere', directory / 'linked')
    elif problem == 'duplicate':
        manifest = directory / 'SHA256SUMS'
        manifest.write_text(manifest.read_text() + manifest.read_text().splitlines()[0] + '\n')
    with pytest.raises(bundle.BundleError):
        bundle.verify_directory(directory)


def test_build_refuses_a_symlinked_directory_instead_of_omitting_it(tmp_path):
    """A symlinked directory was silently left out of the manifest and the archive; it is refused like a symlinked file."""
    directory = make(tmp_path)
    (tmp_path / 'elsewhere').mkdir()
    (tmp_path / 'elsewhere/secret.txt').write_text('s')
    os.symlink(tmp_path / 'elsewhere', directory / 'linked')
    with pytest.raises(bundle.BundleError, match='symlink refused: linked'):
        bundle.build(directory)
    assert not (directory / 'SHA256SUMS').exists() and not (tmp_path / 'delivery.tar.gz').exists()


def crafted(path, members):
    """A gzip tar with the given (name, bytes) members, sealed with a matching detached checksum."""
    with tarfile.open(path, 'w:gz') as tar:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    value = hashlib.sha256(path.read_bytes()).hexdigest()
    Path(str(path) + '.sha256').write_text(f'{value}  {path.name}\n')
    return path


def line(data, name):
    return (hashlib.sha256(data).hexdigest() + '  ' + name + '\n').encode()


BODY = b'claimed evidence\n'


@pytest.mark.parametrize('case,message', [
    ('outside-root', 'outside the root bundle'),
    ('second-root', 'outside the root bundle'),
    ('duplicate-entry', 'duplicate manifest entry'),
    ('duplicate-member', 'duplicate archive member'),
    ('traversing-entry', 'malformed manifest line'),
    ('absolute-entry', 'malformed manifest line'),
    ('no-root', 'exactly one top-level manifest'),
])
def test_archive_verification_requires_the_exact_root_prefix_and_unique_entries(tmp_path, case, message):
    """Reviewer cases: a file beside the root satisfied the manifest, and a duplicate manifest entry was accepted
    inside an archive although the same manifest is refused on disk."""
    manifest = line(BODY, 'a.txt')
    members = {
        'outside-root': [('bundle/SHA256SUMS', manifest), ('xundle/a.txt', BODY)],
        'second-root': [('bundle/SHA256SUMS', manifest), ('bundle/a.txt', BODY), ('other/x', b'y')],
        'duplicate-entry': [('bundle/SHA256SUMS', manifest + line(b'x', 'a.txt')), ('bundle/a.txt', b'x')],
        'duplicate-member': [('bundle/SHA256SUMS', manifest), ('bundle/a.txt', b'x'), ('bundle/a.txt', BODY)],
        'traversing-entry': [('bundle/SHA256SUMS', line(BODY, '../a.txt')), ('bundle/a.txt', BODY)],
        'absolute-entry': [('bundle/SHA256SUMS', line(BODY, '/a.txt')), ('bundle/a.txt', BODY)],
        'no-root': [('SHA256SUMS', manifest), ('a.txt', BODY)],
    }[case]
    archive = crafted(tmp_path / 'crafted.tar.gz', members)
    with pytest.raises(bundle.BundleError, match=message):
        bundle.verify_archive(archive)
    assert bundle.main(['verify', str(archive)]) == 1


def test_a_crafted_archive_with_the_right_root_and_bytes_still_verifies(tmp_path):
    archive = crafted(tmp_path / 'good.tar.gz', [('bundle/SHA256SUMS', line(BODY, 'a.txt') + line(b'p', 'patches/1.patch')),
                                                 ('bundle/a.txt', BODY), ('bundle/patches/1.patch', b'p')])
    assert bundle.verify_archive(archive)['files'] == 2


def test_archive_verification_fails_on_tampering_or_a_wrong_detached_checksum(tmp_path):
    directory = make(tmp_path)
    result = bundle.build(directory)
    archive = Path(result['archive'])
    detached = Path(result['detached'])
    detached.write_text('0' * 64 + '  delivery.tar.gz\n')
    with pytest.raises(bundle.BundleError, match='digest mismatch'):
        bundle.verify_archive(archive)
    detached.write_text(result['sha256'] + '  other.tar.gz\n')
    with pytest.raises(bundle.BundleError, match='another file'):
        bundle.verify_archive(archive)
    detached.unlink()
    with pytest.raises(bundle.BundleError, match='detached checksum missing'):
        bundle.verify_archive(archive)


def test_cli_builds_and_verifies(tmp_path, capsys):
    directory = make(tmp_path)
    assert bundle.main(['build', str(directory)]) == 0
    assert bundle.main(['verify', str(directory)]) == 0
    assert bundle.main(['verify', str(tmp_path / 'delivery.tar.gz')]) == 0
    (directory / 'README.md').write_text('tampered')
    assert bundle.main(['verify', str(directory)]) == 1
    assert 'digest mismatch' in capsys.readouterr().out
