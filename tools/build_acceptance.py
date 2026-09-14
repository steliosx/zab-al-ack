#!/usr/bin/env python3
"""Build truthful T1–T41 matrix from explicitly selected final test reports.

Never infers execution from test-file existence and never turns lab fixtures into
G1–G8 deployment evidence. Historical regression failures remain separate files.
"""
from __future__ import annotations
import argparse
import csv
import json
import hashlib
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from evidence_utils import code_digests

ROOT = Path(__file__).resolve().parents[1]
# T40/T41 are the amendment A5 additions: reader-facing confidentiality and safe
# retirement of stored intents. Both need native evidence. T41 needs real
# PostgreSQL cleanup and concurrency. T40 is not response-only: it also covers
# the authenticated search predicate and the interval in which a staged intent is
# committed but the acknowledgement has not finished, which needs two concurrent
# sessions and so cannot be established by the single-backend WASM layer.
IDS = {f'T{i}' for i in range(1, 42)} - {'T11'} | {'T11a', 'T11b'}
NATIVE_REQUIRED = {'T1','T2','T4','T5','T6','T7','T8','T10','T14','T15','T16','T19','T20','T21','T22',
                   'T30','T31','T32','T34','T35','T36','T37','T38','T39','T40','T41'}
MANUAL = {'T2','T20','T27','T30','T31','T34'}


def test_ids(value):
    return sorted(set(re.findall(r'T[1-9][0-9]?[ab]?(?![a-zA-Z0-9])', value)) & IDS)


def read_xml(path, layer):
    root = ET.parse(path).getroot()
    rows = []
    for case in root.iter('testcase'):
        name = case.get('name', '')
        classname = case.get('classname', '')
        test_file = case.get('file') or classname.replace('.', '/') + '.py'
        status = 'PASS'
        reason = ''
        if case.find('failure') is not None or case.find('error') is not None:
            status = 'FAIL'
        elif case.find('skipped') is not None:
            reason = (case.find('skipped').get('message') or '')
            status = 'BLOCKED_ENVIRONMENT' if 'BLOCKED_ENVIRONMENT' in reason else 'NOT_RUN'
        actual_layer = layer
        if 'native_concurrent' in name or 'migration_rerun' in name or 'test_native_login' in classname or 'test_native_delivery_faults' in classname:
            actual_layer = 'native_postgres'
        if 'pgbouncer' in (classname + name).lower():
            actual_layer = 'pgbouncer'
        if name.startswith('test_T34_native_statement_timeout'):
            actual_layer = 'native_postgres'
        if 'real_zabbix' in classname:
            actual_layer = 'real_zabbix'
        rows.append({'nodeid': test_file + '::' + name, 'ids': test_ids(name), 'layer': actual_layer,
                     'status': status, 'evidence_file': str(path.relative_to(ROOT)), 'reason': reason})
    return rows


def aggregate(rows):
    # A test skipped by the WASM/optional runner can execute in a separately
    # recorded native run. Resolve ONLY that same test in that same evidence
    # layer; retain skips for other tests and never mask an executed failure.
    per_case = {}
    for row in rows:
        per_case.setdefault((row['nodeid'], row['layer']), set()).add(row['status'])
    values = set()
    for statuses in per_case.values():
        if 'FAIL' in statuses:
            values.add('FAIL')
        elif 'PASS' in statuses:
            values.add('PASS')
        else:
            values.update(statuses)
    if 'FAIL' in values:
        return 'FAIL'
    if 'BLOCKED_ENVIRONMENT' in values:
        return 'BLOCKED_ENVIRONMENT'
    if 'NOT_RUN' in values:
        return 'NOT_RUN'
    return 'PASS' if values else 'NOT_RUN'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unit-xml', required=True, type=Path)
    parser.add_argument('--wasm-xml', required=True, type=Path)
    parser.add_argument('--optional-xml', type=Path)
    parser.add_argument('--native-xml', type=Path, action='append', default=[],
                        help='Redacted JUnit from an executed native PostgreSQL/PgBouncer phase (repeatable)')
    parser.add_argument('--real-zabbix-xml', type=Path, action='append', default=[],
                        help='Redacted JUnit from an executed real-Zabbix contract run (repeatable)')
    parser.add_argument('--wasm-sql-json', type=Path)
    args = parser.parse_args()
    evidence = read_xml(args.unit_xml.resolve(), 'unit_mock') + read_xml(args.wasm_xml.resolve(), 'postgres_wasm')
    if args.optional_xml:
        evidence += read_xml(args.optional_xml.resolve(), 'real_zabbix')
    for executed in args.real_zabbix_xml:
        # An executed real-Zabbix report resolves the reference runner's skip of
        # the same test; a missing file is an error, never a silent NOT_RUN.
        evidence += read_xml(executed.resolve(), 'real_zabbix')
    for native in args.native_xml:
        # Only reports produced by an executed native phase are accepted; a
        # missing file is an error, never a silent BLOCKED_ENVIRONMENT.
        evidence += read_xml(native.resolve(), 'native_postgres')
    if args.wasm_sql_json:
        packet = json.loads(args.wasm_sql_json.read_text())
        for item in packet.get('results', packet.get('checks', packet.get('tests', []))):
            # SQL suites carry their own explicit test-ID list; no name inference
            # from source files is used to manufacture a PASS.
            labels = item.get('test_ids', item.get('acceptance_ids', []))
            if isinstance(labels, str):
                labels = test_ids(labels)
            sql_path = ROOT / item.get('test', '')
            if not labels and item.get('sha256') and sql_path.is_file():
                raw = sql_path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != item['sha256']:
                    raise ValueError('SQL evidence hash no longer matches executed source; rerun the suite')
                labels = test_ids(raw.decode())
            evidence.append({'nodeid': item.get('test', 'SQL assertion suite'), 'ids': sorted(set(labels) & IDS),
                'layer': 'postgres_wasm', 'status': item.get('status', 'NOT_RUN'),
                'evidence_file': str(args.wasm_sql_json.resolve().relative_to(ROOT)), 'reason': item.get('scope', '')})
    path = ROOT / 'docs/acceptance_matrix.csv'
    with path.open(newline='') as stream:
        matrix = list(csv.DictReader(stream))
    assert {row['test_id'] for row in matrix} == IDS and len(matrix) == 42
    for row in matrix:
        tid = row['test_id']
        relevant = [item for item in evidence if tid in item['ids']]
        executed_keys = {(item['nodeid'], item['layer']) for item in relevant if item['status'] in {'PASS', 'FAIL'}}
        row['executed_test_references'] = ';'.join(sorted({item['nodeid'] for item in relevant if item['status'] in {'PASS','FAIL'}}))
        row['not_run_test_references'] = ';'.join(sorted({item['nodeid'] for item in relevant
            if item['status'] not in {'PASS','FAIL'} and (item['nodeid'], item['layer']) not in executed_keys}))
        row['evidence_file'] = ';'.join(sorted({item['evidence_file'] for item in relevant}))
        for layer in ('unit_mock', 'postgres_wasm', 'native_postgres', 'pgbouncer', 'real_zabbix'):
            items = [item for item in relevant if item['layer'] == layer]
            result = aggregate(items)
            if layer == 'native_postgres' and not items and tid in NATIVE_REQUIRED:
                result = 'BLOCKED_ENVIRONMENT'
            if layer == 'pgbouncer' and tid == 'T34' and not items:
                result = 'BLOCKED_ENVIRONMENT'
            row[layer + '_result'] = result
        executed = [item for item in relevant if item['status'] in {'PASS','FAIL'}]
        row['reference_result'] = aggregate(executed)
        if not executed:
            row['reference_result'] = 'MANUAL_GATE_PENDING' if tid in MANUAL else 'NOT_RUN'
        row['deployment_result'] = 'MANUAL_GATE_PENDING'
        row['notes'] = ('Reference result summarises ONLY mapped executed checks. PostgreSQL WASM is supplemental single-backend evidence; '
                        'unit/mock passes do not prove installed tenant permissions, real source effects, native concurrency or G1–G8. '
                        'See per-layer columns and deployment_acceptance.md for unexercised clauses.')
        if tid in MANUAL:
            row['notes'] += ' Full catalogue item remains MANUAL_GATE_PENDING.'
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(matrix[0]), lineterminator='\n'); writer.writeheader(); writer.writerows(matrix)
    report = {'semantics': 'All rows remain deployment MANUAL_GATE_PENDING. PASS refers only to mapped executed reference checks.',
              'reports': [str(args.unit_xml), str(args.wasm_xml)] + [str(item) for item in args.native_xml] + [str(item) for item in args.real_zabbix_xml],
              'reference_summary': {status: sum(row['reference_result'] == status for row in matrix)
                                    for status in ('PASS','FAIL','NOT_RUN','BLOCKED_ENVIRONMENT','MANUAL_GATE_PENDING')},
              'code_digests': code_digests(ROOT), 'test_evidence': evidence, 'acceptance': matrix}
    (ROOT / 'evidence/acceptance-results.json').write_text(json.dumps(report, indent=2) + '\n')
    lines = ['# Acceptance evidence by catalogue ID', '',
        'Every deployment result remains **MANUAL_GATE_PENDING**. PASS below describes only executed checks in the named layer, '
        'not full catalogue or deployment acceptance. WASM means PostgreSQL 18.3 in PGlite, a single backend; native concurrency, '
        'authentication, TLS, PgBouncer and HA are not established by it.', '',
        '| ID | Unit/mock | PostgreSQL WASM | Native PostgreSQL | Real Zabbix |',
        '|---|---|---|---|---|']
    for row in matrix:
        lines.append('| ' + ' | '.join(row[k] for k in ('test_id','unit_mock_result','postgres_wasm_result','native_postgres_result','real_zabbix_result')) + ' |')
    lines += ['', 'Exact executed/not-run test references, report paths and requirement wording are in `docs/acceptance_matrix.csv`. '
              'T2, T20, T27, T30, T31 and T34 require the explicit environment procedures in `docs/deployment_acceptance.md`. '
              'T11b deliberately preserves the approved trusted-operator scope without claiming historical ingestion lineage.', '']
    (ROOT / 'evidence/acceptance-results.md').write_text('\n'.join(lines))
    print(json.dumps(report['reference_summary']))


if __name__ == '__main__':
    main()
