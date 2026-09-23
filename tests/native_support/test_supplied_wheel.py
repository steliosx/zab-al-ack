"""Exact-artifact regressions without Docker, package installation or services."""
import hashlib
from types import SimpleNamespace
import zipfile

import pytest

import supplied_wheel as wheel
import run_deployment_e2e as runner


def artifact(tmp_path, change=None):
    files = {
        'zabalack/__init__.py': b'"""fixture"""\n',
        'zabalack/data.txt': b'package resource\n',
        'zabalack-1.0.dist-info/METADATA': b'Name: zabalack\nVersion: 1.0\n',
        'zabalack-1.0.dist-info/WHEEL': b'Root-Is-Purelib: true\nTag: py3-none-any\n',
        'zabalack-1.0.dist-info/entry_points.txt': b'[console_scripts]\nzabalack = zabalack.cli:main\n',
        'zabalack-1.0.dist-info/RECORD': b'',
    }
    if change:
        change(files)
    path = tmp_path / 'zabalack-1.0-py3-none-any.whl'
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    root = tmp_path / 'checkout'
    for name, data in files.items():
        if name.startswith('zabalack/'):
            target = root / 'src' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    root.mkdir(exist_ok=True)
    (root / 'pyproject.toml').write_text('[project]\nname="zabalack"\nversion="1.0"\n')
    return path, digest, root, files


def test_exact_payload_and_project_are_required(tmp_path):
    path, digest, root, _ = artifact(tmp_path)
    assert wheel.check_source(path, digest, root)[1]['sha256'] == digest
    (root / 'src/zabalack/data.txt').write_bytes(b'changed resource')
    with pytest.raises(ValueError, match='source_mismatch'):
        wheel.check_source(path, digest, root)


@pytest.mark.parametrize('flaw', ['digest', 'traversal', 'version', 'platform', 'extra_payload'])
def test_rejects_wrong_artifact_identity(tmp_path, flaw):
    def change(files):
        if flaw == 'traversal':
            files['../escape'] = b'bad'
        if flaw == 'extra_payload':
            files['other_package/__init__.py'] = b'bad'
        if flaw == 'platform':
            files['zabalack-1.0.dist-info/WHEEL'] = b'Root-Is-Purelib: false\nTag: cp312-linux\n'
    path, digest, root, _ = artifact(tmp_path, change)
    if flaw == 'digest':
        digest = '0' * 64
    if flaw == 'version':
        (root / 'pyproject.toml').write_text('[project]\nname="zabalack"\nversion="2.0"\n')
    with pytest.raises(ValueError):
        wheel.check_source(path, digest, root)


@pytest.mark.parametrize('flaw', ['none', 'resource', 'extra_module', 'entry_target', 'version', 'origin', 'editable',
                                  'dist_info_extra', 'shadow_entry', 'pth_hook', 'sitecustomize'])
def test_installed_inventory_and_metadata_must_match(tmp_path, monkeypatch, flaw):
    path, digest, root, files = artifact(tmp_path)
    installed = tmp_path / 'site-packages'
    for name, data in files.items():
        target = installed / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    if flaw == 'resource':
        (installed / 'zabalack/data.txt').write_bytes(b'changed')
    if flaw == 'extra_module':
        (installed / 'zabalack/unexpected.py').write_bytes(b'bad')
    if flaw == 'entry_target':
        (installed / 'zabalack-1.0.dist-info/entry_points.txt').write_bytes(b'[console_scripts]\nzabalack = other:main\n')
    if flaw == 'dist_info_extra':
        (installed / 'zabalack-1.0.dist-info/EXTRA').write_bytes(b'not from the wheel or pip')
    if flaw == 'shadow_entry':
        (installed / 'zabalack.pth').write_bytes(b'/elsewhere\n')
    if flaw == 'pth_hook':
        (installed / 'zz_hook.pth').write_bytes(b'import os; os.environ["INJECTED"] = "1"\n')
    if flaw == 'sitecustomize':
        (installed / 'sitecustomize.py').write_bytes(b'import os\n')
    distribution = SimpleNamespace(version='2.0' if flaw == 'version' else '1.0',
        locate_file=lambda _: installed,
        read_text=lambda _: '{"dir_info":{"editable":true}}' if flaw == 'editable' else None)
    monkeypatch.setattr(wheel.importlib.metadata, 'distribution', lambda _: distribution)
    module = SimpleNamespace(__file__=str((root if flaw == 'origin' else installed) / 'zabalack/__init__.py'))
    monkeypatch.setattr(wheel.importlib, 'import_module', lambda _: module)
    if flaw == 'none':
        assert wheel.check_installed(path, digest, root)['installed_bytes_verified'] is True
    else:
        with pytest.raises(ValueError):
            wheel.check_installed(path, digest, root)


def test_supplied_context_omits_source_and_preserves_exact_wheel(tmp_path):
    path, digest, _, _ = artifact(tmp_path)
    data, _, identity = wheel.wheel_bytes(path, digest)
    destination = tmp_path / 'context'
    runner.staged_context(destination, (data, identity))
    assert not (destination / 'src').exists()
    assert (destination / 'qualified-wheel' / path.name).read_bytes() == data
    assert (destination / 'lab/supplied_wheel.py').is_file()
    spec = runner.compose_spec(tmp_path, destination, 'zabalack-e2e-0123456789ab', supplied_wheel=True)
    assert spec['services']['runner']['build']['dockerfile'] == 'lab/deployment_e2e_wheel.Dockerfile'


@pytest.mark.parametrize('malformed', [False, True])
def test_preflight_invalidates_old_success_before_any_docker(tmp_path, monkeypatch, malformed):
    import json
    path, digest, root, _ = artifact(tmp_path)
    if malformed:
        path.write_bytes(b'not a zip')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    else:
        digest = '0' * 64
    (root / 'evidence').mkdir()
    report = root / 'evidence/deployment-e2e-run.json'
    report.write_text('{"status":"PASS"}')
    monkeypatch.setattr(runner, 'ROOT', root)
    monkeypatch.setattr(runner, 'local_docker', lambda _: pytest.fail('must not contact Docker'))
    assert runner.main(['--isolated-lab', '--wheel', str(path), '--wheel-sha256', digest]) == 2
    assert json.loads(report.read_text())['status'] == 'NOT_RUN'


def test_plan_has_no_wheel_file_or_docker_access(monkeypatch):
    monkeypatch.setattr(runner, 'check_source', lambda *_: pytest.fail('plan read artifact'))
    monkeypatch.setattr(runner, 'local_docker', lambda _: pytest.fail('plan contacted Docker'))
    assert runner.main(['--isolated-lab', '--plan', '--wheel', '/absent.whl', '--wheel-sha256', 'a' * 64]) == 0
