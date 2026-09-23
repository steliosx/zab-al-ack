"""The native-lab binding end to end with the real runner, cycle and validator functions (review of b57bebb, HIGH).

No function on the evidence path is mocked: tools/run_deployment_e2e_local.conclude (the runner's last step),
lab/deployment_native_lab.finalize_record (the cycle's binding) and tools/deployment_evidence.validate_report
run as shipped, against a genuine passing record for this checkout. A live cycle on the runner host is the
process-level evidence; these tests pin the lifecycle.
"""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

import run_deployment_e2e_local as runner
from deployment_evidence import validate_report
from tests.release.test_deployment_evidence import passing

ROOT = Path(__file__).resolve().parents[2]
PENDING = {'pending': True, 'final_state': None, 'cleanup_complete': False}
CLEAN = {'run_id': 'synthetic', 'run_exit_code': 0, 'cleanup': {'cleanup': {'zabbix': {}, 'postgres': {'status': 'STOPPED'}}, 'state': 'stopped'},
         'retention': {'status': 'RETAINED'}, 'wipe': {'wipe': 'done', 'state': 'absent'}, 'final_state': 'absent'}


@pytest.fixture
def cycle(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('binding_deployment_native_lab', ROOT / 'lab/deployment_native_lab.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'RECORD', tmp_path / 'deployment-e2e-run.json')  # REPO stays this checkout: the real validator binds to it
    return module


def runner_record():
    """What the bound runner holds after a passing run: a genuine PASS for this checkout plus the pending marker."""
    report = passing(ROOT, local=True)
    report['native_lab'] = dict(PENDING)
    return report


def test_a_successful_bound_run_becomes_pass_only_after_the_cycle_binds_it(cycle):
    report = runner_record()
    # Pending intermediate evidence never validates as a final PASS, before or after the runner's last step.
    with pytest.raises(ValueError):
        validate_report(copy.deepcopy(report), ROOT)
    runner.conclude(report, ROOT)
    assert report['status'] == 'PENDING_BINDING' and report['provisional_status'] == 'PASS'
    with pytest.raises(ValueError, match='run did not pass'):
        validate_report(copy.deepcopy(report), ROOT)
    cycle.write_record(report)
    # The cycle binds: run exited 0, cleanup complete, lab absent.
    final = cycle.finalize_record('cycle', 0, dict(CLEAN))
    assert final['status'] == 'PASS' and 'provisional_status' not in final and not final.get('reason')
    assert final['native_lab'] == {'command': 'cycle', 'run_id': 'synthetic', 'run_exit_code': 0, 'cleanup': CLEAN['cleanup'],
                                   'retention': {'status': 'RETAINED'}, 'wipe': {'wipe': 'done', 'state': 'absent'},
                                   'final_state': 'absent', 'cleanup_complete': True}
    validate_report(final, ROOT)  # the final bound record validates
    assert json.loads(cycle.RECORD.read_text()) == final


@pytest.mark.parametrize('ending', ['never-bound', 'cleanup-incomplete', 'retention-failed', 'interrupted', 'run-command'])
def test_a_run_cut_short_or_not_cleanly_bound_never_leaves_a_validating_pass(cycle, ending):
    report = runner_record()
    runner.conclude(report, ROOT)
    cycle.write_record(report)
    if ending == 'never-bound':  # e.g. the cycle was SIGKILLed after the runner wrote its record
        final = json.loads(cycle.RECORD.read_text())
        assert final['status'] == 'PENDING_BINDING'
    elif ending == 'cleanup-incomplete':
        final = cycle.finalize_record('cycle', 5, dict(CLEAN, final_state='running', wipe=None))
    elif ending == 'retention-failed':
        final = cycle.finalize_record('cycle', 5, dict(CLEAN, retention={'error': 'synthetic'}, wipe=None, final_state='stopped'))
    elif ending == 'interrupted':
        final = cycle.finalize_record('cycle', 130, dict(CLEAN, refusal='interrupted (SIGTERM)', interrupted=True))
    else:
        final = cycle.finalize_record('run', 0, {'run_exit_code': 0, 'final_state': 'running'})
    assert final['status'] != 'PASS'
    with pytest.raises(ValueError):
        validate_report(final, ROOT)


def test_a_bound_record_that_fails_final_validation_is_a_fail(cycle):
    report = runner_record()
    runner.conclude(report, ROOT)
    report['code_digests'] = {'source': '0' * 64, 'harness': '0' * 64}  # evidence of another tree
    cycle.write_record(report)
    final = cycle.finalize_record('cycle', 0, dict(CLEAN))
    assert final['status'] == 'FAIL' and final['reason'] == 'final_evidence_validation_failed'


def test_a_plain_pass_from_a_bound_runner_is_not_accepted_as_final(cycle):
    """Only the provisional PENDING_BINDING shape can be bound; a runner PASS in a cycle skipped the provisional check."""
    report = passing(ROOT, local=True)
    cycle.write_record(report)
    final = cycle.finalize_record('cycle', 0, dict(CLEAN))
    assert final['status'] == 'FAIL' and final['reason'] == 'runner_record_not_pending'


def test_the_runner_run_directly_still_validates_its_pass_as_final():
    report = passing(ROOT, local=True)
    runner.conclude(report, ROOT)
    assert report['status'] == 'PASS'
    broken = passing(ROOT, local=True)
    broken['counts']['failed'] = 1
    runner.conclude(broken, ROOT)
    assert broken['status'] == 'FAIL' and broken['reason'] == 'final_evidence_validation_failed'
