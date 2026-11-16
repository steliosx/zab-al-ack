"""Every action commit in the workflows and release templates is an inspected one.

Carried forward from PR #29 (tests/release/test_pin_consistency.py at 3e6fd20c).
Its pyproject-to-lock checks are covered by test_hashed_locks.py; these two
action checks were not enforced anywhere else.

Action commits are inspected once and recorded in docs/ci_reference_manifest.json,
docs/upstream-codeql-manifest.json and docs/upstream-attestation-manifest.json.
A workflow-only bump (what Dependabot's github-actions updates produce) leaves
those records describing a commit no longer in use, so it must fail here.
These are structural checks of the tree; they fetch nothing and prove no execution.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_FILES = (*sorted((ROOT / '.github/workflows').glob('*.yml')),
                  *sorted((ROOT / 'release').glob('*.yml.example')))

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
