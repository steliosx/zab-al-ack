"""The real setuptools sdist carries exactly the public source-archive files (ZB-B1-REL-01).

verify_release requires the normalised sdist to hold every file of the source ZIP (except .gitignore) with identical
bytes, plus only setuptools' generated metadata. The ZIP is built from public_files(); the sdist from MANIFEST.in.
At 494768d MANIFEST.in selected only *.py, *.yml, *.service and *.timer under deploy/, so the lab-trust installer,
its checksums, sudoers and tmpfiles rules, and the ingress template were missing from every sdist, and
verify_release failed. The other release tests build a synthetic sdist from public_files() and cannot see this, so
this test builds a real one from a copy of the public files, with the same backend and flags as build_release.
"""
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from public_audit import public_files

ROOT = Path(__file__).resolve().parents[2]
# The nine tracked deploy files the 494768d sdist omitted.
DEPLOY_FILES = (
    'deploy/ingress/alerta-ingest.nginx.conf.template',
    'deploy/lab-trust/SHA256SUMS',
    'deploy/lab-trust/check-ca.sh',
    'deploy/lab-trust/install.sh',
    'deploy/lab-trust/snapshot-check.sh',
    'deploy/lab-trust/uninstall.sh',
    'deploy/lab-trust/verify.sh',
    'deploy/lab-trust/zabalack-lab-zabbix.sudoers',
    'deploy/lab-trust/zabalack-lab-zabbix.tmpfiles',
)
# setuptools' generated members, as verify_release allows them.
GENERATED = {'PKG-INFO', 'src/zabalack.egg-info/PKG-INFO', 'src/zabalack.egg-info/SOURCES.txt',
             'src/zabalack.egg-info/dependency_links.txt', 'src/zabalack.egg-info/entry_points.txt',
             'src/zabalack.egg-info/requires.txt', 'src/zabalack.egg-info/top_level.txt'}


@pytest.fixture(scope='module')
def archives(tmp_path_factory):
    """(source, sdist): {relative name: bytes} for the public files and for a real sdist built from them."""
    tree = tmp_path_factory.mktemp('public-tree')
    source = {}
    for path in public_files(ROOT):
        relative = path.relative_to(ROOT).as_posix()
        source[relative] = path.read_bytes()
        target = tree / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    out = tmp_path_factory.mktemp('sdist')
    env = dict(os.environ, SOURCE_DATE_EPOCH='1789171200', PYTHONHASHSEED='0', PIP_NO_INDEX='1',
               PIP_DISABLE_PIP_VERSION_CHECK='1')
    subprocess.run([sys.executable, '-m', 'build', '--no-isolation', '--sdist', '--outdir', str(out)], cwd=tree, env=env,
                   check=True, capture_output=True, timeout=300)
    [built] = list(out.glob('zabalack-*.tar.gz'))
    with tarfile.open(built) as bundle:
        roots = {member.name.split('/', 1)[0] for member in bundle.getmembers()}
        assert len(roots) == 1
        prefix = roots.pop() + '/'
        sdist = {member.name.removeprefix(prefix): bundle.extractfile(member).read()
                 for member in bundle.getmembers() if member.isfile()}
    return source, sdist


def test_the_nine_deploy_files_are_in_the_sdist_byte_for_byte(archives):
    source, sdist = archives
    for name in DEPLOY_FILES:
        assert name in source, f'{name} is not a public file'
        assert sdist.get(name) == source[name], f'{name} missing or different in the sdist'


def test_the_sdist_holds_exactly_the_public_files_plus_generated_metadata(archives):
    """verify_release's rule: every source-archive file with identical bytes, and nothing else but generated metadata."""
    source, sdist = archives
    expected = set(source) - {'.gitignore'}
    generated = set(GENERATED)
    if 'setup.cfg' not in source:
        generated.add('setup.cfg')
        assert sdist.get('setup.cfg') == b'[egg_info]\ntag_build = \ntag_date = 0\n\n'
    assert sorted(expected - set(sdist)) == [], 'public files missing from the sdist'
    assert sorted(set(sdist) - expected - generated) == [], 'unexpected content in the sdist'
    assert [name for name in expected if sdist[name] != source[name]] == []
