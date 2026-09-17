"""Contradictory service reports must not qualify an export or archive.

Synthetic evidence only: these tests do not execute Docker or Zabbix.
"""
import copy
import json
import tomllib

import pytest

from tests.release.test_release_provenance import candidate, build_fixture  # noqa: F401
from evidence_utils import code_digests
import public_audit
import verify_release


NAME = 'evidence/deployment-e2e-run.json'
CHECKS = {
    'authenticated_capture', 'cross_customer_denied', 'exactly_one_effect',
    'human_update_recorded', 'ingest_key_local_only', 'media_macro_ingestion',
    'neutral_update_filtered', 'probe_denied_table_read', 'projection',
    'recovery_macro_ingestion', 'repeat_409', 'separate_native_logins',
    'worker_denied_table_update',
}
COMPONENTS = {'postgres', 'zabbix-db', 'zabbix-server', 'zabbix-web'}


@pytest.mark.parametrize('flaw', ['none', 'missing_identity', 'missing_mode', 'digest', 'filename', 'integer_verified', 'not_run'])
def test_supplied_artifact_evidence_is_explicit_and_fail_closed(candidate, flaw):
    from deployment_evidence import validate_report
    root, _ = candidate
    value = passing(root)
    version = tomllib.loads((root / 'pyproject.toml').read_text())['project']['version']
    value.update(artifact_mode='supplied_wheel', supplied_wheel={
        'filename': 'zabalack-' + version + '-py3-none-any.whl', 'version': version,
        'sha256': 'a' * 64, 'installed_bytes_verified': True})
    if flaw == 'missing_identity':
        value.pop('supplied_wheel')
    if flaw == 'missing_mode':
        value.pop('artifact_mode')
    if flaw == 'digest':
        value['supplied_wheel']['sha256'] = 'not-a-hash'
    if flaw == 'filename':
        value['supplied_wheel']['filename'] = 'other.whl'
    if flaw == 'integer_verified':
        value['supplied_wheel']['installed_bytes_verified'] = 1
    if flaw == 'not_run':
        value = {'status': 'NOT_RUN', 'supplied_wheel': value['supplied_wheel']}
    if flaw == 'none':
        validate_report(value, root)
    else:
        with pytest.raises(ValueError):
            validate_report(value, root)


def passing(root, local=False):
    value = {
        'status': 'PASS', 'layer': 'native_deployment_e2e',
        'deployment_gates': 'MANUAL_GATE_PENDING', 'code_digests': code_digests(root),
        'tree_unchanged': True, 'counts': {'passed': 4, 'failed': 0, 'skipped': 0},
        'checks': {key: True for key in CHECKS},
        'versions': {'alerta': '9.1.0', 'zabbix': '7.0.10', 'postgres_major': '18'},
        'started_at': '2026-09-11T12:00:00Z', 'finished_at': '2026-09-11T12:00:45Z',
        'runner_shutdown_exit_code': 0, 'shutdown_exit_code': 0,
        'image_ids': {key: 'sha256:' + 'a' * 64 for key in COMPONENTS},
    }
    if local:
        value.update(not_docker=True, image_ids=None,
                     runner='Synthetic local-process record; no service is executed by this test.',
                     components={key: 'Synthetic component identity' for key in COMPONENTS | {'runner'}})
    return value


FLAWS = [
    'all_false', 'missing_check', 'integer_check', 'extra_check', 'zero_tests',
    'failed_test', 'boolean_count', 'missing_count', 'missing_cleanup',
    'failed_cleanup', 'boolean_cleanup', 'failed_runner_cleanup', 'tree_changed',
    'wrong_versions', 'wrong_layer', 'gates_closed', 'reversed_clock',
    'naive_clock', 'missing_images', 'wrong_image_hash', 'mixed_runtime',
    'string_runtime_flag', 'local_components_missing', 'local_runner_missing',
    'status_and_digests_only', 'not_run_with_passes', 'supplied_wheel_version',
]


def broken(root, flaw):
    report = passing(root, local=flaw.startswith('local_'))
    if flaw == 'supplied_wheel_version':
        report.update(artifact_mode='supplied_wheel', supplied_wheel={
            'filename': 'zabalack-1.0-py3-none-any.whl', 'version': '1.0',
            'sha256': 'a' * 64, 'installed_bytes_verified': True})
        return report
    if flaw == 'all_false': report['checks'] = {key: False for key in CHECKS}
    elif flaw == 'missing_check': report['checks'].pop('exactly_one_effect')
    elif flaw == 'integer_check': report['checks']['exactly_one_effect'] = 1
    elif flaw == 'extra_check': report['checks']['unreviewed'] = True
    elif flaw == 'zero_tests': report['counts']['passed'] = 0
    elif flaw == 'failed_test': report['counts']['failed'] = 1
    elif flaw == 'boolean_count': report['counts']['skipped'] = False
    elif flaw == 'missing_count': del report['counts']['skipped']
    elif flaw == 'missing_cleanup': del report['shutdown_exit_code']
    elif flaw == 'failed_cleanup': report['shutdown_exit_code'] = 1
    elif flaw == 'boolean_cleanup': report['shutdown_exit_code'] = False
    elif flaw == 'failed_runner_cleanup': report['runner_shutdown_exit_code'] = 1
    elif flaw == 'tree_changed': report['tree_unchanged'] = False
    elif flaw == 'wrong_versions': report['versions']['zabbix'] = '7.0.999'
    elif flaw == 'wrong_layer': report['layer'] = 'mocked'
    elif flaw == 'gates_closed': report['deployment_gates'] = 'PASS'
    elif flaw == 'reversed_clock': report['finished_at'] = '2026-09-10T12:00:00Z'
    elif flaw == 'naive_clock': report['started_at'] = '2026-09-11T12:00:00'
    elif flaw == 'missing_images': del report['image_ids']
    elif flaw == 'wrong_image_hash': report['image_ids']['postgres'] = 'postgres:18.3'
    elif flaw == 'mixed_runtime': report['not_docker'] = True
    elif flaw == 'string_runtime_flag': report['not_docker'] = 'false'
    elif flaw == 'local_components_missing': del report['components']['runner']
    elif flaw == 'local_runner_missing': del report['runner']
    elif flaw == 'status_and_digests_only': report = {k: report[k] for k in ('status', 'code_digests')}
    elif flaw == 'not_run_with_passes': report['status'] = 'NOT_RUN'
    else: raise AssertionError('Unknown test case')
    return report


def write(root, record):
    (root / NAME).write_text(json.dumps(record))


@pytest.mark.parametrize('flaw', FLAWS)
def test_export_rejects_contradictory_deployment_evidence(candidate, flaw):
    root, _ = candidate
    write(root, broken(root, flaw))
    findings = public_audit.evidence_binding_findings(root)
    assert any(row['path'] == NAME for row in findings)


@pytest.mark.parametrize('flaw', FLAWS)
def test_unsigned_archive_verifier_rejects_contradictory_deployment_evidence(candidate, flaw):
    root, output = candidate
    write(root, broken(root, flaw))
    # Rebuild every synthetic subject/hash, so this exercises evidence meaning
    # rather than merely tripping a changed-file checksum.
    build_fixture(root, output)
    with pytest.raises(ValueError, match='Deployment E2E'):
        verify_release.verify(root, output / 'dist')


@pytest.mark.parametrize('mode', ['docker', 'local', 'not_run'])
def test_valid_record_keeps_its_runtime_scope(candidate, mode):
    root, output = candidate
    value = passing(root, local=mode == 'local') if mode != 'not_run' else {
        'status': 'NOT_RUN', 'reason': 'local_docker_unavailable_or_refused',
    }
    original = copy.deepcopy(value)
    write(root, value)
    assert not any(row['path'] == NAME for row in public_audit.evidence_binding_findings(root))
    build_fixture(root, output)
    assert verify_release.verify(root, output / 'dist')['signed_attestation'] is False
    assert json.loads((root / NAME).read_text()) == original


def test_duplicate_json_fields_cannot_hide_failed_checks(candidate):
    root, output = candidate
    value = passing(root)
    encoded = json.dumps(value)
    (root / NAME).write_text('{"checks": {}, ' + encoded[1:])
    assert any(row['path'] == NAME for row in public_audit.evidence_binding_findings(root))
    build_fixture(root, output)
    with pytest.raises(ValueError, match='Deployment E2E'):
        verify_release.verify(root, output / 'dist')
