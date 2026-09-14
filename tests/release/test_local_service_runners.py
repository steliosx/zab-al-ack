"""Offline checks of the non-Docker service runners (R14). No service is started."""
import json
import os
from pathlib import Path
import stat
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import native_ci
import native_local
import run_deployment_e2e_local as local_e2e


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
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('zabalack/__init__.py', payload)
        archive.writestr(f'zabalack-{version}.dist-info/METADATA', f'Metadata-Version: 2.1\nName: zabalack\nVersion: {version}\n')
    return path


def fake_runner(tmp_path: Path, info: dict) -> Path:
    python = tmp_path / 'python'
    python.write_text('#!/bin/sh\nprintf %s ' + json.dumps(json.dumps(info)) + '\n')
    python.chmod(python.stat().st_mode | stat.S_IXUSR)
    return python


def test_local_e2e_runner_rejects_an_editable_or_checkout_import(tmp_path, monkeypatch):
    monkeypatch.setattr(local_e2e, 'ROOT', tmp_path / 'checkout')
    (tmp_path / 'checkout/src/zabalack').mkdir(parents=True)
    (tmp_path / 'checkout/src/zabalack/__init__.py').write_text('')
    wheel = synthetic_wheel(tmp_path / 'zabalack-0.1.0a0-py3-none-any.whl')
    checkout = {'file': str(tmp_path / 'checkout/src/zabalack/__init__.py'), 'version': '0.1.0a0',
                'location': str(tmp_path / 'checkout/src'), 'direct_url': None}
    with pytest.raises(RuntimeError, match='runner_imports_checkout_not_wheel'):
        local_e2e.check_runner(fake_runner(tmp_path, checkout), wheel)
    site = tmp_path / 'site-packages/zabalack'
    site.mkdir(parents=True)
    (site / '__init__.py').write_bytes(b'"""synthetic"""\n')
    editable = {'file': str(site / '__init__.py'), 'version': '0.1.0a0', 'location': str(site.parent),
                'direct_url': json.dumps({'url': 'file:///elsewhere', 'dir_info': {'editable': True}})}
    with pytest.raises(RuntimeError, match='runner_install_is_editable'):
        local_e2e.check_runner(fake_runner(tmp_path, editable), wheel)


def test_local_e2e_runner_requires_installed_bytes_to_equal_the_wheel(tmp_path, monkeypatch):
    monkeypatch.setattr(local_e2e, 'ROOT', tmp_path / 'checkout')
    site = tmp_path / 'site-packages'
    (site / 'zabalack').mkdir(parents=True)
    (site / 'zabalack/__init__.py').write_bytes(b'"""different"""\n')
    info = {'file': str(site / 'zabalack/__init__.py'), 'version': '0.1.0a0', 'location': str(site),
            'direct_url': json.dumps({'url': 'file:///wheel', 'archive_info': {}})}
    wheel = synthetic_wheel(tmp_path / 'zabalack-0.1.0a0-py3-none-any.whl')
    with pytest.raises(RuntimeError, match='installed_bytes_differ_from_wheel'):
        local_e2e.check_runner(fake_runner(tmp_path, info), wheel)
    (site / 'zabalack/__init__.py').write_bytes(b'"""synthetic"""\n')
    with pytest.raises(RuntimeError, match='installed_distribution_metadata_missing'):
        local_e2e.check_runner(fake_runner(tmp_path, info), wheel)
    dist_info = site / 'zabalack-0.1.0a0.dist-info'
    dist_info.mkdir()
    (dist_info / 'METADATA').write_text('Metadata-Version: 2.1\nName: zabalack\nVersion: 0.1.0a0\n')
    (dist_info / 'entry_points.txt').write_text('[alerta.plugins]\nzabalack_zabbix_ack = zabalack.plugins:Ack\n')
    with pytest.raises(RuntimeError, match='installed_entry_points_missing'):
        local_e2e.check_runner(fake_runner(tmp_path, info), wheel)
    (dist_info / 'entry_points.txt').write_text('[alerta.plugins]\nzabalack_zabbix_ack = zabalack.plugins:Ack\n'
                                               'zabalack_zabbix_normalise = zabalack.plugins:Normalise\n')
    runner = local_e2e.check_runner(fake_runner(tmp_path, info), wheel)
    assert runner['version'] == '0.1.0a0' and len(runner['wheel_sha256']) == 64


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
