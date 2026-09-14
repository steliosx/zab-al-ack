"""Synthetic report construction for validator tests; never exported as execution."""
from evidence_utils import code_digests
from mutation_catalog import selected
from run_mutation_checks import OUTCOMES


def passing_mutation_record(root, profile='pr'):
    baseline = {'outcome': 'BASELINE_PASS', 'exit_code': 0, 'tests': 2,
                'failures': 0, 'errors': 0, 'skipped': 0, 'assertion_failures': 0,
                'case_digest': 'a' * 64}
    mutants = [dict(baseline, name=item.name, outcome='KILLED', exit_code=1,
                    failures=1, assertion_failures=1) for item in selected(profile)]
    return {'schema_version': 1, 'status': 'PASS', 'profile': profile,
            'code_digests': code_digests(root), 'tree_unchanged': True,
            'baseline': baseline, 'mutants': mutants, 'selected_count': len(mutants),
            'counts': {key: len(mutants) if key == 'KILLED' else 0 for key in OUTCOMES}}
