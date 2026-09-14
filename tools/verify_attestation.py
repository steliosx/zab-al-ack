#!/usr/bin/env python3
"""Run pinned GitHub CLI cryptographic verification with exact certificate policy.

JSON supplied by a caller is never accepted as verification evidence. This
wrapper owns the verifier execution; it does not issue or upload attestations.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess

from collect_ci_evidence import EvidenceError, require
from verify_release import safe_bytes, sha

GH_VERSION = '2.83.2'
PREDICATE = 'https://slsa.dev/provenance/v1'
WORKFLOW = '.github/workflows/attest.yml'


def verify(artifact: Path, repository: str, source_ref: str, commit: str, *,
           bundle: Path | None = None, trusted_root: Path | None = None) -> dict:
    require(bool(re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', repository)), 'Invalid repository policy')
    require(bool(re.fullmatch(r'refs/tags/v[0-9]+\.[0-9]+\.[0-9]+a[0-9]+', source_ref)),
            'Expected reviewed alpha tag policy')
    require(bool(re.fullmatch(r'[0-9a-f]{40}', commit)), 'Expected exact source commit')
    require(trusted_root is None or bundle is not None, 'Offline root requires a local bundle')
    watched = [artifact, *([bundle] if bundle else []), *([trusted_root] if trusted_root else [])]
    before = [sha(safe_bytes(path)) for path in watched]
    env = dict(os.environ, GH_HOST='github.com', GH_PROMPT_DISABLED='1', GH_PAGER='cat',
               GH_NO_UPDATE_NOTIFIER='1', TZ='UTC')
    version = subprocess.run(['gh', '--version'], capture_output=True, text=True, timeout=15, env=env)
    require(version.returncode == 0 and bool(re.match(r'gh version ' + re.escape(GH_VERSION) + r'(?:\s|$)', version.stdout)),
            'Pinned GitHub CLI verifier unavailable')
    # Inspected v2.83.2 --signer-workflow builds a prefix regular expression.
    # Exact --cert-identity binds the complete workflow path AND immutable ref.
    identity = f'https://github.com/{repository}/{WORKFLOW}@{source_ref}'
    command = ['gh', 'attestation', 'verify', str(artifact.absolute()), '--hostname', 'github.com',
               '--repo', repository, '--cert-identity', identity,
               '--cert-oidc-issuer', 'https://token.actions.githubusercontent.com',
               '--source-ref', source_ref, '--source-digest', commit, '--signer-digest', commit,
               '--predicate-type', PREDICATE, '--deny-self-hosted-runners', '--format', 'json']
    if bundle:
        command += ['--bundle', str(bundle.absolute())]
    if trusted_root:
        command += ['--custom-trusted-root', str(trusted_root.absolute())]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=120, env=env)
    require(completed.returncode == 0, 'Cryptographic verifier refused attestation')
    require(len(completed.stdout) <= 4 * 1024 * 1024, 'Verifier output exceeds size bound')
    results = json.loads(completed.stdout)
    require(isinstance(results, list) and bool(results), 'Verifier returned no verified attestations')
    matched = False
    for result in results:
        require(isinstance(result, dict) and isinstance(result.get('verificationResult'), dict),
                'Malformed verifier result')
        statement = result['verificationResult'].get('statement')
        require(isinstance(statement, dict) and isinstance(statement.get('subject'), list),
                'Malformed verified statement')
        require(statement.get('predicateType') == PREDICATE, 'Unexpected verified predicate')
        for subject in statement.get('subject', []):
            if isinstance(subject, dict) and subject.get('digest') == {'sha256': before[0]}:
                matched = True
    require(matched, 'Verified statement does not bind the artifact digest')
    require(before == [sha(safe_bytes(path)) for path in watched], 'Verifier inputs changed during verification')
    return {'status': 'VERIFIED_GITHUB_ATTESTATION', 'artifact_sha256': before[0],
            'repository': repository, 'source_ref': source_ref, 'source_commit': commit,
            'certificate_identity': identity, 'predicate_type': PREDICATE,
            'verifier': 'gh==' + GH_VERSION, 'deployment_gates': 'MANUAL_GATE_PENDING'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifact', type=Path)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--source-ref', required=True)
    parser.add_argument('--source-digest', required=True)
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--trusted-root', type=Path)
    args = parser.parse_args()
    try:
        result = verify(args.artifact, args.repo, args.source_ref, args.source_digest,
                        bundle=args.bundle, trusted_root=args.trusted_root)
    except FileNotFoundError:
        result = {'status': 'NOT_RUN', 'reason': 'Required local input or pinned GitHub CLI unavailable'}
    except (EvidenceError, OSError, ValueError, TypeError, subprocess.SubprocessError):
        result = {'status': 'FAIL', 'reason': 'Attestation verification did not complete successfully'}
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'VERIFIED_GITHUB_ATTESTATION' else 2 if result['status'] == 'NOT_RUN' else 1


if __name__ == '__main__':
    raise SystemExit(main())
