"""tools/evidence_bundle.py: manifests never list themselves; archives carry a detached checksum."""
import hashlib
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


@pytest.mark.parametrize('problem', ['modified', 'unlisted', 'missing', 'symlink', 'duplicate'])
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
    elif problem == 'duplicate':
        manifest = directory / 'SHA256SUMS'
        manifest.write_text(manifest.read_text() + manifest.read_text().splitlines()[0] + '\n')
    with pytest.raises(bundle.BundleError):
        bundle.verify_directory(directory)


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
