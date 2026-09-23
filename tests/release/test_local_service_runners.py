"""Offline checks of the non-Docker service runners (R14). No service is started."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import venv
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import native_ci
import native_local
import run_deployment_e2e_local as local_e2e
from cancellation import Cancelled

ROOT = Path(__file__).resolve().parents[2]


PASS_XML = '<testsuites><testsuite><testcase classname="synthetic" name="test_T38_check"/></testsuite></testsuites>'


def test_local_native_runner_uses_the_compose_runner_phase_table():
    assert native_local.PHASES is native_ci.PHASES
    assert list(native_local.PHASES) == ['postgres', 'delivery_faults', 'worker', 'alerta', 'performance']


def test_local_native_runner_refuses_without_declared_service_binaries(monkeypatch, tmp_path):
    monkeypatch.delenv('ACK_LAB_PG_BIN', raising=False)
    monkeypatch.delenv('ACK_LAB_PGBOUNCER_BIN', raising=False)
    with pytest.raises(RuntimeError, match='NOT_RUN'):
        native_local.binaries()
    (tmp_path / 'psql').write_text('')
    monkeypatch.setenv('ACK_LAB_PG_BIN', str(tmp_path))
    with pytest.raises(RuntimeError, match='ACK_LAB_PGBOUNCER_BIN'):
        native_local.binaries()


def test_local_native_unavailability_invalidates_previous_evidence(monkeypatch, tmp_path):
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    for phase in native_ci.PHASES:
        (evidence / ('native-' + phase + '.xml')).write_text(PASS_XML)
    (evidence / 'native-run.json').write_text(json.dumps({'status': 'PASS', 'phases': [{'status': 'PASS'}]}))
    monkeypatch.setattr(native_local, 'ROOT', tmp_path)
    monkeypatch.delenv('ACK_LAB_PG_BIN', raising=False)
    monkeypatch.delenv('ACK_LAB_PGBOUNCER_BIN', raising=False)
    monkeypatch.setattr(native_local, 'code_digests', lambda root: {'source': 'x', 'harness': 'y'})
    assert native_local.main(['--isolated-lab']) == 2
    record = json.loads((evidence / 'native-run.json').read_text())
    assert record['status'] == 'NOT_RUN' and record['phases'] == [] and record['not_docker'] is True
    assert not list(evidence.glob('native-*.xml'))


def test_local_native_plan_starts_nothing(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(native_local, 'ROOT', tmp_path)
    assert native_local.main(['--isolated-lab', '--plan']) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan['phases'] == native_ci.PHASES and plan['fresh_cluster_per_phase'] is True
    assert not (tmp_path / 'evidence').exists()


def synthetic_wheel(path: Path, version='0.1.0a0', payload=b'"""synthetic"""\n'):
    """The minimal wheel the runner-level tests use with check_runner stubbed out."""
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('zabalack/__init__.py', payload)
        archive.writestr(f'zabalack-{version}.dist-info/METADATA', f'Metadata-Version: 2.1\nName: zabalack\nVersion: {version}\n')
    return path


def candidate_wheel(path: Path, version='0.1.0a0', payload=b'"""synthetic"""\n', entry_points=None):
    """A wheel shaped like the release builder's: package, dist-info metadata, WHEEL tags, entry points, RECORD."""
    dist = f'zabalack-{version}.dist-info'
    entry_points = entry_points or ('[alerta.plugins]\nzabalack_zabbix_ack = zabalack.plugins:Ack\n'
                                    'zabalack_zabbix_normalise = zabalack.plugins:Normalise\n')
    members = {
        'zabalack/__init__.py': payload,
        'zabalack/plugins.py': b'class Ack:\n    pass\n\n\nclass Normalise:\n    pass\n',
        f'{dist}/METADATA': f'Metadata-Version: 2.1\nName: zabalack\nVersion: {version}\n'.encode(),
        f'{dist}/WHEEL': b'Wheel-Version: 1.0\nGenerator: synthetic\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
        f'{dist}/entry_points.txt': entry_points.encode(),
        f'{dist}/top_level.txt': b'zabalack\n',
    }
    members[f'{dist}/RECORD'] = ''.join(f'{name},sha256=synthetic,{len(data)}\n' for name, data in members.items()).encode()
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path, members


def runner_environment(tmp_path: Path):
    """A real virtual environment (no pip) whose site-packages the tests populate by hand."""
    env = tmp_path / 'runner-venv'
    venv.create(env, with_pip=False)
    site = next(env.glob('lib/python*/site-packages'))
    return env / 'bin/python', site


def install(site: Path, members: dict, *, editable=False):
    """What pip leaves behind after `pip install --no-deps wheel`: the members, plus INSTALLER, REQUESTED,
    direct_url.json and a rewritten RECORD."""
    for name, data in members.items():
        target = site / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    dist = next(name.split('/')[0] for name in members if name.endswith('/METADATA'))
    (site / dist / 'INSTALLER').write_text('pip\n')
    (site / dist / 'REQUESTED').write_text('')
    origin = {'url': 'file:///dist/wheel', 'dir_info': {'editable': True}} if editable else {'url': 'file:///dist/wheel', 'archive_info': {}}
    (site / dist / 'direct_url.json').write_text(json.dumps(origin))
    (site / dist / 'RECORD').write_text('../../../bin/zabalack,sha256=launcher,242\n'
                                        + ''.join(f'{name},sha256=synthetic,{len(data)}\n' for name, data in members.items()))
    return dist


CASES = {
    'installed': None,
    'extra-package-file': 'installed_package_inventory_mismatch',
    'modified-package-bytes': 'installed_wheel_bytes_mismatch',
    'extra-dist-info-file': 'installed_dist_info_inventory_mismatch',
    'second-entry-named-after-the-project': 'unexpected_installed_entries',
    'unowned-pth-hook': 'unowned_startup_hook',
    'unowned-sitecustomize': 'unowned_startup_hook',
    'owned-pth-hook': None,
    'editable-install': 'editable_install',
    'wrong-digest': 'wheel_digest_mismatch',
    'missing-entry-point': 'installed_entry_points_missing',
    'installed-from-checkout': 'installed_from_checkout',
    'nothing-installed': None,
}


@pytest.mark.parametrize('case', list(CASES))
def test_local_e2e_runner_verifies_the_whole_installation_inside_the_runner_interpreter(tmp_path, monkeypatch, case):
    """R11: the reviewer showed an extra zabalack/backdoor.py and a zz_hook.pth accepted with
    installed_bytes_verified; every deviation from the wheel is now refused by tools/supplied_wheel.check_installed
    running in the runner environment, and the digest is the supplied one."""
    monkeypatch.setattr(local_e2e, 'ROOT', ROOT if case != 'installed-from-checkout' else tmp_path)
    if case == 'installed-from-checkout':
        # The checkout under test contains the runner environment; the verifier itself is copied into it.
        (tmp_path / 'tools').mkdir()
        shutil.copy(ROOT / 'tools/supplied_wheel.py', tmp_path / 'tools/supplied_wheel.py')
    entry_points = '[alerta.plugins]\nzabalack_zabbix_ack = zabalack.plugins:Ack\n' if case == 'missing-entry-point' else None
    wheel, members = candidate_wheel(tmp_path / 'zabalack-0.1.0a0-py3-none-any.whl', entry_points=entry_points)
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    python, site = runner_environment(tmp_path)
    if case != 'nothing-installed':
        dist = install(site, members, editable=case == 'editable-install')
    if case == 'extra-package-file':
        (site / 'zabalack/backdoor.py').write_text('# not in the wheel\n')
    elif case == 'modified-package-bytes':
        (site / 'zabalack/__init__.py').write_bytes(b'"""changed"""\n')
    elif case == 'extra-dist-info-file':
        (site / dist / 'EXTRA').write_text('x')
    elif case == 'second-entry-named-after-the-project':
        (site / 'zabalack_shadow').mkdir()
        (site / 'zabalack_shadow/__init__.py').write_text('')
    elif case == 'unowned-pth-hook':
        (site / 'zz_hook.pth').write_text('import os; os.environ["INJECTED"] = "1"\n')
    elif case == 'unowned-sitecustomize':
        (site / 'sitecustomize.py').write_text('import os\n')
    elif case == 'owned-pth-hook':
        (site / 'distutils-precedence.pth').write_text('import _distutils_hack\n')
        (site / 'setuptools-80.0.0.dist-info').mkdir()
        (site / 'setuptools-80.0.0.dist-info/RECORD').write_text('distutils-precedence.pth,sha256=synthetic,26\n'
                                                                 'setuptools-80.0.0.dist-info/METADATA,sha256=synthetic,1\n')
        (site / 'setuptools-80.0.0.dist-info/METADATA').write_text('Name: setuptools\nVersion: 80.0.0\n')
    expected = 'f' * 64 if case == 'wrong-digest' else digest
    if case == 'nothing-installed':
        with pytest.raises(RuntimeError, match='runner_installed_wheel_check_failed'):
            local_e2e.check_runner(python, wheel, expected)
        return
    label = CASES[case]
    if label is None:
        runner = local_e2e.check_runner(python, wheel, expected)
        assert runner['version'] == '0.1.0a0' and runner['wheel_sha256'] == digest and runner['installed_bytes_verified'] is True
        assert runner['startup_hooks'] == (['distutils-precedence.pth'] if case == 'owned-pth-hook' else [])
        assert 'location' not in runner and str(site) not in json.dumps(runner)
    else:
        with pytest.raises(RuntimeError, match='runner_installed_wheel_check_failed: ' + label + '$'):
            local_e2e.check_runner(python, wheel, expected)


def test_local_e2e_runner_check_needs_a_digest_and_an_executable_interpreter(tmp_path, monkeypatch):
    monkeypatch.setattr(local_e2e, 'ROOT', ROOT)
    wheel, _ = candidate_wheel(tmp_path / 'zabalack-0.1.0a0-py3-none-any.whl')
    with pytest.raises(RuntimeError, match='runner_python_missing'):
        local_e2e.check_runner(tmp_path / 'absent', wheel, 'a' * 64)
    python, _ = runner_environment(tmp_path)
    with pytest.raises(RuntimeError, match='invalid_wheel_digest'):
        local_e2e.check_runner(python, wheel, 'not-a-digest')


@pytest.mark.parametrize('shape', ['missing', 'extra', 'blank', 'not_object'])
def test_local_e2e_runner_requires_exact_service_identities(tmp_path, shape):
    value = {name: 'synthetic ' + name for name in local_e2e.IMAGE_COMPONENTS}
    if shape == 'missing':
        value.pop('zabbix-web')
    elif shape == 'extra':
        value['runner'] = 'must be generated, not supplied'
    elif shape == 'blank':
        value['postgres'] = '   '
    else:
        value = [value]
    path = tmp_path / 'services.json'
    path.write_text(json.dumps(value))
    with pytest.raises(RuntimeError, match='services_file_shape'):
        local_e2e.services(path)


def test_local_e2e_runner_records_not_run_without_the_host_layout(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(local_e2e, 'ROOT', tmp_path)
    monkeypatch.setattr(local_e2e, 'LINK', tmp_path / 'missing-link')
    monkeypatch.setattr(local_e2e, 'code_digests', lambda root: {'source': 'x', 'harness': 'y'})
    services = tmp_path / 'services.json'
    services.write_text(json.dumps({name: 'synthetic ' + name for name in local_e2e.IMAGE_COMPONENTS}))
    wheel = synthetic_wheel(tmp_path / 'zabalack-0.1.0a0-py3-none-any.whl')
    (tmp_path / 'evidence').mkdir()
    (tmp_path / 'evidence/deployment-e2e-run.json').write_text(json.dumps({'status': 'PASS', 'counts': {'passed': 4}}))
    code = local_e2e.main(['--isolated-lab', '--runner-python', os.fspath(tmp_path / 'absent'), '--wheel', str(wheel),
                           '--services-file', str(services)])
    assert code == 2
    record = json.loads((tmp_path / 'evidence/deployment-e2e-run.json').read_text())
    assert record['status'] == 'NOT_RUN' and record['reason'].startswith('lab_link_missing')
    assert 'counts' not in record and 'checks' not in record and 'code_digests' not in record
    assert json.loads(capsys.readouterr().out)['status'] == 'NOT_RUN'


def test_local_e2e_runner_records_an_interrupted_execution_and_exits_130(tmp_path, monkeypatch, capsys):
    """R8: a cancellation during pytest ends with FAIL lab_interrupted, the ports checked, the record written."""
    import signal as signal_module
    monkeypatch.setattr(local_e2e, 'ROOT', tmp_path)
    monkeypatch.setattr(local_e2e, 'code_digests', lambda root: {'source': 'x', 'harness': 'y'})
    (tmp_path / 'evidence').mkdir()
    services = tmp_path / 'services.json'
    services.write_text(json.dumps({name: 'synthetic ' + name for name in local_e2e.IMAGE_COMPONENTS}))
    wheel = synthetic_wheel(tmp_path / 'zabalack-0.1.0a0-py3-none-any.whl')
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    monkeypatch.setattr(local_e2e, 'PRIVATE', private)
    monkeypatch.setattr(local_e2e, 'check_host_layout', lambda: None)
    monkeypatch.setattr(local_e2e, 'check_runner', lambda python, path, expected: {'python': str(python), 'version': '0.1.0a0', 'wheel_sha256': 'e' * 64})
    monkeypatch.setattr(local_e2e, 'bootstrap', lambda project: None)
    monkeypatch.setattr(local_e2e, 'clean_private', lambda: None)
    monkeypatch.setattr(local_e2e, 'lab_ports_free', lambda: True)
    monkeypatch.setattr(local_e2e, 'run_child', lambda command, **kwargs: (_ for _ in ()).throw(Cancelled(signal_module.SIGTERM)))
    code = local_e2e.main(['--isolated-lab', '--runner-python', os.fspath(tmp_path / 'python'), '--wheel', str(wheel), '--services-file', str(services)])
    record = json.loads((tmp_path / 'evidence/deployment-e2e-run.json').read_text())
    assert code == 130 and record['status'] == 'FAIL' and record['reason'] == 'lab_interrupted' and record['interrupted_by'] == 'interrupted (SIGTERM)'
    assert record['shutdown_exit_code'] == 0 and 'finished_at' in record
    assert json.loads(capsys.readouterr().out)['status'] == 'FAIL'


def test_local_e2e_runner_records_a_supplied_wheel_identity_only_after_the_exact_preflight(tmp_path, monkeypatch, capsys):
    """R29: the native path carries the Compose form's supplied-wheel receipt."""
    monkeypatch.setattr(local_e2e, 'ROOT', tmp_path)
    monkeypatch.setattr(local_e2e, 'code_digests', lambda root: {'source': 'x', 'harness': 'y'})
    (tmp_path / 'evidence').mkdir()
    services = tmp_path / 'services.json'
    services.write_text(json.dumps({name: 'synthetic ' + name for name in local_e2e.IMAGE_COMPONENTS}))
    wheel = synthetic_wheel(tmp_path / 'zabalack-0.1.0a0-py3-none-any.whl')
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    # Wrong digest: refused before the host layout or runner is examined.
    monkeypatch.setattr(local_e2e, 'check_host_layout', lambda: pytest.fail('layout must not be checked after a failed preflight'))
    code = local_e2e.main(['--isolated-lab', '--runner-python', os.fspath(tmp_path / 'absent'), '--wheel', str(wheel),
                           '--services-file', str(services), '--wheel-sha256', '0' * 64])
    record = json.loads((tmp_path / 'evidence/deployment-e2e-run.json').read_text())
    assert code == 2 and record['status'] == 'NOT_RUN' and record['reason'] == 'supplied_wheel_preflight_failed'
    assert 'supplied_wheel' not in record and record.get('artifact_mode') is None
    capsys.readouterr()
    # Right digest and checkout payload: the plan names supplied_wheel mode and the receipt carries the identity.
    monkeypatch.setattr(local_e2e, 'check_source', lambda path, expected, root: (b'', {'filename': wheel.name, 'sha256': expected, 'version': '0.1.0a0'}))
    monkeypatch.setattr(local_e2e, 'check_host_layout', lambda: None)
    monkeypatch.setattr(local_e2e, 'check_runner', lambda python, path, expected: {'python': str(python), 'version': '0.1.0a0', 'wheel_sha256': digest})
    code = local_e2e.main(['--isolated-lab', '--runner-python', os.fspath(tmp_path / 'python'), '--wheel', str(wheel),
                           '--services-file', str(services), '--wheel-sha256', digest, '--plan'])
    plan = json.loads(capsys.readouterr().out)
    assert code == 0 and plan['artifact_mode'] == 'supplied_wheel'
    # A runner whose installed wheel differs from the supplied identity is refused.
    monkeypatch.setattr(local_e2e, 'check_runner', lambda python, path, expected: {'python': str(python), 'version': '0.1.0a0', 'wheel_sha256': 'e' * 64})
    code = local_e2e.main(['--isolated-lab', '--runner-python', os.fspath(tmp_path / 'python'), '--wheel', str(wheel),
                           '--services-file', str(services), '--wheel-sha256', digest])
    record = json.loads((tmp_path / 'evidence/deployment-e2e-run.json').read_text())
    assert code == 2 and record['reason'] == 'supplied_wheel_identity_mismatch'
    # The artifact was not exercised: no artifact-mode or wheel metadata may suggest otherwise on a NOT_RUN record.
    assert 'artifact_mode' not in record and 'supplied_wheel' not in record
    monkeypatch.setattr(local_e2e, 'check_host_layout', lambda: (_ for _ in ()).throw(RuntimeError('lab_link_missing')))
    code = local_e2e.main(['--isolated-lab', '--runner-python', os.fspath(tmp_path / 'python'), '--wheel', str(wheel),
                           '--services-file', str(services), '--wheel-sha256', digest])
    record = json.loads((tmp_path / 'evidence/deployment-e2e-run.json').read_text())
    assert code == 2 and record['status'] == 'NOT_RUN' and record['reason'] == 'lab_link_missing'
    assert 'artifact_mode' not in record and 'supplied_wheel' not in record
