# Tag proposal: v0.1.0b1 (beta candidate)

Prepared 11 October 2026. This document proposes a tag. It does not create one. The tag
is created only after the owner and the independent reviewer (Codex) each
approve this exact commit in writing, and after the publication audit returns
zero. No upload, release or deployment is authorized by this proposal.

## Exact identity

| Item | Value |
|---|---|
| Tag name | `v0.1.0b1` |
| Commit | `3c3c0dfbf02f74ae3abe38ec136037afabde48b5` (main, 11 October 2026) |
| Version | `0.1.0b1` (pyproject, package, manifests, register) |
| CI run | 38147339640 attempt 1 (ci.yml, GitHub-hosted ubuntu-24.04) |
| Scan run | 38147339650 (security.yml) |
| Candidate artifact | `zabalack-candidate-38147339640-1` (7,619,869 B) |

## Superseded candidate

The earlier candidate `fee1528a6853cc3f5a19d41594fcf72f0e186f59` (run 38140956596, wheel
`38161158…`) is superseded by the F2 fix (#40) and retained privately unchanged. Its
results are not rebound to this commit.

## Artifact hashes (SHA-256, from the run's own SHA256SUMS; verified)

| File | SHA-256 |
|---|---|
| `zabalack-0.1.0b1-py3-none-any.whl` | `c88dee99900b0da9761cbec082745c45ed8f81e5163b9ca82fa3d619f8d57a9d` |
| `zabalack-0.1.0b1.tar.gz` | `830bcbe654092874451c8cdb005c7be529a9dc6b76a12340997eba3dce1be715` |
| `zabalack-0.1.0b1-source.zip` | `485bd6ed0d9236b6f5a31322f2ca6dc62fd63bfaf57d1fe7d9254331053e8b8e` |
| `SHA256SUMS` | `4b1f8037f688601f6fd409f6a7194c8e1336b1109c9cb31adbb8a756915252b1` |
| `BUILD_PROVENANCE.json` | `f262ad2281041967fb87d3b8be1dae3756a6651eac0ecda3443d321e6b30f193` |

## What passed at this commit

| Job | Result |
|---|---|
| quality | SUCCESS |
| mutation | SUCCESS, 12 killed, 0 survived |
| reference | SUCCESS, 1,503 passed, 47 skipped, 0 failed |
| native (Docker lab, x86_64 hosted runner) | SUCCESS, 311 passed across 5 phases |
| package (public audit, build, acceptance mapping) | SUCCESS |
| scan (pip-audit 58 pins, Gitleaks dir + history) | SUCCESS, 0 advisories, 0 findings |
| CodeQL | SKIPPED at this commit's push run (variable off). Earlier on 11 October, dispatched run 38144863693 on the superseded commit fee1528 FAILED at ingestion; no clean scan exists |

The native result is CI evidence for this run on a hosted x86_64 runner. It
is not the deferred native x64 qualification milestone and not installed
acceptance (see `docs/BETA_READINESS.md`).

## Inputs still open before the tag

- Publication register: backup maintainer, private conduct contact,
  copyright/third-party approval, public-content approval, PyPI namespace
  confirmation, explicit publication authorization, verified private security
  reporting. `python tools/public_audit.py --publication` must return 0.
- Independent external security review (xAI Grok 4.7, AI-assisted; starts after the
  repository is public): this revision reviewed, findings settled (see
  `SECURITY_REVIEW_REQUEST_v0.1.0b1.md`). Internal adversarial pass completed
  11 October 2026; report retained privately (sha256 0bd8c5a9…).
- Owner policy decisions recorded (R24): attestation deferred; environment statement
  as tested; CodeQL gate off until the repository is public, then one completed run
  and triage before the tag (see `POLICY_DECISIONS_v0.1.0b1.md`).
- Release notes reviewed (`RELEASE_NOTES_v0.1.0b1.md`).

## Approval record (fill in)

| Party | Decision | Exact commit confirmed | Date (Nassau) | Reference |
|---|---|---|---|---|
| Owner | | | | |
| Codex (independent reviewer) | | | | signed message id |

## Procedure after approval

1. Re-read live `main`; confirm it is still `3c3c0dfbf02f74ae3abe38ec136037afabde48b5`.
2. Run `python tools/public_audit.py --publication`; require exit 0.
3. Create an annotated tag `v0.1.0b1` on that commit; push the tag only.
4. Review the tag's Actions run; compare its artifact hashes with the table above.
   A different hash is a different artifact; stop and record.
5. Create the GitHub prerelease with the release notes. No PyPI upload.
