"""CI coverage follows executable phase plans, never comments or quoted examples."""
from importlib import import_module
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
native_ci = import_module('native_ci')
run_reference_suite = import_module('run_reference_suite')

DATABASE_TEST_DIRS = ('tests/postgres', 'tests/alerta', 'tests/performance')


def pytest_targets(command):
    """Understand only the two explicit Python pytest command forms we run."""
    assert command[0] == sys.executable
    if command[1:3] == ['-m', 'pytest']:
        arguments = command[3:]
    else:
        assert command[1:7] == ['lab/pglite_exec.py', '--isolated-lab', '--', sys.executable, '-m', 'pytest']
        arguments = command[7:]
    return {arg for arg in arguments if arg.startswith('tests/')}


def listed_targets():
    targets = {target for group in native_ci.PHASES.values() for target in group}
    for _, _, command in run_reference_suite.phase_commands(True):
        targets.update(pytest_targets(command))
    return targets


def test_every_database_test_file_is_in_a_ci_suite():
    present = {p.relative_to(ROOT).as_posix() for directory in DATABASE_TEST_DIRS
               for p in (ROOT / directory).glob('test_*.py')}
    assert present
    assert not present - listed_targets()


def test_every_test_directory_is_named_by_a_ci_suite():
    directories = {p.parent.relative_to(ROOT).as_posix() for p in (ROOT / 'tests').glob('*/test_*.py')}
    assert directories
    assert not (directories - set(DATABASE_TEST_DIRS) - listed_targets())


@pytest.mark.parametrize('target', ['tests/postgres/test_source_binding.py',
                                  'tests/postgres/test_ambiguous_api_migration.py',
                                  'tests/postgres/test_background_result_fencing.py', 'tests/postgres/test_primary_retry.py', 'tests/postgres/test_reseal_boundary.py'])
def test_fencing_and_migration_cases_run_natively_and_on_wasm(target):
    assert target in native_ci.PHASES['postgres']
    wasm = next(command for name, _, command in run_reference_suite.phase_commands(True) if name == 'postgres-wasm')
    assert target in pytest_targets(wasm)
    assert 'postgres-wasm' not in {name for name, _, _ in run_reference_suite.phase_commands(False)}


@pytest.mark.parametrize('command', [
    ['echo', sys.executable, '-m', 'pytest', 'tests/postgres/test_fake.py'],
    [sys.executable, '-c', "print('tests/postgres/test_fake.py')"],
    [sys.executable, 'unrelated.py', '-m', 'pytest', 'tests/postgres/test_fake.py'],
])
def test_inert_command_text_cannot_satisfy_executable_coverage(command):
    with pytest.raises(AssertionError):
        pytest_targets(command)


def test_a_comment_or_step_description_is_not_a_selected_test():
    assert not pytest_targets([sys.executable, '-m', 'pytest', '# tests/postgres/test_fake.py'])
