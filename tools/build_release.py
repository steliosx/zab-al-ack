#!/usr/bin/env python3
"""Build and compare local candidate artifacts. No upload or remote Git operations."""
from __future__ import annotations
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from public_audit import audit, public_files
from evidence_utils import code_digests
from agent_guidance import inspect as inspect_guidance

ROOT = Path(__file__).resolve().parents[1]
EPOCH = 1789171200


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalise_sdist(source: Path, destination: Path) -> None:
    with source.open('rb') as input_stream, tarfile.open(fileobj=input_stream, mode='r:gz') as original:
        with destination.open('wb') as output_stream, gzip.GzipFile(filename='', mode='wb', fileobj=output_stream, mtime=EPOCH) as compressed:
            with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as archive:
                for member in sorted(original.getmembers(), key=lambda item: item.name):
                    name = Path(member.name)
                    if name.is_absolute() or '..' in name.parts or not (member.isfile() or member.isdir()):
                        raise ValueError('Unsafe or non-regular member in sdist')
                    info = tarfile.TarInfo(member.name)
                    info.mtime = EPOCH
                    info.mode = 0o755 if member.isdir() or member.mode & 0o111 else 0o644
                    info.type = tarfile.DIRTYPE if member.isdir() else tarfile.REGTYPE
                    info.size = member.size if member.isfile() else 0
                    archive.addfile(info, original.extractfile(member) if member.isfile() else None)


def source_zip(root: Path, destination: Path, version: str) -> None:
    timestamp = datetime.fromtimestamp(EPOCH, timezone.utc).timetuple()[:6]
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in public_files(root):
            if path.is_symlink():
                raise ValueError('Symlink in public source selection')
            name = 'zabalack-' + version + '/' + path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(name, timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (0o100755 if path.stat().st_mode & 0o111 else 0o100644) << 16
            archive.writestr(info, path.read_bytes())


def main() -> int:
    inspection = audit(ROOT)
    if inspection['status'] != 'PASS':
        print(json.dumps(inspection, indent=2))
        return 1
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    work = ROOT / 'build/reproducibility'
    output = ROOT / 'dist'
    distributions = output / 'pypi'
    distributions.mkdir(parents=True, exist_ok=True)
    # Clear only this version's previously generated build products. Source and
    # previous version releases remain untouched.
    names = []
    for attempt in ('a', 'b'):
        destination = work / attempt
        destination.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, SOURCE_DATE_EPOCH=str(EPOCH), PYTHONHASHSEED='0',
                   PIP_NO_INDEX='1', PIP_DISABLE_PIP_VERSION_CHECK='1')
        with (destination / 'build.log').open('w') as log:
            subprocess.run([sys.executable, '-m', 'build', '--no-isolation', '--wheel', '--sdist',
                '--outdir', str(destination)], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
        wheels = list(destination.glob('*-' + version + '-*.whl'))
        sources = [p for p in destination.glob('*-' + version + '.tar.gz') if not p.name.startswith('normalised-')]
        if len(wheels) != 1 or len(sources) != 1:
            raise RuntimeError('Expected exactly one wheel and sdist for the selected candidate')
        normalised = destination / ('normalised-' + sources[0].name)
        normalise_sdist(sources[0], normalised)
        names.append((wheels[0], normalised, sources[0].name))
    comparisons = []
    for index, label in ((0, 'wheel'), (1, 'normalised_sdist')):
        first, second = names[0][index], names[1][index]
        first_hash, second_hash = digest(first), digest(second)
        if first_hash != second_hash:
            raise RuntimeError(label + ' reproducibility comparison failed; inspect build/reproducibility')
        filename = first.name if index == 0 else names[0][2]
        target = distributions / filename
        shutil.copyfile(first, target)
        comparisons.append({'artifact': 'pypi/' + filename, 'sha256': first_hash,
                            'two_builds_byte_identical': True, 'kind': label})
    archive = output / ('zabalack-' + version + '-source.zip')
    source_zip(ROOT, archive, version)
    second_zip = work / 'source-second.zip'
    source_zip(ROOT, second_zip, version)
    if digest(archive) != digest(second_zip):
        raise RuntimeError('Source ZIP reproducibility comparison failed')
    comparisons.append({'artifact': archive.name, 'sha256': digest(archive),
                        'two_builds_byte_identical': True, 'kind': 'source_zip'})
    provenance = {'version': version, 'code_digests': code_digests(ROOT), 'agent_guidance': inspect_guidance(ROOT), 'source_date_epoch': EPOCH, 'python': sys.version.split()[0],
        'build_backend': 'setuptools==83.0.0', 'archive_normalisation': 'Stable order, timestamps, ownership and gzip header for sdist',
        'comparisons': comparisons, 'signed_attestation': False, 'published': False,
        'scope': 'Reproducibility within the declared build environment; cross-platform reproduction untested'}
    (output / 'BUILD_PROVENANCE.json').write_text(json.dumps(provenance, indent=2) + '\n')
    included = [distributions / names[0][0].name, distributions / names[0][2], archive, output / 'BUILD_PROVENANCE.json']
    (output / 'SHA256SUMS').write_text(''.join(digest(path) + '  ' + path.relative_to(output).as_posix() + '\n' for path in included))
    print(json.dumps(provenance, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
