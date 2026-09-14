#!/usr/bin/env python3
"""Fetch only public immutable source URLs in the shipped reference manifest.

No private inventory, credentials or configurable callback hosts are accepted.
This is a development-only source retrieval command, not an installer.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import urllib.parse
import urllib.request

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download-public-sources', action='store_true', required=True)
    args = parser.parse_args()
    manifest = json.loads((REPO / 'docs/reference_manifest.json').read_text())
    destination = REPO.parent / 'upstream-verified'
    for source in manifest['sources']:
        commit = source['commit']
        if len(commit) != 40 or any(c not in '0123456789abcdef' for c in commit):
            raise ValueError('Only full immutable commits accepted')
        for item in source.get('files', []):
            url = urllib.parse.urlsplit(item['url'])
            if url.scheme != 'https' or url.netloc != 'raw.githubusercontent.com':
                raise ValueError('Unsupported public source host')
            relative = Path(item['path'])
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('Unsafe reference path')
            request = urllib.request.Request(item['url'], headers={'User-Agent':'zabalack-reference-retrieval/1'})
            with urllib.request.urlopen(request, timeout=30) as response:
                if urllib.parse.urlsplit(response.url).netloc != 'raw.githubusercontent.com':
                    raise ValueError('Unexpected redirect host')
                data = response.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != item['sha256']:
                raise ValueError(f'Checksum/size mismatch for {source["component"]}/{relative}')
            output = destination / source['component'] / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(data)
            print(f'Verified {source["component"]}/{relative}')


if __name__ == '__main__':
    main()
