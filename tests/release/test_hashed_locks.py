"""The application and runtime locks are hash-checked, and their readers accept the format."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PIN = re.compile(r'^([A-Za-z0-9_.\-]+)==(\S+) \\$')


def pins(name):
    found, current = {}, None
    for line in (ROOT / name).read_text().splitlines():
        match = PIN.match(line)
        if match:
            current = match.group(1).lower().replace('_', '-')
            found[current] = (match.group(2), set())
        elif line.startswith('    --hash=sha256:') and current:
            found[current][1].add(line.split()[0])
        elif line.strip() and not line.startswith('#'):
            raise AssertionError(f'{name}: unexpected line {line!r}')
    return found


def test_every_application_and_runtime_pin_carries_hashes():
    for name in ('requirements.lock', 'requirements-runtime.lock'):
        locked = pins(name)
        assert locked, name
        assert all(hashes for _, hashes in locked.values()), name


def test_the_runtime_lock_is_the_application_lock_restricted():
    application, runtime = pins('requirements.lock'), pins('requirements-runtime.lock')
    assert runtime and set(runtime) <= set(application)
    assert all(runtime[name] == application[name] for name in runtime)


def test_ci_and_ansible_install_the_locks_in_hash_checking_mode():
    for workflow in ('.github/workflows/ci.yml', '.github/workflows/deployment-e2e.yml'):
        text = (ROOT / workflow).read_text()
        assert 'pip install -r requirements.lock' not in text, workflow
        assert 'pip install --require-hashes -r requirements.lock' in text, workflow
    play = (ROOT / 'deploy/ansible/01_install_shadow.yml').read_text()
    assert '--require-hashes --no-index --find-links' in play


def test_the_dependency_inventory_reads_the_hashed_application_lock(monkeypatch):
    """generate() names exactly the pinned distributions, never a --hash line."""
    import sys
    sys.path.insert(0, str(ROOT / 'tools'))
    import dependency_inventory
    seen = []

    def record(name):
        seen.append(name)
        return None  # generate() then fails on the first use, after naming every pin

    monkeypatch.setattr(dependency_inventory.metadata, 'distribution', record)
    try:
        dependency_inventory.generate(ROOT)
    except AttributeError:
        pass
    application = {name.lower().replace('_', '-') for name in seen} & set(pins('requirements.lock'))
    assert not any(name.lstrip().startswith('-') or name != name.strip() for name in seen)
    assert application == set(pins('requirements.lock'))
