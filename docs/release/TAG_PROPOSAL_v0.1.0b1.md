# Tag proposal: v0.1.0b1 (beta candidate)

Prepared 11 October 2026. This document proposes a tag. It does not create one. The tag
is created only after the owner and the independent reviewer (Codex) each
approve this exact commit in writing, and after the publication audit returns
zero. No upload, release or deployment is authorized by this proposal.

## Exact identity

| Item | Value |
|---|---|
| Tag name | `v0.1.0b1` |
| Commit | `f85212e238fb6322802b163219096db48e428291` (main, 11 October 2026) |
| Version | `0.1.0b1` (pyproject, package, manifests, register) |
| CI run | 38187030741 attempt 1 (ci.yml, GitHub-hosted ubuntu-24.04) |
| Scan run | 38187030803 (security.yml) |
| Candidate artifact | `zabalack-candidate-38187030741-1` (7,649,222 B) |

## Superseded candidate

Earlier candidates are retained privately unchanged and not rebound: `fee1528a…`
(run 38140956596, wheel `38161158…`, superseded by the F2 fix #40) and `3c3c0dfb…`
(run 38147339640, wheel `c88dee99…`, superseded by the PostgreSQL 18.6 pin #41 and the
coexistence contract #42) and `132a5440…` (run 38154875005, wheel `bf5b0171…`, superseded by
the register inputs #44 and the lab TLS floor #46).

## Artifact hashes (SHA-256, from the run's own SHA256SUMS; verified)

| File | SHA-256 |
|---|---|
| `zabalack-0.1.0b1-py3-none-any.whl` | `bf5b017111742c7c6c90a8701286987b20649a05a8aa7fa12515803f1617719b` |
| `zabalack-0.1.0b1.tar.gz` | `ce346c9d562dc80ae094a16df4491d3812c47d1383f57eeaedf13051dbb0b821` |
| `zabalack-0.1.0b1-source.zip` | `9940da37c6b160eae3861e006f278864857fd68bb4898ab7b46011c84bea1cc7` |
| `SHA256SUMS` | `460b990ed078d957ee9b0c6d52890318accbeb299af6fe2a557f67f0bd014fda` |
| `BUILD_PROVENANCE.json` | `232501b7d14f2067e58d32cef53ecfa8a6d31d188f8a720f34ac7e2f6cdfd61b` |

## What passed at this commit

| Job | Result |
|---|---|
| quality | SUCCESS |
| mutation | SUCCESS, 12 killed, 0 survived |
| reference | SUCCESS, 1,503 passed, 47 skipped, 0 failed |
| native (Docker lab, PostgreSQL 18.6, x86_64 hosted runner) | SUCCESS, 5 phases |
| package (public audit, build, acceptance mapping) | SUCCESS |
| scan (pip-audit 58 pins, Gitleaks dir + history) | SUCCESS, 0 advisories, 0 findings |
| CodeQL | SUCCESS (python, javascript-typescript) on this commit's push run; 8 harness-path alerts triaged in `CODEQL_TRIAGE_20261011.md` (PR #45) |

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

1. Re-read live `main`; confirm it is still `f85212e238fb6322802b163219096db48e428291`.
2. Run `python tools/public_audit.py --publication`; require exit 0.
3. Create an annotated tag `v0.1.0b1` on that commit; push the tag only.
4. Review the tag's Actions run; compare its artifact hashes with the table above.
   A different hash is a different artifact; stop and record.
5. Create the GitHub prerelease with the release notes. No PyPI upload.
