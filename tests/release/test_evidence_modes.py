"""Every evidence writer states the mode its readers need, whatever the umask.

Adopted from the independent review of 0.1.0a15, which added the real-Zabbix
recorder, the shared review writer, both umasks and replacement of an existing
restrictive file to the two regressions a15 shipped. Only synthetic JSON is
written, under pytest's temporary directory; no service, token or candidate
evidence is touched.
"""
import json
import os
from pathlib import Path
import stat
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import record_real_zabbix  # noqa: E402
import review_runs  # noqa: E402
import run_mutation_checks  # noqa: E402

PUBLIC_WRITERS = {
    'mutation': run_mutation_checks._atomic_json,
    'real_zabbix': record_real_zabbix.write_record,
}
PRIVATE_WRITERS = {
    'mutation': run_mutation_checks._atomic_json,
    'review': review_runs.atomic_json,
}


@pytest.fixture
def umask(request):
    previous = os.umask(request.param)
    try:
        yield request.param
    finally:
        os.umask(previous)


@pytest.mark.parametrize('umask', [0o000, 0o077], indirect=True)
@pytest.mark.parametrize('writer', sorted(PUBLIC_WRITERS))
def test_public_record_is_readable_after_atomic_replace(tmp_path, umask, writer):
    """mkstemp's 0600 must not become the mode of a published record."""
    write = PUBLIC_WRITERS[writer]
    target = tmp_path / 'public.json'
    payload = {'status': 'SYNTHETIC_REVIEW_RECORD'}
    for existing_mode in (None, 0o600):
        if existing_mode is not None:
            target.chmod(existing_mode)
        write(target, payload)
        assert json.loads(target.read_text()) == payload
        assert stat.S_IMODE(target.stat().st_mode) == 0o644


@pytest.mark.parametrize('umask', [0o000, 0o077], indirect=True)
@pytest.mark.parametrize('writer', sorted(PRIVATE_WRITERS))
def test_private_diagnostic_stays_private(tmp_path, umask, writer):
    """A caller asking for 0600 gets 0600, including under a permissive umask."""
    target = tmp_path / 'private.json'
    PRIVATE_WRITERS[writer](target, {'kind': 'synthetic private diagnostic'}, mode=0o600)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.parametrize('umask', [0o000, 0o077], indirect=True)
def test_shared_writer_can_explicitly_publish(tmp_path, umask):
    """The private-by-default review writer publishes when the caller says so."""
    target = tmp_path / 'explicit-public.json'
    review_runs.atomic_json(target, {'kind': 'synthetic public diagnostic'}, mode=0o644)
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
