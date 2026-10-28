"""Synthetic artifact-policy tests; no signing service or hosted CI is invoked."""
import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tarfile
from types import SimpleNamespace
import zipfile

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import collect_ci_evidence as ci
from evidence_utils import code_digests
from public_audit import public_files
from agent_guidance import inspect as inspect_guidance, CONTEXT as GUIDANCE_CONTEXT, SKILLS
import verify_attestation as signed
import verify_release as release
from tests.release.test_ci_collection import CONTEXT, PR_CONTEXT, create_reports

ROOT = Path(__file__).resolve().parents[2]


def write_zip(path, rows):
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in rows.items():
            archive.writestr(name, data)


VERSION = '0.0.0a7'


def build_fixture(root, candidate, version=VERSION):
    """Construct consistent inert archives, not an executable release build."""
    dist = candidate / 'dist'
    (dist / 'pypi').mkdir(parents=True, exist_ok=True)
    source = {p.relative_to(root).as_posix(): p.read_bytes() for p in public_files(root)}
    write_zip(dist / f'zabalack-{version}-source.zip',
              {f'zabalack-{version}/' + name: data for name, data in source.items()})
    wheel = {name.removeprefix('src/'): data for name, data in source.items() if name.startswith('src/zabalack/')}
    prefix = f'zabalack-{version}.dist-info/'
    wheel.update({prefix + name: data for name, data in {
        'licenses/LICENSE': source['LICENSE'], 'licenses/NOTICE': source['NOTICE'],
        'METADATA': (f'Metadata-Version: 2.4\nName: zabalack\nVersion: {version}\nLicense-Expression: Apache-2.0\n'
                     'Requires-Python: <3.13,>=3.12\nRequires-Dist: httpx==0.28.1\n'
                     'Provides-Extra: test\nRequires-Dist: pytest==9.0.3; extra == "test"\n\nSynthetic\n').encode(),
        'WHEEL': b'Wheel-Version: 1.0\nGenerator: setuptools (84.1.0)\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
        'entry_points.txt': b'[console_scripts]\nzabalack = zabalack.cli:main\n', 'top_level.txt': b'zabalack\n',
    }.items()})
    record = io.StringIO()
    writer = csv.writer(record, lineterminator='\n')
    for name, data in wheel.items():
        writer.writerow([name, 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('='), len(data)])
    writer.writerow([prefix + 'RECORD', '', ''])
    wheel[prefix + 'RECORD'] = record.getvalue().encode()
    write_zip(dist / f'pypi/zabalack-{version}-py3-none-any.whl', wheel)
    sdist = dict(source)
    sdist.update({'PKG-INFO': wheel[prefix + 'METADATA'],
        'setup.cfg': b'[egg_info]\ntag_build = \ntag_date = 0\n\n',
        'src/zabalack.egg-info/PKG-INFO': wheel[prefix + 'METADATA'],
        'src/zabalack.egg-info/entry_points.txt': wheel[prefix + 'entry_points.txt'],
        'src/zabalack.egg-info/top_level.txt': wheel[prefix + 'top_level.txt'],
        'src/zabalack.egg-info/dependency_links.txt': b'\n',
        'src/zabalack.egg-info/requires.txt': b'httpx==0.28.1\n\n[test]\npytest==9.0.3\n',
        'src/zabalack.egg-info/SOURCES.txt': b'',
    })
    sdist['src/zabalack.egg-info/SOURCES.txt'] = ('\n'.join(sorted(set(sdist) - {'PKG-INFO', 'setup.cfg'})) + '\n').encode()
    with tarfile.open(dist / f'pypi/zabalack-{version}.tar.gz', 'w:gz') as archive:
        for name, data in sdist.items():
            info = tarfile.TarInfo(f'zabalack-{version}/' + name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    comparisons = [{'kind': kind, 'artifact': name, 'sha256': release.sha((dist / name).read_bytes()),
                    'two_builds_byte_identical': True} for kind, name in {
        'wheel': f'pypi/zabalack-{version}-py3-none-any.whl',
        'normalised_sdist': f'pypi/zabalack-{version}.tar.gz',
        'source_zip': f'zabalack-{version}-source.zip',
    }.items()]
    (dist / 'BUILD_PROVENANCE.json').write_text(json.dumps({
        'version': version, 'code_digests': code_digests(root), 'agent_guidance': inspect_guidance(root), 'comparisons': comparisons,
        'signed_attestation': False, 'published': False,
    }))
    refresh_sums(dist)


def refresh_sums(dist):
    files = sorted(p for p in dist.rglob('*') if p.is_file() and p.name != 'SHA256SUMS')
    (dist / 'SHA256SUMS').write_text(''.join(release.sha(p.read_bytes()) + '  ' +
                                   p.relative_to(dist).as_posix() + '\n' for p in files))


def edit_json(path, change):
    value = json.loads(path.read_text())
    change(value)
    path.write_text(json.dumps(value))


@pytest.fixture
def candidate(tmp_path, request):
    return make_candidate(tmp_path, getattr(request, 'param', CONTEXT), VERSION)


@pytest.fixture
def beta_candidate(tmp_path):
    return make_candidate(tmp_path, CONTEXT, '0.0.0b7')


def make_candidate(tmp_path, context, version):
    root = tmp_path / 'checkout'
    (root / 'src/zabalack').mkdir(parents=True)
    (root / 'src/zabalack/__init__.py').write_text('# Synthetic policy fixture\n')
    (root / 'tests').mkdir()
    (root / 'tests/synthetic.py').write_text('# Synthetic harness\n')
    (root / 'pyproject.toml').write_text(f'[project]\nname = "zabalack"\nversion = "{version}"\n'
        'license = "Apache-2.0"\nrequires-python = ">=3.12,<3.13"\ndependencies = ["httpx==0.28.1"]\n'
        '[project.scripts]\nzabalack = "zabalack.cli:main"\n[project.optional-dependencies]\ntest = ["pytest==9.0.3"]\n')
    (root / 'LICENSE').write_text('Synthetic licence fixture\n')
    (root / 'NOTICE').write_text('Synthetic notice fixture\n')
    for name in (*GUIDANCE_CONTEXT, *(f'.agents/skills/{s}/SKILL.md' for s in SKILLS)):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    inputs = tmp_path / 'ci-inputs'
    for kind in ci.KINDS:
        ci.prepare(root, kind, context)
        create_reports(root, kind)
        shutil.copytree(ci.pack(root, kind, context), inputs / ci.artifact_name(kind, context))
    ci.collect(root, inputs, context)
    output = tmp_path / 'candidate'
    shutil.copytree(root / 'evidence', output / 'evidence')
    build_fixture(root, output, version)
    return root, output


def test_beta_candidate_verifies_like_an_alpha(beta_candidate):
    root, output = beta_candidate
    result = release.verify(root, output / 'dist', candidate=output, context=CONTEXT)
    assert result['status'] == 'PASS' and result['version'] == '0.0.0b7'
    assert {Path(item['path']).name for item in result['subjects']} >= {
        'zabalack-0.0.0b7-py3-none-any.whl', 'zabalack-0.0.0b7.tar.gz', 'zabalack-0.0.0b7-source.zip'}


@pytest.mark.parametrize('version', ['0.0.0', '0.0.0rc1', '0.0.0.dev1', '0.0.0b', 'v0.0.0b7', '0.0.0b7.post1'])
def test_non_alpha_beta_versions_are_rejected_before_any_archive_is_read(candidate, version):
    root, output = candidate
    (root / 'pyproject.toml').write_text(f'[project]\nname = "zabalack"\nversion = "{version}"\n')
    dist = output / 'dist'
    shutil.rmtree(dist)
    with pytest.raises(ci.EvidenceError, match='alpha or beta'):
        release.verify(root, dist, candidate=output, context=CONTEXT)


def test_current_ci_subjects_are_consistent_but_remain_unsigned(candidate):
    root, output = candidate
    result = release.verify(root, output / 'dist', candidate=output, context=CONTEXT)
    assert result['status'] == 'PASS' and result['signed_attestation'] is False
    assert result['scope'] == 'unsigned_release_consistency'
    assert len(result['subjects']) == 4
    stage = release.stage(root, output / 'dist', result)
    assert {path.name for path in stage.iterdir()} == {Path(row['path']).name for row in result['subjects']}
    (stage / 'unexpected').write_text('extra input')
    release.stage(root, output / 'dist', result)
    assert not (stage / 'unexpected').exists()


@pytest.mark.parametrize('candidate', [PR_CONTEXT], indirect=True)
def test_pr_metadata_survives_producers_collection_and_package_verification(candidate):
    root, output = candidate
    result = release.verify(root, output / 'dist', candidate=output, context=PR_CONTEXT)
    assert result['ci_context'] == PR_CONTEXT
    assert result['status'] == 'PASS' and result['signed_attestation'] is False


@pytest.mark.parametrize('candidate', [PR_CONTEXT], indirect=True)
@pytest.mark.parametrize('field', ['number', 'head_sha', 'base_sha', 'omit'])
def test_package_verification_rejects_different_or_omitted_pr_identity(candidate, field):
    root, output = candidate
    context = json.loads(json.dumps(PR_CONTEXT))
    if field == 'omit':
        del context['pull_request']
    else:
        context['pull_request'][field] = 18 if field == 'number' else 'e' * 40
    with pytest.raises(ci.EvidenceError, match='foreign'):
        release.verify(root, output / 'dist', candidate=output, context=context)


@pytest.mark.parametrize('candidate,stored_number', [
    (PR_CONTEXT, 17.0),
    (dict(PR_CONTEXT, pull_request=dict(PR_CONTEXT['pull_request'], number=1)), True),
], indirect=['candidate'], ids=['float', 'boolean'])
def test_package_rejects_equal_but_invalid_saved_pr_number(candidate, stored_number):
    root, output = candidate
    context = ci.read_json(output / 'evidence/ci-collection.json')['context']
    for directory in (root, output):
        edit_json(directory / 'evidence/ci-collection.json',
                  lambda value: value['context']['pull_request'].update(number=stored_number))
    # Keep source/candidate evidence and all archive checksums consistent, so the
    # rejection proves stored-context validation rather than a stale checksum.
    build_fixture(root, output)
    assert ci.read_json(output / 'evidence/ci-collection.json')['context'] == context
    with pytest.raises(ci.EvidenceError, match='Invalid pull request number'):
        release.verify(root, output / 'dist', candidate=output, context=context)


def test_historical_package_cannot_satisfy_a_current_pr_context(candidate):
    root, output = candidate
    with pytest.raises(ci.EvidenceError, match='foreign'):
        release.verify(root, output / 'dist', candidate=output, context=PR_CONTEXT)


@pytest.mark.parametrize('field,value', [('repository', 'other/repo'), ('sha', 'b' * 40),
                                        ('run_id', '124'), ('run_attempt', '3')])
def test_foreign_ci_context_cannot_be_staged(candidate, field, value):
    root, output = candidate
    context = dict(CONTEXT, **{field: value})
    with pytest.raises(ci.EvidenceError, match='foreign'):
        release.verify(root, output / 'dist', candidate=output, context=context)


@pytest.mark.parametrize('mutation', ['subject', 'duplicate_sum', 'unknown_sum', 'missing_sum',
                                    'false_signed_claim', 'stale_comparison', 'changed_harness'])
def test_release_integrity_failures_are_rejected(candidate, mutation):
    root, output = candidate
    dist = output / 'dist'
    if mutation == 'subject':
        next((dist / 'pypi').glob('*.whl')).write_bytes(b'changed')
    elif mutation in {'duplicate_sum', 'unknown_sum', 'missing_sum'}:
        path = dist / 'SHA256SUMS'
        lines = path.read_text().splitlines()
        path.write_text('\n'.join(lines + [lines[0]] if mutation == 'duplicate_sum' else
                       lines + ['0' * 64 + '  ../outside'] if mutation == 'unknown_sum' else lines[:-1]) + '\n')
    elif mutation == 'false_signed_claim':
        edit_json(dist / 'BUILD_PROVENANCE.json', lambda value: value.update(signed_attestation=True))
        refresh_sums(dist)
    elif mutation == 'stale_comparison':
        edit_json(dist / 'BUILD_PROVENANCE.json', lambda value: value['comparisons'][0].update(sha256='0' * 64))
        refresh_sums(dist)
    else:
        (root / 'tests/synthetic.py').write_text('# changed\n')
    with pytest.raises(ci.EvidenceError):
        release.verify(root, dist, candidate=output, context=CONTEXT)


def refresh_comparison(dist, kind):
    def update(value):
        row = next(row for row in value['comparisons'] if row['kind'] == kind)
        row['sha256'] = release.sha((dist / row['artifact']).read_bytes())
    edit_json(dist / 'BUILD_PROVENANCE.json', update)
    refresh_sums(dist)


@pytest.mark.parametrize('mutation', ['entrypoint', 'dependency', 'python', 'name', 'version',
    'new_metadata_file', 'record', 'purelib', 'extra_import', 'changed_code'])
def test_rehashed_wheel_cannot_change_install_behavior(candidate, mutation):
    root, output = candidate
    dist = output / 'dist'
    path = next((dist / 'pypi').glob('*.whl'))
    wheel = release.zip_contents(path.read_bytes())
    prefix = 'zabalack-0.0.0a7.dist-info/'
    if mutation == 'entrypoint':
        wheel[prefix + 'entry_points.txt'] = b'[console_scripts]\nzabalack = http.server:test\n'
    elif mutation in {'dependency', 'python', 'name', 'version'}:
        original, replacement = {
            'dependency': (b'httpx==0.28.1', b'foreign-package==1.0'),
            'python': (b'<3.13,>=3.12', b'>=3.5'),
            'name': (b'Name: zabalack', b'Name: foreign'),
            'version': (b'Version: 0.0.0a7', b'Version: 9.0.0'),
        }[mutation]
        wheel[prefix + 'METADATA'] = wheel[prefix + 'METADATA'].replace(original, replacement)
    elif mutation == 'new_metadata_file':
        wheel[prefix + 'hook.py'] = b'print("unexpected executable")\n'
    elif mutation == 'record':
        wheel[prefix + 'RECORD'] = b''
    elif mutation == 'purelib':
        wheel[prefix + 'WHEEL'] = wheel[prefix + 'WHEEL'].replace(b'Root-Is-Purelib: true', b'Root-Is-Purelib: false')
    elif mutation == 'extra_import':
        wheel['sitecustomize.py'] = b'# Unexpected startup path\n'
    else:
        wheel['zabalack/__init__.py'] = b'# Changed product bytes\n'
    write_zip(path, wheel)
    refresh_comparison(dist, 'wheel')
    with pytest.raises(ci.EvidenceError):
        release.verify(root, dist, candidate=output, context=CONTEXT)


@pytest.mark.parametrize('kind', ['source_zip', 'normalised_sdist'])
def test_rehashed_source_archives_cannot_change_reviewed_code(candidate, kind):
    root, output = candidate
    dist = output / 'dist'
    prefix = 'zabalack-0.0.0a7/'
    if kind == 'source_zip':
        path = next(dist.glob('*-source.zip'))
        data = release.zip_contents(path.read_bytes())
        data[prefix + 'src/zabalack/__init__.py'] = b'# changed\n'
        write_zip(path, data)
    else:
        path = next((dist / 'pypi').glob('*.tar.gz'))
        data = release.tar_contents(path.read_bytes())
        data[prefix + 'src/zabalack/__init__.py'] = b'# changed\n'
        with tarfile.open(path, 'w:gz') as archive:
            for name, content in data.items():
                item = tarfile.TarInfo(name)
                item.size = len(content)
                archive.addfile(item, io.BytesIO(content))
    refresh_comparison(dist, kind)
    with pytest.raises(ci.EvidenceError):
        release.verify(root, dist, candidate=output, context=CONTEXT)


@pytest.mark.parametrize('name', ['PKG-INFO', 'src/zabalack.egg-info/requires.txt',
                                'src/zabalack.egg-info/entry_points.txt', 'src/zabalack.egg-info/SOURCES.txt'])
def test_rehashed_sdist_install_metadata_is_checked(candidate, name):
    root, output = candidate
    dist = output / 'dist'
    path = next((dist / 'pypi').glob('*.tar.gz'))
    data = release.tar_contents(path.read_bytes())
    data['zabalack-0.0.0a7/' + name] += b'unexpected metadata\n'
    with tarfile.open(path, 'w:gz') as archive:
        for key, content in data.items():
            item = tarfile.TarInfo(key)
            item.size = len(content)
            archive.addfile(item, io.BytesIO(content))
    refresh_comparison(dist, 'normalised_sdist')
    with pytest.raises(ci.EvidenceError):
        release.verify(root, dist, candidate=output, context=CONTEXT)


@pytest.mark.parametrize('mutation', ['reference_failure', 'native_skip', 'ci_report_hash',
                                    'extra_evidence', 'old_ci_copy', 'missing_kind'])
def test_current_ci_validation_does_not_trust_manifest_alone(candidate, mutation):
    root, output = candidate
    if mutation == 'reference_failure':
        edit_json(output / 'evidence/reference-run.json', lambda value: value.update(all_executed_phases_succeeded=False))
    elif mutation == 'native_skip':
        path = next((output / 'evidence').glob('native-*.xml'))
        path.write_text(path.read_text().replace('</testcase>', '<skipped /></testcase>'))
    elif mutation == 'ci_report_hash':
        edit_json(output / 'evidence/ci-collection.json', lambda value: value['files'][0].update(sha256='0' * 64))
    elif mutation == 'extra_evidence':
        (output / 'evidence/old.json').write_text('{}')
    elif mutation == 'old_ci_copy':
        edit_json(output / 'evidence/ci-collection.json', lambda value: value.update(collected_at='other'))
    else:
        edit_json(output / 'evidence/ci-collection.json', lambda value: value['kinds'].pop())
    with pytest.raises(ci.EvidenceError):
        release.verify(root, output / 'dist', candidate=output, context=CONTEXT)


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'a//b', 'a/./b', 'a\\b'])
def test_archive_paths_are_rejected_without_extraction(name):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr(name, b'data')
    with pytest.raises(ci.EvidenceError):
        release.zip_contents(data.getvalue())


def test_symlink_and_duplicate_archive_members_are_rejected():
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        item = zipfile.ZipInfo('link')
        item.external_attr = 0o120777 << 16
        archive.writestr(item, b'target')
    with pytest.raises(ci.EvidenceError, match='Non-regular'):
        release.zip_contents(data.getvalue())
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w:gz') as archive:
        for _ in range(2):
            item = tarfile.TarInfo('duplicate')
            archive.addfile(item, io.BytesIO(b''))
    with pytest.raises(ci.EvidenceError, match='Duplicate'):
        release.tar_contents(data.getvalue())


@pytest.fixture
def verifier(tmp_path, monkeypatch):
    path = tmp_path / 'synthetic.whl'
    path.write_bytes(b'unit verifier fixture')
    invocations = []
    response = [{'verificationResult': {'statement': {
        'predicateType': signed.PREDICATE,
        'subject': [{'name': path.name, 'digest': {'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}}],
    }}}]
    def run(command, **kwargs):
        invocations.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='gh version 2.83.2 (synthetic)\n' if command[1] == '--version'
                               else json.dumps(response))
    monkeypatch.setattr(signed.subprocess, 'run', run)
    return path, invocations, response


def test_owned_verifier_process_enforces_certificate_identity_and_digest(verifier):
    path, calls, response = verifier
    result = signed.verify(path, 'synthetic/zabalack', 'refs/tags/v0.0.0a7', 'a' * 40)
    command, options = calls[1]
    assert command[0:3] == ['gh', 'attestation', 'verify']
    for flag, value in {'--repo': 'synthetic/zabalack', '--hostname': 'github.com',
        '--cert-identity': 'https://github.com/synthetic/zabalack/.github/workflows/attest.yml@refs/tags/v0.0.0a7',
        '--source-ref': 'refs/tags/v0.0.0a7', '--source-digest': 'a' * 40,
        '--signer-digest': 'a' * 40, '--predicate-type': signed.PREDICATE}.items():
        assert command[command.index(flag) + 1] == value
    assert '--deny-self-hosted-runners' in command and '--signer-workflow' not in command
    assert not options.get('shell', False)
    assert options['env']['GH_HOST'] == 'github.com'
    assert result['deployment_gates'] == 'MANUAL_GATE_PENDING'


@pytest.mark.parametrize('mutation', ['empty', 'wrong_digest', 'wrong_predicate', 'malformed_result',
                                    'malformed_statement', 'malformed_subjects'])
def test_success_exit_without_matching_verified_statement_is_refused(verifier, mutation):
    path, calls, response = verifier
    if mutation == 'empty':
        response.clear()
    elif mutation == 'wrong_digest':
        response[0]['verificationResult']['statement']['subject'][0]['digest']['sha256'] = '0' * 64
    elif mutation == 'wrong_predicate':
        response[0]['verificationResult']['statement']['predicateType'] = 'synthetic/untrusted'
    elif mutation == 'malformed_result':
        response[0]['verificationResult'] = []
    elif mutation == 'malformed_statement':
        response[0]['verificationResult']['statement'] = 'malformed'
    else:
        response[0]['verificationResult']['statement']['subject'] = None
    with pytest.raises(ci.EvidenceError):
        signed.verify(path, 'synthetic/zabalack', 'refs/tags/v0.0.0a7', 'a' * 40)


def test_verifier_failure_never_becomes_success(verifier, monkeypatch):
    path, calls, response = verifier
    monkeypatch.setattr(signed.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1, stdout=''))
    with pytest.raises(ci.EvidenceError):
        signed.verify(path, 'synthetic/zabalack', 'refs/tags/v0.0.0a7', 'a' * 40)


@pytest.mark.parametrize('source_ref', ['refs/heads/main', 'refs/tags/v0.0.0', 'refs/tags/v0.0.0rc1',
                                        'refs/tags/0.0.0b7', 'refs/tags/v0.0.0b7-dirty'])
def test_invalid_policy_is_rejected_before_verifier_access(verifier, source_ref):
    path, calls, response = verifier
    with pytest.raises(ci.EvidenceError):
        signed.verify(path, 'synthetic/zabalack', source_ref, 'a' * 40)
    assert calls == []


def test_reviewed_beta_tag_satisfies_the_tag_policy(verifier):
    path, calls, response = verifier
    result = signed.verify(path, 'synthetic/zabalack', 'refs/tags/v0.0.0b7', 'a' * 40)
    command, _ = calls[1]
    assert command[command.index('--source-ref') + 1] == 'refs/tags/v0.0.0b7'
    assert command[command.index('--cert-identity') + 1].endswith('@refs/tags/v0.0.0b7')
    assert result['deployment_gates'] == 'MANUAL_GATE_PENDING'


def test_subject_change_during_verification_is_rejected(verifier, monkeypatch):
    path, calls, response = verifier
    original = signed.subprocess.run
    def changing_run(command, **kwargs):
        value = original(command, **kwargs)
        if command[1] != '--version':
            path.write_bytes(b'changed during verifier')
        return value
    monkeypatch.setattr(signed.subprocess, 'run', changing_run)
    with pytest.raises(ci.EvidenceError, match='changed'):
        signed.verify(path, 'synthetic/zabalack', 'refs/tags/v0.0.0a7', 'a' * 40)


def test_attestation_template_is_inactive_deliberate_and_current_run_only():
    assert not (ROOT / '.github/workflows/attest.yml').exists()
    workflow = yaml.load((ROOT / 'release/attest.yml.example').read_text(), Loader=yaml.BaseLoader)
    assert set(workflow['on']) == {'workflow_dispatch'}
    assert workflow['permissions'] == {'contents': 'read'}
    build, attest = workflow['jobs']['build'], workflow['jobs']['attest']
    assert build['uses'] == './.github/workflows/ci.yml'
    assert "vars.ZABALACK_ATTESTATIONS_ENABLED == 'true'" in build['if']
    assert 'inputs.reviewed_commit == github.sha' in build['if']
    assert attest['needs'] == 'build' and attest['environment'] == 'release-attestation'
    assert attest['permissions'] == {'contents': 'read', 'id-token': 'write', 'attestations': 'write'}
    steps = attest['steps']
    download = next(step for step in steps if step.get('uses', '').startswith('actions/download-artifact@'))
    assert download['with'] == {'name': 'zabalack-candidate-${{ github.run_id }}-${{ github.run_attempt }}',
                               'path': 'build/sign-input/'}
    validation = next(i for i, step in enumerate(steps) if 'python tools/verify_release.py' in step.get('run', ''))
    signing = next(i for i, step in enumerate(steps) if step.get('uses', '').startswith('actions/attest@'))
    assert validation < signing
    assert steps[signing]['with'] == {'subject-path': 'build/attestation-subjects/*',
                                     'push-to-registry': 'false', 'create-storage-record': 'false'}


def test_attestation_pins_match_inspected_official_source_manifest():
    manifest = json.loads((ROOT / 'docs/upstream-attestation-manifest.json').read_text())
    components = {row['name']: row for row in manifest['components']}
    assert components['actions/attest']['commit'] == '1e69f48acb82d1966a394da916b4c1698aa569d6'
    assert components['cli/cli']['commit'] == '560ea94fc7d63ab1f56ca47713599be5e8966fd3'
    assert components['cli/cli']['version'] == signed.GH_VERSION
    assert manifest['local_consistency_is_signed'] is False
    assert manifest['hosted_attestation_issuance'] == 'NOT_RUN'
    for row in components.values():
        assert row['repository'] == 'https://github.com/' + row['name']
        assert row['files'] and all(len(value) == 64 for value in row['files'].values())


def test_guidance_manifest_cannot_be_rehashed_to_hide_missing_instructions(candidate):
    root, output = candidate
    path = output / 'dist/BUILD_PROVENANCE.json'
    edit_json(path, lambda value: value['agent_guidance'].update(sha256='0' * 64))
    refresh_sums(output / 'dist')
    with pytest.raises(ci.EvidenceError, match='agent guidance'):
        release.verify(root, output / 'dist')
