#!/usr/bin/env python3
"""Map only reports selected by the latest reference run to the catalogue."""
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
from evidence_utils import cases, binding_error

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    report = json.loads((ROOT / 'evidence/reference-run.json').read_text())
    # Every mapped record must have been produced on exactly this source and
    # harness tree (tools/evidence_utils.py); stale or foreign evidence is refused.
    problem = binding_error(report, ROOT, 'Reference')
    if problem:
        raise RuntimeError(problem)
    groups = {}
    for phase in report['phases']:
        if not phase['junit']:
            raise RuntimeError('Missing executed phase report; cannot rebuild acceptance mapping')
        groups.setdefault(phase['layer'], []).append(ROOT / phase['junit'])
    if 'postgres_wasm' not in groups:
        raise RuntimeError('Run the explicit --wasm suite before generating the combined reference mapping')
    merged = ET.Element('testsuites')
    for path in groups['postgres_wasm']:
        data = ET.parse(path).getroot()
        for suite in list(data) if data.tag == 'testsuites' else [data]:
            merged.append(suite)
    output = ROOT / 'evidence/postgres-wasm-combined.xml'
    ET.ElementTree(merged).write(output, encoding='utf-8', xml_declaration=True)
    command = [sys.executable, 'tools/build_acceptance.py', '--unit-xml',
               str(groups['unit_mocked'][0].relative_to(ROOT)), '--wasm-xml', str(output.relative_to(ROOT))]
    if groups.get('optional_native_real_zabbix'):
        command += ['--optional-xml', str(groups['optional_native_real_zabbix'][0].relative_to(ROOT))]
    if (ROOT / 'evidence/runtime_wasm_sql.json').is_file():
        sql_run = json.loads((ROOT / 'evidence/runtime_wasm_sql.json').read_text())
        problem = binding_error(sql_run, ROOT, 'SQL')
        if problem:
            raise RuntimeError(problem)
        if sql_run.get('status') != 'PASS' or not sql_run.get('results') or any(
                not isinstance(item, dict) or item.get('status') != 'PASS' for item in sql_run['results']):
            raise RuntimeError('SQL run did not complete successfully; cannot rebuild acceptance mapping')
        command += ['--wasm-sql-json', 'evidence/runtime_wasm_sql.json']
    native = ROOT / 'evidence/native-run.json'
    if native.is_file():
        # Native phases are mapped only when their redacted JUnit was actually
        # produced by tools/native_ci.py (or an equivalent recorded runner).
        run = json.loads(native.read_text())
        if not isinstance(run, dict) or not isinstance(run.get('phases'), list):
            raise RuntimeError('Native phase report is malformed; cannot rebuild acceptance mapping')
        phases = run['phases']
        if not (run.get('status') == 'NOT_RUN' and not phases):
            if run.get('status') != 'PASS' or not phases:
                raise RuntimeError('Native phase run did not complete successfully; cannot rebuild acceptance mapping')
            problem = binding_error(run, ROOT, 'Native')
            if problem:
                raise RuntimeError(problem)
        for phase in phases:
            if not isinstance(phase, dict):
                raise RuntimeError('Native phase record is malformed; cannot rebuild acceptance mapping')
            if not phase.get('junit'):
                raise RuntimeError('Native phase has no executed report; cannot rebuild acceptance mapping')
            if not (ROOT / phase['junit']).is_file():
                raise RuntimeError('Recorded native report is missing; cannot rebuild acceptance mapping')
            if phase.get('exit_code') != 0 or phase.get('status') != 'PASS':
                raise RuntimeError('Native phase did not complete successfully; review its failure before mapping')
            if (type(phase.get('shutdown_exit_code')) is not int
                    or phase['shutdown_exit_code'] != 0 or phase.get('shutdown_reason')):
                raise RuntimeError('Native phase cleanup did not explicitly succeed; cannot rebuild acceptance mapping')
            try:
                rows = cases(ROOT / phase['junit'])
            except (ET.ParseError, OSError, ValueError) as error:
                raise RuntimeError('Native phase JUnit is malformed; cannot rebuild acceptance mapping') from error
            if not rows or any(row['status'] != 'passed' for row in rows):
                raise RuntimeError('Native phase JUnit is incomplete or not passing; cannot rebuild acceptance mapping')
            observed = {status: sum(row['status'] == status for row in rows)
                        for status in ('passed', 'failed', 'skipped')}
            if 'counts' in phase and phase['counts'] != observed:
                raise RuntimeError('Native phase counts disagree with JUnit; cannot rebuild acceptance mapping')
            command += ['--native-xml', phase['junit']]
    real = ROOT / 'evidence/real-zabbix-run.json'
    if real.is_file():
        try:
            run = json.loads(real.read_text())
        except (json.JSONDecodeError, OSError) as error:
            raise RuntimeError('Real-Zabbix run record is malformed; cannot rebuild acceptance mapping') from error
        if not isinstance(run, dict):
            raise RuntimeError('Real-Zabbix run record is malformed; cannot rebuild acceptance mapping')
        if run.get('status') == 'NOT_RUN':
            # Explicit unavailability may coexist with an old XML on disk, but
            # it must not select that XML or claim any executed test counts.
            empty = {status: 0 for status in ('passed', 'failed', 'skipped')}
            counts = run.get('counts', empty)
            if (run.get('junit') or not isinstance(counts, dict) or counts != empty
                    or any(type(value) is not int for value in counts.values())):
                raise RuntimeError('Real-Zabbix NOT_RUN record contains execution evidence; cannot rebuild acceptance mapping')
        else:
            if run.get('status') != 'PASS' or ('exit_code' in run and run['exit_code'] != 0):
                raise RuntimeError('Real-Zabbix run did not complete successfully; cannot rebuild acceptance mapping')
            problem = binding_error(run, ROOT, 'Real-Zabbix')
            if problem:
                raise RuntimeError(problem)
            if not isinstance(run.get('junit'), str) or not run['junit'] or not (ROOT / run['junit']).is_file():
                raise RuntimeError('Real-Zabbix run record without an executed report; cannot rebuild acceptance mapping')
            try:
                rows = cases(ROOT / run['junit'])
            except (ET.ParseError, OSError, ValueError) as error:
                raise RuntimeError('Real-Zabbix JUnit is malformed; cannot rebuild acceptance mapping') from error
            if not rows or any(row['status'] != 'passed' for row in rows):
                raise RuntimeError('Real-Zabbix JUnit is incomplete or not passing; cannot rebuild acceptance mapping')
            observed = {status: sum(row['status'] == status for row in rows)
                        for status in ('passed', 'failed', 'skipped')}
            counts = run.get('counts')
            if (not isinstance(counts, dict) or counts != observed
                    or any(type(value) is not int for value in counts.values())):
                raise RuntimeError('Real-Zabbix counts disagree with JUnit; cannot rebuild acceptance mapping')
            command += ['--real-zabbix-xml', run['junit']]
    return subprocess.run(command, cwd=ROOT).returncode


if __name__ == '__main__':
    raise SystemExit(main())
