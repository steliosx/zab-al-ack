#!/usr/bin/env python3
"""Verify release byte consistency; optionally stage subjects from current-run CI.

This is an unsigned integrity check. Only a separate cryptographic verifier can
establish signed provenance. No archive member is executed or extracted.
"""
from __future__ import annotations

import argparse
import base64
import configparser
import csv
from email.parser import BytesParser
from email.policy import default as email_policy
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import tomllib
import zipfile

from collect_ci_evidence import (
    EvidenceError, KINDS, environment_context, read_json, require,
    validate_context, validate_reports,
)
from evidence_utils import code_digests
from deployment_evidence import read_record as read_deployment_record, validate_report as validate_deployment_report
from public_audit import public_files
from agent_guidance import inspect as inspect_guidance

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 100 * 1024 * 1024
MAX_MEMBERS = 2000


def safe_bytes(path: Path) -> bytes:
    require(not path.is_symlink() and not any(p.is_symlink() for p in path.parents)
            and path.is_file(), 'Release input is missing or not a regular file')
    require(path.stat().st_size <= MAX_BYTES, 'Release input exceeds size bound')
    return path.read_bytes()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def member_name(name: str) -> str:
    path = PurePosixPath(name)
    require(not path.is_absolute() and path.as_posix() == name
            and all(part not in {'..', '.'} for part in path.parts)
            and '\\' not in name and '\x00' not in name and bool(path.parts),
            'Unsafe archive member path')
    return name


def zip_contents(data: bytes) -> dict[str, bytes]:
    result = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        require(len(members) <= MAX_MEMBERS and sum(m.file_size for m in members) <= MAX_BYTES,
                'Archive exceeds expanded size or member bound')
        for item in members:
            name = member_name(item.filename)
            require(name not in result and not item.is_dir(), 'Duplicate or directory ZIP member')
            mode = stat.S_IFMT(item.external_attr >> 16)
            require(mode in {0, stat.S_IFREG}, 'Non-regular ZIP member')
            result[name] = archive.read(item)
    return result


def tar_contents(data: bytes) -> dict[str, bytes]:
    result = {}
    seen = set()
    expanded = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        for item in archive:
            name = member_name(item.name)
            expanded += item.size
            require(name not in seen and len(seen) < MAX_MEMBERS and expanded <= MAX_BYTES,
                    'Duplicate or oversized tar member')
            seen.add(name)
            require(item.isfile() or item.isdir(), 'Non-regular tar member')
            if item.isfile():
                stream = archive.extractfile(item)
                require(stream is not None, 'Missing tar content')
                result[name] = stream.read()
    return result


def verify_wheel_metadata(wheel: dict[str, bytes], source: dict[str, bytes], project: dict) -> None:
    """Check install behavior against the reviewed project, including metadata.

    This deliberately implements the narrow metadata produced by the reference
    setuptools pin, not a permissive general-purpose Python package validator.
    """
    prefix = f'zabalack-{project["version"]}.dist-info/'
    expected = {'METADATA', 'WHEEL', 'entry_points.txt', 'top_level.txt', 'RECORD',
                'licenses/LICENSE', 'licenses/NOTICE'}
    require({name.removeprefix(prefix) for name in wheel if name.startswith(prefix)} == expected,
            'Unexpected or missing wheel metadata file')
    for name in ('LICENSE', 'NOTICE'):
        require(wheel[prefix + 'licenses/' + name] == source.get(name), 'Wheel licence differs from source')
    metadata = BytesParser(policy=email_policy).parsebytes(wheel[prefix + 'METADATA'])
    def one(key: str, expected_value: str) -> None:
        require(metadata.get_all(key, []) == [expected_value], 'Wheel metadata differs from project')
    one('Metadata-Version', '2.4')
    one('Name', project['name'])
    one('Version', project['version'])
    one('License-Expression', project['license'])
    requires_python = metadata.get_all('Requires-Python', [])
    require(len(requires_python) == 1 and sorted(requires_python[0].replace(' ', '').split(',')) ==
            sorted(project['requires-python'].replace(' ', '').split(',')), 'Wheel Python constraint differs from project')
    extras = project.get('optional-dependencies', {})
    dependencies = list(project.get('dependencies', []))
    dependencies += [dependency + '; extra == "' + extra + '"'
                     for extra, rows in extras.items() for dependency in rows]
    require(sorted(metadata.get_all('Requires-Dist', [])) == sorted(dependencies)
            and sorted(metadata.get_all('Provides-Extra', [])) == sorted(extras),
            'Wheel dependencies differ from project')
    entry_points = configparser.ConfigParser(interpolation=None, strict=True)
    entry_points.optionxform = str
    entry_points.read_string(wheel[prefix + 'entry_points.txt'].decode())
    groups = dict(project.get('entry-points', {}))
    if project.get('scripts'):
        groups['console_scripts'] = project['scripts']
    if project.get('gui-scripts'):
        groups['gui_scripts'] = project['gui-scripts']
    require(not entry_points.defaults() and {name: dict(entry_points[name]) for name in entry_points.sections()} == groups,
            'Wheel entry points differ from project')
    wheel_metadata = BytesParser(policy=email_policy).parsebytes(wheel[prefix + 'WHEEL'])
    require(set(wheel_metadata.keys()) == {'Wheel-Version', 'Generator', 'Root-Is-Purelib', 'Tag'}
            and all(wheel_metadata.get_all(key, []) == [value] for key, value in {
                'Wheel-Version': '1.0', 'Generator': 'setuptools (84.1.0)',
                'Root-Is-Purelib': 'true', 'Tag': 'py3-none-any'}.items()), 'Unexpected wheel install layout')
    require(wheel[prefix + 'top_level.txt'] == b'zabalack\n', 'Unexpected wheel import root')
    seen = set()
    for row in csv.reader(io.StringIO(wheel[prefix + 'RECORD'].decode())):
        require(len(row) == 3 and row[0] in wheel and row[0] not in seen, 'Invalid wheel RECORD member')
        name, digest, size = row
        seen.add(name)
        if name == prefix + 'RECORD':
            require(digest == size == '', 'Invalid RECORD self-reference')
        else:
            data = wheel[name]
            expected_digest = 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=')
            require(digest == expected_digest and size == str(len(data)), 'Wheel RECORD differs from member bytes')
    require(seen == set(wheel), 'Wheel RECORD does not cover exact file set')


def verify(root: Path, dist: Path, *, candidate: Path | None = None,
           context: dict | None = None) -> dict:
    project = tomllib.loads(safe_bytes(root / 'pyproject.toml').decode())['project']
    version = project['version']
    require(isinstance(version, str) and bool(re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+a[0-9]+', version)),
            'Expected an explicit alpha version')
    names = {
        'wheel': f'pypi/zabalack-{version}-py3-none-any.whl',
        'normalised_sdist': f'pypi/zabalack-{version}.tar.gz',
        'source_zip': f'zabalack-{version}-source.zip',
    }
    all_names = {*names.values(), 'BUILD_PROVENANCE.json'}
    contents = {name: safe_bytes(dist / name) for name in all_names}
    sums = safe_bytes(dist / 'SHA256SUMS').decode('ascii')
    seen = {}
    for line in sums.splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  ([A-Za-z0-9_./-]+)', line)
        require(match is not None, 'Invalid checksum line')
        digest, name = match.groups()
        require(name in all_names and name not in seen, 'Unexpected or duplicate checksum subject')
        seen[name] = digest
    require(set(seen) == all_names and all(sha(data) == seen[name] for name, data in contents.items()),
            'Release checksum mismatch or missing subject')
    provenance = read_json(dist / 'BUILD_PROVENANCE.json')
    require(provenance.get('version') == version and provenance.get('code_digests') == code_digests(root),
            'Release source or harness differs from checkout')
    require(provenance.get('agent_guidance') == inspect_guidance(root),
            'Release agent guidance differs from checkout')
    require(provenance.get('signed_attestation') is False and provenance.get('published') is False,
            'Local provenance must remain explicitly unsigned and unpublished')
    comparisons = provenance.get('comparisons')
    require(isinstance(comparisons, list) and len(comparisons) == len(names), 'Incomplete build comparisons')
    kinds = set()
    for row in comparisons:
        require(isinstance(row, dict) and row.get('kind') in names, 'Invalid build comparison')
        kind = row['kind']
        require(kind not in kinds and row.get('artifact') == names[kind]
                and row.get('sha256') == seen[names[kind]] and row.get('two_builds_byte_identical') is True,
                'Build comparison does not match subject bytes')
        kinds.add(kind)

    prefix = f'zabalack-{version}/'
    source = zip_contents(contents[names['source_zip']])
    require(all(name.startswith(prefix) for name in source), 'Unexpected source archive root')
    source = {name.removeprefix(prefix): data for name, data in source.items()}
    if 'evidence/deployment-e2e-run.json' in source:
        # Even an unsigned local verification must reject internally
        # contradictory service evidence embedded in otherwise consistent bytes.
        validate_deployment_report(read_deployment_record(source['evidence/deployment-e2e-run.json']), root)
    # Evidence is generated by CI. Every other selected file must equal the
    # trusted checkout, including documentation and release workflow policy.
    expected = {p.relative_to(root).as_posix(): safe_bytes(p) for p in public_files(root)
                if p.relative_to(root).parts[0] != 'evidence'}
    actual = {name: data for name, data in source.items() if PurePosixPath(name).parts[0] != 'evidence'}
    require(actual == expected, 'Source archive differs from checkout public files')
    wheel = zip_contents(contents[names['wheel']])
    wheel_code = {name: data for name, data in wheel.items() if name.startswith('zabalack/')}
    expected_code = {name.removeprefix('src/'): data for name, data in source.items()
                     if name.startswith('src/zabalack/')}
    require(wheel_code == expected_code, 'Wheel code differs from reviewed source')
    require(all(name.startswith(('zabalack/', f'zabalack-{version}.dist-info/')) for name in wheel),
            'Unexpected executable wheel content')
    verify_wheel_metadata(wheel, source, project)
    sdist = tar_contents(contents[names['normalised_sdist']])
    require(all(name.startswith(prefix) for name in sdist), 'Unexpected sdist root')
    sdist = {name.removeprefix(prefix): data for name, data in sdist.items()}
    require(all(sdist.get(name) == data for name, data in source.items() if name != '.gitignore'),
            'Source distribution differs from source archive')
    generated = {'PKG-INFO', 'src/zabalack.egg-info/PKG-INFO', 'src/zabalack.egg-info/SOURCES.txt',
                 'src/zabalack.egg-info/dependency_links.txt', 'src/zabalack.egg-info/entry_points.txt',
                 'src/zabalack.egg-info/requires.txt', 'src/zabalack.egg-info/top_level.txt'}
    if 'setup.cfg' not in source:
        generated.add('setup.cfg')
        require(sdist.get('setup.cfg') == b'[egg_info]\ntag_build = \ntag_date = 0\n\n',
                'Unexpected generated setup configuration')
    require(set(sdist) == (set(source) - {'.gitignore'}) | generated, 'Unexpected or missing source distribution content')
    info = f'zabalack-{version}.dist-info/'
    require(sdist['PKG-INFO'] == sdist['src/zabalack.egg-info/PKG-INFO'] == wheel[info + 'METADATA']
            and sdist['src/zabalack.egg-info/entry_points.txt'] == wheel[info + 'entry_points.txt']
            and sdist['src/zabalack.egg-info/top_level.txt'] == wheel[info + 'top_level.txt']
            and sdist['src/zabalack.egg-info/dependency_links.txt'] == b'\n',
            'Source distribution install metadata differs from verified wheel')
    requirements = ''.join(value + '\n' for value in project.get('dependencies', []))
    requirements += ''.join('\n[' + extra + ']\n' + ''.join(value + '\n' for value in rows)
                            for extra, rows in sorted(project.get('optional-dependencies', {}).items()))
    require(sdist['src/zabalack.egg-info/requires.txt'] == requirements.encode(),
            'Source distribution dependencies differ from project')
    source_list = sdist['src/zabalack.egg-info/SOURCES.txt'].decode().splitlines()
    require(len(source_list) == len(set(source_list)) and set(source_list) == set(sdist) - {'PKG-INFO', 'setup.cfg'},
            'Source distribution manifest differs from archive')

    if candidate is not None:
        require(context is not None, 'Signing requires explicit current CI context')
        validate_context(context)
        collection = read_json(candidate / 'evidence/ci-collection.json')
        validate_context(collection.get('context'))
        require(collection.get('status') == 'PASS' and collection.get('context') == context
                and collection.get('code_digests') == code_digests(root)
                and collection.get('kinds') == list(KINDS) and collection.get('signed_attestation') is False,
                'Required current-run CI collection is missing or foreign')
        reports = set()
        for kind in KINDS:
            reports.update(validate_reports(root, candidate, kind))
        require(isinstance(collection.get('files'), list), 'Invalid CI collection file list')
        selected = {}
        for item in collection['files']:
            require(isinstance(item, dict) and set(item) == {'path', 'sha256'}
                    and isinstance(item['path'], str) and item['path'] in reports
                    and item['path'] not in selected, 'Unexpected or duplicate CI collection subject')
            selected[item['path']] = item['sha256']
        require(set(selected) == reports, 'Required CI collection reports missing')
        for name in reports | {'evidence/ci-collection.json'}:
            data = safe_bytes(candidate / name)
            require(source.get(name) == data, 'Archive evidence differs from current CI evidence')
            if name in selected:
                require(sha(data) == selected[name], 'CI collection report hash mismatch')
        evidence_files = {p.relative_to(candidate).as_posix(): safe_bytes(p)
                          for p in (candidate / 'evidence').rglob('*') if not p.is_dir()}
        require(evidence_files == {name: data for name, data in source.items() if name.startswith('evidence/')},
                'Candidate evidence file set differs from source archive')
    return {'status': 'PASS', 'scope': 'unsigned_release_consistency', 'signed_attestation': False,
            'version': version, 'code_digests': code_digests(root), 'ci_context': context,
            'subjects': [{'path': name, 'sha256': seen[name]} for name in sorted(all_names)]}


def stage(root: Path, dist: Path, result: dict) -> Path:
    """A fixed, fresh directory prevents a signing glob accepting extra inputs."""
    output = root / 'build/attestation-subjects'
    require(not output.is_symlink() and not any(p.is_symlink() for p in output.parents),
            'Unsafe staging directory')
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    for item in result['subjects']:
        data = safe_bytes(dist / item['path'])
        require(sha(data) == item['sha256'], 'Subject changed after verification')
        (output / PurePosixPath(item['path']).name).write_bytes(data)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, default=ROOT / 'dist')
    parser.add_argument('--candidate', type=Path, help='Require fresh current-run CI and stage signing subjects')
    args = parser.parse_args()
    try:
        context = environment_context(ROOT) if args.candidate else None
        dist = args.candidate / 'dist' if args.candidate else args.dist
        result = verify(ROOT, dist, candidate=args.candidate, context=context)
        if args.candidate:
            stage(ROOT, dist, result)
        print(json.dumps(result, indent=2))
        return 0
    except (EvidenceError, OSError, ValueError, KeyError, TypeError, RuntimeError,
            configparser.Error, csv.Error, subprocess.SubprocessError, zipfile.BadZipFile, tarfile.TarError):
        # Raw parse errors may contain caller-controlled strings. Keep public
        # CLI diagnostics fixed; focused unit assertions retain exact reasons.
        print(json.dumps({'status': 'FAIL', 'scope': 'unsigned_release_consistency',
                          'reason': 'Release consistency validation failed'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
