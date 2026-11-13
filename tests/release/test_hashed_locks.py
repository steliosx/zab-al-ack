"""The application and runtime locks are hash-checked, and their readers accept the format."""
import re
import shlex
import sys

import pytest
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
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


def test_every_exact_pin_in_pyproject_equals_the_application_lock():
    """A bump of pyproject.toml alone leaves the hash lock installing the old
    file. `pip check` does not see that: it never reads the build-system pins,
    and CI installs the project with --no-deps."""
    import tomllib
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    declared = [*project['build-system']['requires'], *project['project']['dependencies'],
                *(pin for group in project['project']['optional-dependencies'].values() for pin in group)]
    locked = pins('requirements.lock')
    assert len(declared) >= 9
    for requirement in declared:
        match = re.fullmatch(r'([A-Za-z0-9_.-]+)(?:\[[a-z,]+\])?==(\S+)', requirement)
        assert match, requirement
        assert locked[match.group(1).lower().replace('_', '-')][0] == match.group(2), requirement


def test_the_mutation_runner_pins_the_locked_pytest(monkeypatch):
    import importlib.metadata
    import run_mutation_checks as runner
    locked = pins('requirements.lock')
    namespace = {}
    exec(compile(runner._GUARD, 'mutation-test-guard', 'exec'), namespace)
    check = namespace['pytest_sessionstart']
    monkeypatch.setattr(pytest, '__version__', locked['pytest'][0])
    monkeypatch.setattr(importlib.metadata, 'version', lambda name: locked['pytest-asyncio'][0])
    check(None)
    monkeypatch.setattr(pytest, '__version__', '0.0.0')
    with pytest.raises(pytest.UsageError, match='mutation_pytest_version_mismatch'):
        check(None)
    monkeypatch.setattr(pytest, '__version__', locked['pytest'][0])
    monkeypatch.setattr(importlib.metadata, 'version', lambda name: '0.0.0')
    with pytest.raises(pytest.UsageError, match='mutation_pytest_version_mismatch'):
        check(None)


def require_hashed_installs(workflow):
    """Require real unconditional install steps in every job, not inert YAML text.

    Only the current simple command form is supported; compound shell scripts
    need explicit review instead of a substring pretending to prove execution.
    """
    for name, job in workflow['jobs'].items():
        installs = []
        for step in job.get('steps', []):
            command = shlex.split(step.get('run', ''), comments=True)
            if command[:4] == ['python', '-m', 'pip', 'install'] and 'requirements.lock' in command:
                assert command == ['python', '-m', 'pip', 'install', '--require-hashes', '-r', 'requirements.lock'], name
                assert len(step['run'].strip().splitlines()) == 1, name
                assert 'if' not in step and 'continue-on-error' not in step, name
                installs.append(step)
        assert installs, name


def test_ci_and_ansible_install_the_locks_in_hash_checking_mode():
    for workflow in ('.github/workflows/ci.yml', '.github/workflows/deployment-e2e.yml'):
        require_hashed_installs(yaml.safe_load((ROOT / workflow).read_text()))
    plays = yaml.safe_load((ROOT / 'deploy/ansible/01_install_shadow.yml').read_text())
    installers = [task['ansible.builtin.pip'] for play in plays for task in play['tasks']
                  if task.get('ansible.builtin.pip', {}).get('requirements') == '{{ zabalack_root }}/requirements-runtime.lock']
    assert len(installers) == 1
    args = installers[0]['extra_args'].split()
    assert all(flag in args for flag in ('--require-hashes', '--no-index', '--find-links'))


@pytest.mark.parametrize('step', [
    {'run': '# python -m pip install --require-hashes -r requirements.lock'},
    {'run': 'echo python -m pip install --require-hashes -r requirements.lock'},
    {'name': 'python -m pip install --require-hashes -r requirements.lock', 'run': 'true'},
    {'run': 'python -m pip install -r requirements.lock # --require-hashes'},
    {'run': 'python -m pip install --require-hashes -r requirements.lock', 'if': False},
    {'run': 'python -m pip install --require-hashes -r requirements.lock --help'},
    {'run': 'python -m pip install --require-hashes -r requirements.lock; true'},
    {'run': 'python -m pip install --require-hashes -r requirements.lock', 'continue-on-error': True},
])
def test_inert_or_optional_install_text_cannot_satisfy_guard(step):
    with pytest.raises(AssertionError):
        require_hashed_installs({'jobs': {'test': {'steps': [step]}}})


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
