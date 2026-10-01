"""R33: the locks pin at or above the first patched version of each recorded dependency advisory.

A structural regression guard: a later lock refresh cannot silently return PyJWT or urllib3 to an affected version
in any lock that carries them, and neither package enters the runtime lock unnoticed (which would widen the advisories'
applicability to the shipped package). It fetches nothing and proves no exploitability or execution; the fresh
exact-lock pip-audit scan of the candidate is the advisory check.
"""
from __future__ import annotations

from pathlib import Path
import re

from packaging.version import Version
import pytest

ROOT = Path(__file__).resolve().parents[2]
PIN = re.compile(r'^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==([^\s;#\\]+)')


def pins(name: str) -> dict[str, str]:
    """Exact pins of a lock, hashed or not; hash and comment lines are skipped."""
    found = {}
    for raw in (ROOT / name).read_text().splitlines():
        match = PIN.match(raw)
        if match:
            found[re.sub(r'[-_.]+', '-', match.group(1)).lower()] = match.group(2)
    return found

# (advisory, package, first patched version), as recorded in docs/RELEASE_AMENDMENTS.md R30 and R33
ADVISORIES = (
    ('GHSA-w6j9-cwv2-h6wq', 'pyjwt', '2.14.0'),
    ('GHSA-42vr-xj54-vc7v', 'pyjwt', '2.15.0'),
    ('GHSA-x33g-cr3x-6449', 'pyjwt', '2.15.0'),
    ('GHSA-8988-9cw3-xx77', 'urllib3', '2.8.0'),
    ('GHSA-vxq7-64xx-v4gw', 'urllib3', '2.8.0'),
    ('GHSA-gh4c-6fx4-qh6g', 'urllib3', '2.8.0'),
)
LOCKS = ('requirements.lock', 'requirements-scanner.lock')


@pytest.mark.parametrize('advisory, package, patched', ADVISORIES, ids=[a[0] for a in ADVISORIES])
def test_every_lock_carrying_the_package_pins_at_least_the_first_patched_version(advisory, package, patched):
    carried = {name: lock[package] for name in LOCKS if package in (lock := pins(name))}
    assert carried, f'{package} is in no lock; {advisory} needs a fresh applicability decision'
    for name, version in carried.items():
        assert Version(version) >= Version(patched), f'{name} pins {package} {version}, affected by {advisory}'


def test_the_runtime_lock_carries_neither_advisory_package():
    assert pins('requirements-runtime.lock'), 'the runtime lock parsed to nothing'
    assert not set(pins('requirements-runtime.lock')) & {'pyjwt', 'urllib3'}
