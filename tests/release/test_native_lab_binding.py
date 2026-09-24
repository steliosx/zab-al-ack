"""The native-lab binding end to end with the real runner, cycle and validator functions (review of b57bebb, HIGH).

No function on the evidence path is mocked: tools/run_deployment_e2e_local.conclude (the runner's last step),
lab/deployment_native_lab.finalize_record (the cycle's binding) and tools/deployment_evidence.validate_report
run as shipped, against a genuine passing record for this checkout. The command-path tests at the end run
the cycle command into the real runner's main with only host effects replaced; a live cycle on the runner
host is the process-level evidence.
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
CLEAN = {'run_id': 'synthetic', 'run_exit_code': 0, 'runner_record': {'status': 'RETAINED'},
         'cleanup': {'cleanup': {'zabbix': {}, 'postgres': {'status': 'STOPPED'}}, 'state': 'stopped'},
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
    assert final['native_lab'] == {'command': 'cycle', 'run_id': 'synthetic', 'run_exit_code': 0, 'runner_record': {'status': 'RETAINED'},
                                   'cleanup': CLEAN['cleanup'],
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


# The command path end to end: lab/deployment_native_lab.main(['cycle', ...]) launches
# tools/run_deployment_e2e_local.main through its real run_lab command line, and the
# cycle's real validate_final checks the bound record. Only host effects are replaced:
# PostgreSQL/Zabbix processes (the cycle's service stubs), the runner's host layout
# and installed-environment probes, and the inner pytest, which writes the private
# observations and JUnit a passing tests/deployment_e2e session writes.
from tests.release.test_deployment_native_lab import CYCLE, cycle_environment, record  # noqa: E402
from tests.release import test_deployment_native_lab as native_lab_tests  # noqa: E402
native_lab = native_lab_tests.lab  # the native-lab fixture, shared under this name
from evidence_utils import HARNESS_SCOPE, SOURCE_SCOPE  # noqa: E402
from run_deployment_e2e import EXPECTED_CASES  # noqa: E402
from deployment_evidence import CHECK_NAMES, REFERENCE_VERSIONS  # noqa: E402


def command_path(lab, monkeypatch, *, stop_exit=0):
    real_validate_final = lab.module.validate_final
    cycle_environment(lab, monkeypatch, stop_exit=stop_exit)
    monkeypatch.setattr(lab.module, 'validate_final', real_validate_final)
    # lab.tmp is the cycle's REPO and the runner's ROOT; linking the digest scopes makes its
    # code digests those of this checkout, so the real validator binds to real sources.
    for entry in SOURCE_SCOPE + HARNESS_SCOPE:
        if (ROOT / entry).exists():
            (lab.tmp / entry).symlink_to(ROOT / entry)
    private = lab.tmp / 'runner-private'
    private.mkdir(mode=0o700)
    for key, value in [('ROOT', lab.tmp), ('PRIVATE', private), ('check_host_layout', lambda: None),
                       ('check_source', lambda path, expected, root: (b'', {'filename': path.name, 'sha256': expected, 'version': '0.1.0a18'})),
                       ('check_runner', lambda python, path, expected: {'python': str(python), 'version': '0.1.0a18', 'wheel_sha256': expected,
                                                                       'installed_bytes_verified': True, 'startup_hooks': [],
                                                                       'dependency_verification': 'record_consistency_only'}),
                       ('bootstrap', lambda project: None), ('clean_private', lambda: None), ('lab_ports_free', lambda: True)]:
        monkeypatch.setattr(runner, key, value)
    resets = []
    monkeypatch.setattr(runner.subprocess, 'run', lambda command, **kwargs: resets.append(command))

    def inner_pytest(command, **kwargs):
        assert command[1:4] == ['-m', 'pytest', 'tests/deployment_e2e']
        (private / 'observations.json').write_text(json.dumps({'checks': {name: True for name in CHECK_NAMES}, 'versions': dict(REFERENCE_VERSIONS)}))
        (private / 'junit.xml').write_text('<testsuites><testsuite>' + ''.join(
            f'<testcase classname="tests.deployment_e2e.test_service_path" name="{name}"/>' for name in sorted(EXPECTED_CASES)) + '</testsuite></testsuites>')
        return 0
    monkeypatch.setattr(runner, 'run_child', inner_pytest)
    seen = {}

    def run_lab_child(command, **kwargs):
        # What run_lab executes, run in process: the real runner with the binding the cycle sets.
        assert command[1] == str(lab.tmp / 'tools/run_deployment_e2e_local.py') and kwargs['env']['ZABALACK_NATIVE_LAB_BINDING'] == '1'
        monkeypatch.setenv('ZABALACK_NATIVE_LAB_BINDING', '1')
        code = runner.main(command[2:])
        monkeypatch.delenv('ZABALACK_NATIVE_LAB_BINDING')
        seen['runner_bytes'] = lab.module.RECORD.read_bytes()
        seen['runner_exit'], seen['runner_record'] = code, json.loads(seen['runner_bytes'])
        if seen.get('interrupt_after_runner'):
            raise KeyboardInterrupt('interrupted (SIGTERM)')
        return code
    monkeypatch.setattr(lab.module, 'run_child', run_lab_child)
    seen['resets'] = resets
    return seen


def test_the_cycle_command_binds_the_real_runner_record_and_the_final_record_validates(native_lab, monkeypatch):
    lab = native_lab
    seen = command_path(lab, monkeypatch)
    code = lab.module.main(CYCLE)
    intermediate, final = seen['runner_record'], record(lab)
    # 1, 2: a successful run whose inner tests all passed
    assert seen['runner_exit'] == 0 and intermediate['counts'] == {'passed': 4, 'failed': 0, 'skipped': 0} and seen['resets']
    # 3: the runner's own record is pending and never validates as a final PASS
    assert intermediate['status'] == 'PENDING_BINDING' and intermediate['provisional_status'] == 'PASS' and intermediate['native_lab']['pending'] is True
    for candidate in (intermediate, dict(intermediate, status='PASS')):
        with pytest.raises(ValueError):
            validate_report(copy.deepcopy(candidate), lab.tmp)
    # 4, 5, 6: the outer cycle bound it after a complete cleanup that left the lab absent
    assert final['native_lab']['command'] == 'cycle' and final['native_lab']['run_exit_code'] == 0
    assert final['native_lab']['cleanup_complete'] is True and final['native_lab']['final_state'] == 'absent' and 'pending' not in final['native_lab']
    assert lab.module.inspect(lab.binaries)['state'] == 'absent'
    # 7, 8, 9: the final bound record validates, the command exits 0, the status is PASS
    validate_report(copy.deepcopy(final), lab.tmp)
    assert code == 0 and final['status'] == 'PASS' and 'provisional_status' not in final


def test_the_cycle_command_never_passes_when_cleanup_fails_after_a_passing_runner(native_lab, monkeypatch):
    lab = native_lab
    seen = command_path(lab, monkeypatch, stop_exit=1)
    code = lab.module.main(CYCLE)
    final = record(lab)
    assert seen['runner_exit'] == 0 and seen['runner_record']['status'] == 'PENDING_BINDING'
    assert code != 0 and final['status'] == 'FAIL' and final['native_lab']['cleanup_complete'] is False
    with pytest.raises(ValueError):
        validate_report(copy.deepcopy(final), lab.tmp)


def retained_runner_record(lab, final):
    """The runner-record retention bound in the final record: (manifest entry, retained bytes)."""
    import hashlib
    retention = final['native_lab']['runner_record']
    assert retention['status'] == 'RETAINED' and retention['label'] == 'runner-record'
    directory = lab.tmp / retention['directory']
    assert hashlib.sha256((directory / 'manifest.json').read_bytes()).hexdigest() == retention['manifest_sha256']
    manifest = json.loads((directory / 'manifest.json').read_text())
    assert list(manifest['files']) == ['evidence__deployment-e2e-run.json'] and manifest['run_id'] == final['native_lab']['run_id']
    entry = manifest['files']['evidence__deployment-e2e-run.json']
    retained = (directory / 'evidence__deployment-e2e-run.json').read_bytes()
    assert entry['source'] == 'evidence/deployment-e2e-run.json' and hashlib.sha256(retained).hexdigest() == entry['sha256']
    assert oct(directory.stat().st_mode & 0o777) == oct(0o700)
    return entry, retained


def test_the_runners_original_provisional_record_is_retained_before_the_cycle_replaces_it(native_lab, monkeypatch):
    """S1: the record the runner wrote is retained byte for byte, under its own private label and run identity,
    before cleanup and before the cycle binds and replaces it; the manifest digest in the final record covers it."""
    lab = native_lab
    seen = command_path(lab, monkeypatch)
    assert lab.module.main(CYCLE) == 0
    final = record(lab)
    _, retained = retained_runner_record(lab, final)
    assert retained == seen['runner_bytes'] and json.loads(retained)['status'] == 'PENDING_BINDING'
    assert final['status'] == 'PASS' and retained != (lab.tmp / 'evidence/deployment-e2e-run.json').read_bytes()
    assert final['native_lab']['retention']['status'] == 'RETAINED'  # the service logs, after a complete stop


def test_a_failed_cleanup_still_retains_the_runners_original_record(native_lab, monkeypatch):
    """Review of cea3412 (S1 gap): with the PostgreSQL stop failing, the original provisional bytes were overwritten
    by the final FAIL and never retained. They are retained now; the outcome stays FAIL (exit 5), nothing is wiped and
    no service-log snapshot is taken of a lab that did not stop."""
    lab = native_lab
    seen = command_path(lab, monkeypatch, stop_exit=1)
    assert lab.module.main(CYCLE) == 5
    final = record(lab)
    _, retained = retained_runner_record(lab, final)
    assert retained == seen['runner_bytes'] and json.loads(retained)['status'] == 'PENDING_BINDING'
    assert final['status'] == 'FAIL' and final['native_lab']['cleanup_complete'] is False
    assert final['native_lab']['wipe'] is None and final['native_lab']['retention'] is None


def test_an_interrupted_run_still_retains_the_runners_record(native_lab, monkeypatch):
    lab = native_lab
    seen = command_path(lab, monkeypatch)
    seen['interrupt_after_runner'] = True
    assert lab.module.main(CYCLE) == 130
    final = record(lab)
    _, retained = retained_runner_record(lab, final)
    assert retained == seen['runner_bytes'] and final['status'] == 'FAIL' and final['reason'].startswith('interrupted')


def refuse_retention(lab, monkeypatch, labels):
    """lab_retention refuses these labels before copying a byte; others retain normally."""
    real = lab.module.preserve

    def preserve(root, run_id, label, sources):
        if label in labels:
            raise lab.module.RetentionError('synthetic refusal before any copy: ' + label)
        return real(root, run_id, label, sources)
    monkeypatch.setattr(lab.module, 'preserve', preserve)


def count_wipes(lab, monkeypatch):
    wipes, real = [], lab.module.wipe_lab

    def wipe_lab(*args):
        wipes.append(args)
        return real(*args)
    monkeypatch.setattr(lab.module, 'wipe_lab', wipe_lab)
    return wipes


@pytest.mark.parametrize('case, labels, stop_exit, interrupt, exit_code', [
    ('copy-refused-stop-ok', {'runner-record'}, 0, False, 5),
    ('all-retention-refused-stop-ok', {'runner-record', 'deployment-cycle'}, 0, False, 5),
    ('copy-refused-stop-failed', {'runner-record'}, 1, False, 5),
    ('copy-refused-interrupted', {'runner-record'}, 0, True, 130),
])
def test_an_original_that_could_not_be_retained_stays_the_canonical_record(native_lab, monkeypatch, capsys, case, labels, stop_exit,
                                                                           interrupt, exit_code):
    """Review of ad28093 (R3-RETENTION): with the runner-record copy refused before any byte, the final publication
    overwrote the only original and, with service logs retained, the lab was wiped. Now the original stays exactly as
    the runner wrote it, nothing is wiped, and the outcome goes to a separate terminal diagnostic bound to it."""
    import hashlib
    lab = native_lab
    seen = command_path(lab, monkeypatch, stop_exit=stop_exit)
    seen['interrupt_after_runner'] = interrupt
    refuse_retention(lab, monkeypatch, labels)
    wipes = count_wipes(lab, monkeypatch)
    assert lab.module.main(CYCLE) == exit_code
    canonical = (lab.tmp / 'evidence/deployment-e2e-run.json').read_bytes()
    assert canonical == seen['runner_bytes']  # byte for byte: size and digest follow
    assert json.loads(canonical)['status'] == 'PENDING_BINDING'
    assert not wipes and lab.module.STATE.exists()
    run_id = json.loads(capsys.readouterr().out.strip().splitlines()[-1])['run_id']
    diagnostic = json.loads(lab.module.diagnostic_path().read_text())
    assert diagnostic['kind'] == 'native_lab_terminal_diagnostic' and diagnostic['result'] == 'NOT_A_RESULT'
    assert diagnostic['status'] == 'FAIL' and diagnostic['reason'] == 'runner_record_retention_failed' and diagnostic['run_id'] == run_id
    assert diagnostic['canonical_record'] == {'path': 'evidence/deployment-e2e-run.json', 'left_unchanged': True, 'status': 'PENDING_BINDING',
                                              'bytes': len(seen['runner_bytes']), 'sha256': hashlib.sha256(seen['runner_bytes']).hexdigest()}
    assert diagnostic['exit_code'] == exit_code and 'error' in diagnostic['runner_record_retention']
    assert not (lab.tmp / 'build/private/retained-phases' / run_id / 'runner-record').exists()
    for value in (json.loads(canonical), diagnostic):
        with pytest.raises(ValueError):
            validate_report(copy.deepcopy(value), lab.tmp)


@pytest.mark.parametrize('failure', ['stop-exception', 'stop-refusal'])
def test_a_retained_original_survives_a_stop_that_raises_or_refuses(native_lab, monkeypatch, failure):
    lab = native_lab
    seen = command_path(lab, monkeypatch)

    def stop_lab(*args):
        if failure == 'stop-exception':
            raise RuntimeError('synthetic stop exception')
        raise SystemExit('synthetic stop refusal')
    monkeypatch.setattr(lab.module, 'stop_lab', stop_lab)
    wipes = count_wipes(lab, monkeypatch)
    assert lab.module.main(CYCLE) == 5
    final = record(lab)
    _, retained = retained_runner_record(lab, final)
    assert retained == seen['runner_bytes'] and final['status'] == 'FAIL' and not wipes
    assert not lab.module.diagnostic_path().exists()


def test_a_previous_runs_diagnostic_is_removed_when_the_next_run_starts(native_lab, monkeypatch):
    lab = native_lab
    command_path(lab, monkeypatch)
    lab.module.diagnostic_path().parent.mkdir(parents=True, exist_ok=True)
    lab.module.diagnostic_path().write_text('{"kind": "native_lab_terminal_diagnostic", "run_id": "previous"}\n')
    assert lab.module.main(CYCLE) == 0
    assert record(lab)['status'] == 'PASS' and not lab.module.diagnostic_path().exists()
