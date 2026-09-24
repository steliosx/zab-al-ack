"""interpreter_library_env (review of e8ef82b, F3): both copies, and the four minimal environments that use it.

The helper names the interpreter's own library directory in LD_LIBRARY_PATH only for its exact
libpythonX.Y.so.1.0, as a file. The caller tests capture the environment each real caller builds; with
ZABALACK_RELOCATABLE_PYTHON naming a relocatable interpreter (the x64 runner host's actions/setup-python
3.12.14), each captured environment also starts that interpreter and imports _ctypes.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import psycopg
import pytest

from lab import deployment_e2e_runtime as runtime
import run_deployment_e2e_local as local_e2e

ROOT = Path(__file__).resolve().parents[2]
COPIES = {'tools': local_e2e.interpreter_library_env, 'lab': runtime.interpreter_library_env}
RELOCATABLE = os.environ.get('ZABALACK_RELOCATABLE_PYTHON')
REAL_RUN = subprocess.run  # the callers' subprocess.run is replaced while their environment is captured


class Captured(Exception):
    pass


def prefix(root: Path, name='x64', interpreter='python3.12') -> tuple[Path, Path]:
    """An actions/setup-python-shaped prefix and a venv whose python links to its interpreter."""
    base = root / 'toolcache/Python/3.12.14' / name
    (base / 'bin').mkdir(parents=True)
    (base / 'lib').mkdir()
    (base / 'bin' / interpreter).write_text('#!/bin/sh\nexit 1\n')
    (base / 'bin' / interpreter).chmod(0o755)
    venv = root / ('venv-' + name.replace('/', '_'))
    (venv / 'bin').mkdir(parents=True)
    (venv / 'bin/python').symlink_to(base / 'bin' / interpreter)
    return venv / 'bin/python', base / 'lib'


@pytest.mark.parametrize('copy', sorted(COPIES))
@pytest.mark.parametrize('case, expected', [
    ('own-library-file', True),
    ('own-library-link-to-file', True),
    ('library-directory-is-a-link', True),
    ('wrong-version', False),
    ('stable-abi-only', False),
    ('not-a-file', False),
    ('dangling-link', False),
    ('colon-in-path', False),
    ('semicolon-in-path', False),
    ('dollar-in-path', False),
    ('unversioned-interpreter-name', False),
])
def test_only_the_interpreters_own_library_file_is_named(tmp_path, copy, case, expected):
    name = {'colon-in-path': 'x:64', 'semicolon-in-path': 'x;64', 'dollar-in-path': '$ORIGIN'}.get(case, 'x64')
    python, library = prefix(tmp_path, name, 'python' if case == 'unversioned-interpreter-name' else 'python3.12')
    own = library / 'libpython3.12.so.1.0'
    if case == 'own-library-link-to-file':
        (library / 'libpython3.12.so.1.0.real').write_bytes(b'ELF')
        own.symlink_to('libpython3.12.so.1.0.real')
    elif case == 'library-directory-is-a-link':
        real = tmp_path / 'real-lib'
        real.mkdir()
        (real / 'libpython3.12.so.1.0').write_bytes(b'ELF')
        library.rmdir()
        library.symlink_to(real)
    elif case == 'wrong-version':
        (library / 'libpython3.11.so.1.0').write_bytes(b'ELF')
    elif case == 'stable-abi-only':
        (library / 'libpython3.so').write_bytes(b'ELF')
    elif case == 'not-a-file':
        own.mkdir()
    elif case == 'dangling-link':
        own.symlink_to(tmp_path / 'absent.so')
    else:
        own.write_bytes(b'ELF')
    assert COPIES[copy](str(python)) == ({'LD_LIBRARY_PATH': str(library)} if expected else {})


def test_the_two_copies_are_identical():
    """tools/ and lab/ run in different interpreters; each carries the helper."""
    import ast
    sources = [(ROOT / path).read_text() for path in ('tools/run_deployment_e2e_local.py', 'lab/deployment_e2e_runtime.py')]
    copies = [next(ast.get_source_segment(source, node) for node in ast.parse(source).body
                   if isinstance(node, ast.FunctionDef) and node.name == 'interpreter_library_env') for source in sources]
    assert copies[0] == copies[1]


@pytest.fixture
def interpreter(tmp_path):
    """(python for the callers, expected LD_LIBRARY_PATH): the host's relocatable interpreter, else a synthetic one."""
    if RELOCATABLE:
        python = Path(RELOCATABLE)
        library = Path(os.path.realpath(python)).parent.parent / 'lib'
        assert (library / 'libpython{}.{}.so.1.0'.format(*Path(os.path.realpath(python)).name[6:].split('.'))).is_file()
        return python, str(library)
    python, library = prefix(tmp_path)
    (library / 'libpython3.12.so.1.0').write_bytes(b'ELF')
    return python, str(library)


def check_runner_env(python, tmp_path, monkeypatch):
    monkeypatch.setattr(local_e2e, 'ROOT', ROOT)

    def run(command, **kwargs):
        raise Captured(kwargs['env'])
    monkeypatch.setattr(local_e2e.subprocess, 'run', run)
    with pytest.raises(Captured) as captured:
        local_e2e.check_runner(python, tmp_path / 'absent.whl', 'a' * 64)
    return captured.value.args[0]


def pytest_child_env(python, tmp_path, monkeypatch):
    monkeypatch.setattr(local_e2e, 'ROOT', tmp_path)
    monkeypatch.setattr(local_e2e, 'code_digests', lambda root: {'source': 'x', 'harness': 'y'})
    (tmp_path / 'evidence').mkdir()
    services = tmp_path / 'services.json'
    services.write_text(json.dumps({name: 'synthetic ' + name for name in local_e2e.IMAGE_COMPONENTS}))
    wheel = tmp_path / 'zabalack-0.1.0a0-py3-none-any.whl'
    wheel.write_bytes(b'synthetic')
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    for key, value in [('PRIVATE', private), ('check_host_layout', lambda: None), ('bootstrap', lambda project: None),
                       ('check_runner', lambda python, path, expected: {'python': str(python), 'version': '0.1.0a0', 'wheel_sha256': expected}),
                       ('clean_private', lambda: None), ('lab_ports_free', lambda: True)]:
        monkeypatch.setattr(local_e2e, key, value)
    seen = []

    def run_child(command, **kwargs):
        assert command[1:3] == ['-m', 'pytest']
        seen.append(kwargs['env'])
        return 1
    monkeypatch.setattr(local_e2e, 'run_child', run_child)
    local_e2e.main(['--isolated-lab', '--runner-python', os.fspath(python), '--wheel', str(wheel), '--services-file', str(services)])
    (env,) = seen
    return env


def service_child_env(python, tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.sys, 'executable', os.fspath(python))
    monkeypatch.setattr(runtime, 'PRIVATE', tmp_path)

    def popen(command, **kwargs):
        raise Captured(kwargs['env'])
    monkeypatch.setattr(runtime.subprocess, 'Popen', popen)
    with pytest.raises(Captured) as captured:
        with runtime.child([os.fspath(python), '-m', 'alerta'], 'alerta'):
            pass
    return captured.value.args[0]


def migration_env(python, tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.sys, 'executable', os.fspath(python))
    monkeypatch.setattr(runtime, 'PRIVATE', tmp_path)
    monkeypatch.setattr(runtime, 'ROOT', ROOT)
    (tmp_path / 'pg-password').write_text('synthetic')

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, query, *args):
            return type('Result', (), {'fetchone': lambda self: ('alerta', 'lab_admin', None, None)})()
    monkeypatch.setattr(psycopg, 'connect', lambda *args, **kwargs: Connection())

    def run(command, **kwargs):
        assert command[1] == 'sql/migrate.py'
        raise Captured(kwargs['env'])
    monkeypatch.setattr(runtime.subprocess, 'run', run)
    with pytest.raises(Captured) as captured:
        runtime.provision_database({name: 'synthetic' for name in ('alerta_app', 'zabalack_worker', 'zabalack_probe',
                                                                   'zabalack_operator', 'migrator')})
    return captured.value.args[0]


CALLERS = {'runner-check': check_runner_env, 'runner-pytest': pytest_child_env,
           'runtime-service-child': service_child_env, 'runtime-migration': migration_env}


@pytest.mark.parametrize('caller', sorted(CALLERS))
def test_each_minimal_environment_starts_the_interpreter_with_its_own_libpython(interpreter, tmp_path, monkeypatch, caller):
    python, library = interpreter
    monkeypatch.setenv('LD_LIBRARY_PATH', '/caller/lib')  # never carried over
    env = CALLERS[caller](python, tmp_path, monkeypatch)
    assert env['LD_LIBRARY_PATH'] == library
    if not RELOCATABLE:
        return
    # The real interpreter, started with exactly the caller's environment, imports _ctypes.
    started = REAL_RUN([os.fspath(python), '-c', 'import _ctypes'], env=env, capture_output=True, text=True, timeout=60)
    assert started.returncode == 0, started.stderr
    # And the same environment without the variable is the failure the helper exists for (on a host whose
    # system libpython differs from the interpreter's; recorded, not required, elsewhere).
    bare = REAL_RUN([os.fspath(python), '-c', 'import _ctypes'], env={k: v for k, v in env.items() if k != 'LD_LIBRARY_PATH'},
                          capture_output=True, text=True, timeout=60)
    print(f'{caller}: with LD_LIBRARY_PATH exit {started.returncode}; without exit {bare.returncode} '
          f'{bare.stderr.strip().splitlines()[-1] if bare.returncode else ""}', file=sys.stderr)
