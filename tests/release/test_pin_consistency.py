"""Every exact pin is repeated where it is enforced, and every action commit is an inspected one.

Dependabot edits only `pyproject.toml` (pip ecosystem) and `.github/workflows`
(github-actions ecosystem). The repository installs from the hand-refreshed
`requirements.lock`, so a bump that touches only `pyproject.toml` changes nothing
at runtime while claiming to; and `pip check` cannot report that drift, because it
ignores requirements carried under an extra marker or under `[build-system]`.
Action commits are inspected once and recorded in `docs/ci_reference_manifest.json`,
`docs/upstream-codeql-manifest.json` and `docs/upstream-attestation-manifest.json`;
the release templates under `release/` carry the same pins as the workflows. None
of that was test-enforced before, so a bump could leave the manifests, the
templates or the locks describing a commit or version no longer in use.

These are structural checks of the tree; they fetch nothing and prove no execution.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import tomllib

import yaml

ROOT = Path(__file__).resolve().parents[2]
EXACT_PIN = re.compile(r'^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*==\s*([^\s;#\\]+)')
WORKFLOW_FILES = (*sorted((ROOT / '.github/workflows').glob('*.yml')),
                 *sorted((ROOT / 'release').glob('*.yml.example')))


def canonical(name: str) -> str:
    return re.sub(r'[-_.]+', '-', name).lower()


def lock_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        match = EXACT_PIN.match(line)
        assert match, f'{path.name}: {raw!r} is not an exact == pin'
        name = canonical(match.group(1))
        assert name not in pins, f'{path.name}: {name} pinned twice'
        pins[name] = match.group(3)
    assert pins, path.name
    return pins


def pyproject_pins() -> dict[tuple[str, str], str]:
    data = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    groups = {'build-system.requires': data['build-system']['requires'],
              'project.dependencies': data['project']['dependencies']}
    for extra, requirements in data['project'].get('optional-dependencies', {}).items():
        groups[f'project.optional-dependencies.{extra}'] = requirements
    pins: dict[tuple[str, str], str] = {}
    for group, requirements in groups.items():
        assert requirements, group
        for requirement in requirements:
            match = EXACT_PIN.match(requirement)
            assert match, f'{group}: {requirement!r} is not an exact == pin'
            pins[(group, canonical(match.group(1)))] = match.group(3)
    return pins


def test_every_pyproject_pin_is_repeated_exactly_in_requirements_lock():
    """A pyproject-only bump (what Dependabot's pip updates produce) must fail here."""
    lock = lock_pins(ROOT / 'requirements.lock')
    pins = pyproject_pins()
    assert {group for group, _ in pins} >= {'build-system.requires', 'project.dependencies',
                                            'project.optional-dependencies.alerta',
                                            'project.optional-dependencies.test'}
    drift = {f'{group}: {name}': {'pyproject': version, 'requirements.lock': lock.get(name)}
             for (group, name), version in pins.items() if lock.get(name) != version}
    assert drift == {}, drift


def test_runtime_lock_repeats_the_installed_dependency_pins_and_is_a_subset_of_the_full_lock():
    runtime = lock_pins(ROOT / 'requirements-runtime.lock')
    full = lock_pins(ROOT / 'requirements.lock')
    for (group, name), version in pyproject_pins().items():
        if group == 'project.dependencies':
            assert runtime.get(name) == version, (name, version, runtime.get(name))
    disagreement = {name: (version, full.get(name)) for name, version in runtime.items() if full.get(name) != version}
    assert disagreement == {}, disagreement


def used_actions() -> dict[str, dict[str, list[str]]]:
    """repository -> commit -> where it is used, across workflows and release templates."""
    used: dict[str, dict[str, list[str]]] = {}
    for path in WORKFLOW_FILES:
        workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        for job_name, job in workflow['jobs'].items():
            for step in job.get('steps', []):
                uses = step.get('uses', '')
                if not uses or uses.startswith('./'):
                    continue
                reference, commit = uses.rsplit('@', 1)
                assert re.fullmatch('[0-9a-f]{40}', commit), f'{path.name}:{job_name}: {uses}'
                repository = '/'.join(reference.split('/')[:2])
                used.setdefault(repository, {}).setdefault(commit, []).append(
                    f'{path.relative_to(ROOT).as_posix()}:{job_name}')
    return used


def recorded_actions() -> dict[str, str]:
    """repository -> the single inspected commit the manifests record for it."""
    recorded: dict[str, str] = {}
    for entry in json.loads((ROOT / 'docs/ci_reference_manifest.json').read_text()):
        assert re.fullmatch('[0-9a-f]{40}', entry['commit']), entry
        assert re.fullmatch('[0-9a-f]{64}', entry['action_yml_sha256']), entry
        assert entry['action_bytes'] > 0, entry
        assert entry['source_url'] == (
            f"https://raw.githubusercontent.com/{entry['repository']}/{entry['commit']}/action.yml"), entry
        assert entry['repository'] not in recorded, f"{entry['repository']} recorded twice"
        recorded[entry['repository']] = entry['commit']
    codeql = json.loads((ROOT / 'docs/upstream-codeql-manifest.json').read_text())
    recorded['github/codeql-action'] = codeql['commit']
    attestation = json.loads((ROOT / 'docs/upstream-attestation-manifest.json').read_text())
    for component in attestation['components']:
        if component['name'].startswith('actions/'):
            recorded[component['name']] = component['commit']
    return recorded


def test_every_action_pin_in_workflows_and_release_templates_is_the_inspected_commit():
    """A workflow-only bump (what Dependabot's github-actions updates produce) must fail here."""
    used, recorded = used_actions(), recorded_actions()
    assert used and recorded
    unrecorded = {repository: sorted(commits) for repository, commits in used.items() if repository not in recorded}
    assert unrecorded == {}, f'action commits with no inspection record: {unrecorded}'
    drift = {repository: {commit: where for commit, where in commits.items() if commit != recorded[repository]}
             for repository, commits in used.items()}
    drift = {repository: commits for repository, commits in drift.items() if commits}
    assert drift == {}, f'pins that differ from the inspected commit: {drift}'


def test_ci_reference_manifest_records_only_actions_the_tree_uses():
    used = used_actions()
    stale = [entry['repository'] for entry in json.loads((ROOT / 'docs/ci_reference_manifest.json').read_text())
             if entry['repository'] not in used]
    assert stale == [], f'manifest entries no workflow or template uses: {stale}'
