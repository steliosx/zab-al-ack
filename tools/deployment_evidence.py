"""Validate the meaning of a public deployment-lab result, without services.

This is consistency validation, not authentication of a record's author. The
service runner owns execution. Local-process records (`not_docker`) keep their
distinct scope: they establish the installed-wheel service path on the named
local services (R14; the local runner verifies the installed bytes against the
wheel) and cannot establish Docker execution or the Compose-only exact-artifact
`supplied_wheel` receipt.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import tomllib

from evidence_utils import binding_error

ROOT = Path(__file__).resolve().parents[1]


def zabbix_reference_version() -> str:
    """The pinned Zabbix patch (lab/zabbix_reference.json, R27); never a default."""
    selected = json.loads((ROOT / 'lab/zabbix_reference.json').read_text())['selected']
    if not isinstance(selected.get('version'), str) or not re.fullmatch(r'7\.0\.[0-9]+', selected['version']):
        raise ValueError('lab/zabbix_reference.json selection is malformed')
    return selected['version']


CHECK_NAMES = {
    'authenticated_capture', 'cross_customer_denied', 'exactly_one_effect',
    'human_update_recorded', 'ingest_key_local_only', 'media_macro_ingestion',
    'neutral_update_filtered', 'probe_denied_table_read', 'projection',
    'recovery_macro_ingestion', 'repeat_409', 'separate_native_logins',
    'worker_denied_table_update',
}
REFERENCE_VERSIONS = {'alerta': '9.1.0', 'zabbix': zabbix_reference_version(), 'postgres_major': '18'}
IMAGE_COMPONENTS = {'postgres', 'zabbix-db', 'zabbix-server', 'zabbix-web'}
EXPECTED_COUNTS = {'passed': 4, 'failed': 0, 'skipped': 0}
EMPTY_COUNTS = {'passed': 0, 'failed': 0, 'skipped': 0}
MAX_RECORD_BYTES = 65536


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError('Deployment E2E ' + reason)


def _object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        require(key not in result, 'record contains duplicate fields')
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError('Deployment E2E record contains a non-JSON number')


def read_record(data: bytes) -> dict:
    require(len(data) <= MAX_RECORD_BYTES, 'record exceeds size bound')
    try:
        value = json.loads(data, object_pairs_hook=_object, parse_constant=_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError('Deployment E2E record is malformed') from error
    require(isinstance(value, dict), 'record must be an object')
    return value


def exact_counts(value: object, expected: dict) -> bool:
    return (isinstance(value, dict) and value == expected
            and all(type(item) is int for item in value.values()))


def validate_report(record: object, root: Path) -> None:
    require(isinstance(record, dict), 'record must be an object')
    if record.get('status') == 'NOT_RUN':
        # Unavailability supplies no execution evidence and needs no source
        # binding. It must not retain success from an earlier invocation.
        require(exact_counts(record.get('counts', EMPTY_COUNTS), EMPTY_COUNTS),
                'NOT_RUN record contains test results')
        require(not any(record.get(key) for key in ('checks', 'image_ids', 'components', 'junit', 'supplied_wheel')),
                'NOT_RUN record contains execution evidence')
        require(record.get('tree_unchanged') is not True,
                'NOT_RUN record claims an executed unchanged tree')
        return

    require(record.get('status') == 'PASS', 'run did not pass')
    require(binding_error(record, root, 'Deployment E2E') is None,
            'record is stale or not bound to this tree')
    require(record.get('layer') == 'native_deployment_e2e'
            and record.get('deployment_gates') == 'MANUAL_GATE_PENDING',
            'record has an invalid evidence scope')
    require(record.get('tree_unchanged') is True, 'run did not verify an unchanged tree')
    require(exact_counts(record.get('counts'), EXPECTED_COUNTS),
            'run did not pass all four service cases')
    checks = record.get('checks')
    require(isinstance(checks, dict) and set(checks) == CHECK_NAMES
            and all(value is True for value in checks.values()),
            'required service observations are incomplete')
    require(record.get('versions') == REFERENCE_VERSIONS, 'reference versions differ')
    if 'artifact_mode' in record or 'supplied_wheel' in record:
        require(record.get('artifact_mode') == 'supplied_wheel', 'invalid artifact mode')
        wheel = record.get('supplied_wheel')
        require(isinstance(wheel, dict) and set(wheel) == {'filename', 'sha256', 'version', 'installed_bytes_verified'},
                'supplied wheel identity is incomplete')
        require(isinstance(wheel['version'], str) and re.fullmatch(r'[0-9][A-Za-z0-9.!+_-]*', wheel['version'])
                and wheel['filename'] == 'zabalack-' + wheel['version'] + '-py3-none-any.whl'
                and isinstance(wheel['sha256'], str) and re.fullmatch(r'[0-9a-f]{64}', wheel['sha256'])
                and wheel['installed_bytes_verified'] is True, 'supplied wheel identity is invalid')
        try:
            project = tomllib.loads((root / 'pyproject.toml').read_text())['project']
        except (OSError, ValueError, KeyError) as error:
            raise ValueError('Deployment E2E project identity is unavailable') from error
        require(project.get('name') == 'zabalack' and project.get('version') == wheel['version'],
                'supplied wheel project version differs')
    require(all(type(record.get(key)) is int and record[key] == 0
                for key in ('runner_shutdown_exit_code', 'shutdown_exit_code')),
            'cleanup did not explicitly succeed')
    native_lab = record.get('native_lab')
    if native_lab is not None:
        # A record produced through lab/deployment_native_lab.py passes only when the
        # cycle left nothing behind: both services stopped, diagnostics preserved,
        # lab directories removed. Anything else was rewritten to FAIL by the cycle.
        require(isinstance(native_lab, dict) and not native_lab.get('pending') and native_lab.get('final_state') == 'absent'
                and native_lab.get('cleanup_complete') is True and native_lab.get('run_exit_code') == 0,
                'native lab cycle did not end with the lab absent')
    require(not any(record.get(key) for key in ('reason', 'shutdown_reason', 'runner_shutdown_reason')),
            'passing record retains a failure reason')
    try:
        start = datetime.fromisoformat(record['started_at'])
        finish = datetime.fromisoformat(record['finished_at'])
        valid_time = start.utcoffset() is not None and finish.utcoffset() is not None and finish >= start
    except (KeyError, TypeError, ValueError):
        valid_time = False
    require(valid_time, 'run timestamps are missing or inconsistent')

    local = record.get('not_docker', False)
    require(type(local) is bool, 'runtime flag must be boolean')
    if local:
        require(record.get('image_ids') is None, 'local-process record claims Docker images')
        components = record.get('components')
        require(isinstance(components, dict) and set(components) == IMAGE_COMPONENTS | {'runner'}
                and all(isinstance(value, str) and 0 < len(value.strip()) <= 4096
                        for value in components.values()),
                'local component identities are missing')
        runner = record.get('runner')
        require(isinstance(runner, str) and 0 < len(runner.strip()) <= 4096,
                'local execution description is missing')
    else:
        images = record.get('image_ids')
        require(isinstance(images, dict) and set(images) == IMAGE_COMPONENTS
                and all(isinstance(value, str) and re.fullmatch(r'sha256:[0-9a-f]{64}', value)
                        for value in images.values()),
                'Docker image identities are missing or invalid')
        require(not record.get('components'), 'Docker record contains local-process identities')
