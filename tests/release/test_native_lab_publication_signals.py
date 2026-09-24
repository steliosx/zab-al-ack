"""Review of e8ef82b (F1): real signals at the final publication boundary, and the actual CLI exit.

Each case runs tests/release/native_lab_signal_harness.py in its own process: the cycle command as its CLI runs
it (sys.exit(main(argv))), through the real runner and the real final validation, with a real signal sent at one
boundary. The exit status is the process's own. Before the publication (publish()'s last check, with the
termination signals blocked up to the atomic replace) a signal is an interrupted FAIL and exit 130; a death there
leaves the runner's non-validating PENDING_BINDING record. After the publication the published record stands; a
signal still exits 130 and says so, a death leaves the published record as it is. Never a traceback.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from deployment_evidence import validate_report

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / 'tests/release/native_lab_signal_harness.py'
AFTER = 'after the final record was published; that record stands'

# scenario: (exit status, record status, record reason, validates, stderr contains)
EXPECTED = {
    'none': (0, 'PASS', None, True, None),
    'term-after-cleanup': (130, 'FAIL', 'interrupted (SIGTERM)', False, None),
    'term-entering-publication': (130, 'FAIL', 'interrupted (SIGTERM)', False, None),
    'term-in-validation': (130, 'FAIL', 'interrupted (SIGTERM)', False, None),
    'int-in-validation': (130, 'FAIL', 'interrupted (SIGINT)', False, None),
    'kill-in-validation': (-9, 'PENDING_BINDING', None, False, None),
    'term-at-replace': (130, 'PASS', None, True, 'interrupted (SIGTERM) ' + AFTER),
    'term-after-publication': (130, 'PASS', None, True, 'interrupted (SIGTERM) ' + AFTER),
    'kill-after-publication': (-9, 'PASS', None, True, None),
}


@pytest.mark.parametrize('scenario', list(EXPECTED))
def test_a_signal_at_the_publication_boundary(tmp_path, scenario):
    exit_status, status, reason, validates, message = EXPECTED[scenario]
    result = subprocess.run([sys.executable, str(HARNESS), str(tmp_path), scenario], cwd=ROOT, capture_output=True, text=True,
                            timeout=120, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    record = json.loads((tmp_path / 'evidence/deployment-e2e-run.json').read_text())
    assert (result.returncode, record.get('status'), record.get('reason')) == (exit_status, status, reason), result.stderr[-2000:]
    assert 'Traceback' not in result.stderr
    if message:
        assert message in result.stderr
    else:
        assert AFTER not in result.stderr
    if validates:
        validate_report(record, tmp_path)  # the harness root links this checkout's digest scopes
    else:
        with pytest.raises(ValueError):
            validate_report(record, tmp_path)
    if status == 'FAIL':
        assert record['native_lab']['interrupted'] == reason and record['native_lab']['cleanup_complete'] is True
