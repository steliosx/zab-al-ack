#!/usr/bin/env python3
"""Inventory exact evaluation dependencies and declared licences; no network."""
from __future__ import annotations
import base64
from datetime import datetime, timezone
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import re
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]


def generate(root: Path = ROOT) -> tuple[dict, dict]:
    names = [line.split('==')[0] for line in (root / 'requirements.lock').read_text().splitlines()
             if line.strip() and not line.startswith('#')]
    names += [line.split('==')[0] for line in (root / 'requirements-quality.lock').read_text().splitlines()
              if line.strip() and not line.startswith(('#', ' ', '-')) and '==' in line]
    names.append('zabalack')
    components, relationships, review, purls = [], [], [], {}
    distributions = {canonicalize_name(name): metadata.distribution(name) for name in names}
    for name, dist in sorted(distributions.items()):
        purl = 'pkg:pypi/' + name + '@' + dist.version
        purls[name] = purl
        item = {'type': 'library', 'name': name, 'version': dist.version, 'purl': purl, 'bom-ref': purl}
        expression = dist.metadata.get('License-Expression')
        declared = dist.metadata.get('License')
        classifiers = [value.split(' :: ')[-1] for value in dist.metadata.get_all('Classifier', [])
                       if value.startswith('License ::') and value != 'License :: OSI Approved']
        if expression:
            item['licenses'] = [{'expression': expression}]
        elif declared and declared.strip().upper() not in {'UNKNOWN', 'UNLICENSED'} and len(declared) < 180:
            item['licenses'] = [{'license': {'name': declared.strip()}}]
        elif classifiers:
            item['licenses'] = [{'license': {'name': value}} for value in classifiers]
        else:
            review.append({'name': name, 'reason': 'No concise unambiguous declared licence metadata'})
        licence_files = []
        for path in dist.files or []:
            if re.search(r'(?:license|licence|copying|notice)', str(path), re.I):
                local = dist.locate_file(path)
                if local.is_file():
                    licence_files.append({'package_path': str(path), 'sha256': hashlib.sha256(local.read_bytes()).hexdigest()})
        item['properties'] = [{'name': 'zabalack:license-files', 'value': json.dumps(licence_files, sort_keys=True)},
                              {'name': 'zabalack:inventory-scope', 'value': 'reference build/test environment'}]
        components.append(item)
    for name, dist in sorted(distributions.items()):
        dependencies = []
        for value in dist.requires or []:
            req = Requirement(value)
            if req.marker and not req.marker.evaluate({'extra': ''}):
                continue
            dep = canonicalize_name(req.name)
            if dep in purls:
                dependencies.append(purls[dep])
        relationships.append({'ref': purls[name], 'dependsOn': sorted(set(dependencies))})
    lock = json.loads((root / 'lab/package-lock.json').read_text())
    for path, data in sorted(lock['packages'].items()):
        if not path:
            continue
        name = path.split('node_modules/', 1)[1]
        purl = 'pkg:npm/' + name.replace('@', '%40') + '@' + data['version']
        component = {'type': 'library', 'name': name, 'version': data['version'], 'purl': purl, 'bom-ref': purl,
            'properties': [{'name': 'zabalack:inventory-scope', 'value': 'supplemental WASM lab only'}]}
        package_file = root / 'lab' / path / 'package.json'
        licence = data.get('license')
        if not licence and package_file.is_file():
            licence = json.loads(package_file.read_text()).get('license')
        if isinstance(licence, str):
            component['licenses'] = [{'expression': licence}]
        else:
            review.append({'name': name, 'reason': 'Node licence metadata requires review'})
        integrity = data.get('integrity', '')
        if integrity.startswith('sha512-'):
            component['hashes'] = [{'alg': 'SHA-512', 'content': base64.b64decode(integrity[7:]).hex()}]
        components.append(component)
    bom = {'bomFormat': 'CycloneDX', 'specVersion': '1.6', 'version': 1,
        'metadata': {'component': {'type': 'application', 'name': 'Zab-al-ack reference evaluation environment',
                                  'version': '0.1.0b2'}},
        'components': components, 'dependencies': relationships}
    notes = {'inventory_scope': 'Exact installed reference build/test packages and pinned Node lab dependencies',
        'component_count': len(components), 'metadata_review_required': review,
        'licence_obligations_review': 'PENDING_PUBLISHER_REVIEW',
        'vulnerability_clearance': 'NOT_ESTABLISHED_BY_SBOM'}
    return bom, notes


if __name__ == '__main__':
    bom, notes = generate()
    (ROOT / 'evidence/sbom.cdx.json').write_text(json.dumps(bom, indent=2) + '\n')
    (ROOT / 'evidence/dependency-review.json').write_text(json.dumps(notes, indent=2) + '\n')
    print(json.dumps(notes, indent=2))
