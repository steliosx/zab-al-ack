# Tag proposal: v0.1.0b1 (beta candidate)

Prepared 11 October 2026. This document proposes a tag. It does not create one. The tag
is created only after the owner and the independent reviewer (Codex) each
approve this exact commit in writing, and after the publication audit returns
zero. No upload, release or deployment is authorized by this proposal.

## Exact identity

| Item | Value |
|---|---|
| Tag name | `v0.1.0b1` |
| Commit | `fee1528a6853cc3f5a19d41594fcf72f0e186f59` (main, 11 October 2026) |
| Version | `0.1.0b1` (pyproject, package, manifests, register) |
| CI run | 38140956596 attempt 1 (ci.yml, GitHub-hosted ubuntu-24.04) |
| Scan run | 38140956520 (security.yml) |
| Candidate artifact | `zabalack-candidate-38140956596-1` (7,618,009 B) |

## Artifact hashes (SHA-256, from the run's own SHA256SUMS; verified)

| File | SHA-256 |
|---|---|
| `zabalack-0.1.0b1-py3-none-any.whl` | `38161158addaeb7795f2df0633663d84554c26046fdf7cea7a25be558868941a` |
| `zabalack-0.1.0b1.tar.gz` | `df9a3e79f530c248cd159248defefef6025957eb086ed07ac4b8229412b3166e` |
| `zabalack-0.1.0b1-source.zip` | `d53e7458d2820509434b020ece742fefead9245eaf1e40daeaf03ff461099126` |
| `SHA256SUMS` | `a528f040006a339176d4651a9f44f1e5b831c7726a84c63ed03d5ea09331f180` |
| `BUILD_PROVENANCE.json` | `2004cf8b442449da5fcebd84c95986bacf751cefe8e532aa5968b22d2a685539` |

## What passed at this commit

| Job | Result |
|---|---|
| quality | SUCCESS |
| mutation | SUCCESS, 12 killed, 0 survived |
| reference | SUCCESS, 1,503 passed, 47 skipped, 0 failed |
| native (Docker lab, x86_64 hosted runner) | SUCCESS, 311 passed across 5 phases |
| package (public audit, build, acceptance mapping) | SUCCESS |
| scan (pip-audit 58 pins, Gitleaks dir + history) | SUCCESS, 0 advisories, 0 findings |
| CodeQL | SKIPPED at this commit's push run; later dispatched run 38144863693 FAILED at ingestion (not a clean scan) |

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

1. Re-read live `main`; confirm it is still `fee1528a6853cc3f5a19d41594fcf72f0e186f59`.
2. Run `python tools/public_audit.py --publication`; require exit 0.
3. Create an annotated tag `v0.1.0b1` on that commit; push the tag only.
4. Review the tag's Actions run; compare its artifact hashes with the table above.
   A different hash is a different artifact; stop and record.
5. Create the GitHub prerelease with the release notes. No PyPI upload.
