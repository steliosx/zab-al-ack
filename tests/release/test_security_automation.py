"""CodeQL configuration preserves an explicit, narrow external upload boundary."""
import json
from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[2]


def workflow():
    return yaml.load((ROOT / '.github/workflows/codeql.yml').read_text(), Loader=yaml.BaseLoader)


def test_codeql_has_no_privileged_fork_trigger_or_implicit_enablement():
    value = workflow()
    assert set(value['on']) == {'pull_request', 'push', 'workflow_dispatch', 'schedule'}
    assert value['jobs']['analyze']['if'] == "${{ vars.ZABALACK_CODEQL_ENABLED == 'true' }}"
    assert value['env']['TZ'] == 'UTC'
    assert 'continue-on-error' not in value['jobs']['analyze']


def test_codeql_upload_permission_is_only_in_the_analysis_job():
    value = workflow()
    assert value['permissions'] == {'contents': 'read'}
    assert set(value['jobs']) == {'analyze'}
    job = value['jobs']['analyze']
    assert job['permissions'] == {'contents': 'read', 'security-events': 'write'}
    assert job['runs-on'] == 'ubuntu-24.04'
    assert 1 <= int(job['timeout-minutes']) <= 30


def test_codeql_pins_interpreted_languages_and_engine_without_building_project():
    value = workflow()
    job = value['jobs']['analyze']
    assert set(job['strategy']['matrix']['language']) == {'python', 'javascript-typescript'}
    steps = job['steps']
    checkout = next(step for step in steps if step.get('uses', '').startswith('actions/checkout@'))
    assert checkout['with'] == {'persist-credentials': 'false'}
    init = next(step for step in steps if step.get('uses', '').startswith('github/codeql-action/init@'))
    assert init['with'] == {
        'languages': '${{ matrix.language }}', 'build-mode': 'none',
        'tools': 'linked', 'queries': 'security-extended', 'debug': 'false',
    }
    run_steps = [step for step in steps if 'run' in step]
    assert len(run_steps) == 1
    assert run_steps[0]['run'] == 'test "$CODEQL_VERSION" = \'2.27.0\''
    assert run_steps[0]['env'] == {'CODEQL_VERSION': '${{ steps.codeql.outputs.codeql-version }}'}
    assert all('continue-on-error' not in step for step in steps)


def test_codeql_upload_waits_for_results_without_exporting_database_or_overriding_ref():
    analyze = next(step for step in workflow()['jobs']['analyze']['steps']
                   if step.get('uses', '').startswith('github/codeql-action/analyze@'))
    assert analyze['with'] == {
        'category': '/language:${{ matrix.language }}', 'upload': 'always',
        'upload-database': 'false', 'wait-for-processing': 'true',
    }


def test_codeql_actions_match_inspected_manifest_and_only_allowlisted_tools_execute():
    manifest = json.loads((ROOT / 'docs/upstream-codeql-manifest.json').read_text())
    assert manifest['commit'] == 'b96794f015dfd88f77b49b1c93e0fa7110f94c63'
    assert manifest['cli_version'] == '2.27.0'
    assert manifest['bundle_tag'] == 'codeql-bundle-v2.27.0'
    allowed = {
        'actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683',
        'github/codeql-action/init@' + manifest['commit'],
        'github/codeql-action/analyze@' + manifest['commit'],
    }
    actions = {step['uses'] for step in workflow()['jobs']['analyze']['steps'] if 'uses' in step}
    assert actions == allowed
    assert set(manifest['files']) >= {'init/action.yml', 'analyze/action.yml', 'src/defaults.json', 'src/setup-codeql.ts'}
    for name, digest in manifest['files'].items():
        assert not name.startswith('/') and '..' not in Path(name).parts
        assert re.fullmatch('[a-f0-9]{64}', digest)
    assert manifest['hosted_execution'] == 'NOT_RUN'


def test_codeql_does_not_request_secrets_or_hide_failed_analysis():
    text = (ROOT / '.github/workflows/codeql.yml').read_text()
    for forbidden in ('secrets.', 'pull_request_target', 'workflow_run:', 'id-token:',
                      'attestations:', 'packages:', 'skip-queries:', 'paths-ignore:',
                      'security-and-quality', 'autobuild', 'continue-on-error:'):
        assert forbidden not in text
