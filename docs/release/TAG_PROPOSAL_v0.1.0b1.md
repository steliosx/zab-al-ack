# Tag proposal: v0.1.0b1 (beta candidate)

Prepared 11 October 2026. This document proposes a tag. It does not create one. The tag
is created only after the owner and the independent reviewer (Codex) each
approve this exact commit in writing, and after the publication audit returns
zero. No upload, release or deployment is authorized by this proposal.

## Exact identity

| Item | Value |
|---|---|
| Tag name | `v0.1.0b1` |
| Commit | `132a5440df88ce2ce2ce5d8ec40bbf845d5f34b6` (main, 11 October 2026) |
| Version | `0.1.0b1` (pyproject, package, manifests, register) |
| CI run | 38154875005 attempt 1 (ci.yml, GitHub-hosted ubuntu-24.04) |
| Scan run | 38154875108 (security.yml) |
| Candidate artifact | `zabalack-candidate-38154875005-1` (7,647,948 B) |

## Superseded candidate

Earlier candidates are retained privately unchanged and not rebound: `fee1528a…`
(run 38140956596, wheel `38161158…`, superseded by the F2 fix #40) and `3c3c0dfb…`
(run 38147339640, wheel `c88dee99…`, superseded by the PostgreSQL 18.6 pin #41 and the
coexistence contract #42).

## Artifact hashes (SHA-256, from the run's own SHA256SUMS; verified)

| File | SHA-256 |
|---|---|
| `zabalack-0.1.0b1-py3-none-any.whl` | `bf5b017111742c7c6c90a8701286987b20649a05a8aa7fa12515803f1617719b` |
| `zabalack-0.1.0b1.tar.gz` | `e08c33789784486f87f5617a194a12228ca9594353fde554689ec23be9385563` |
| `zabalack-0.1.0b1-source.zip` | `4d1e593d96689d0a55e503c4e4c4406576acc19e1d6d930008d52c5023ad5858` |
| `SHA256SUMS` | `7cbe6f880d1769425e4bc58fb337c778c1559854d2c180ffcf583ae4399815f1` |
| `BUILD_PROVENANCE.json` | `e599c58eba81af468ad21725b27f6fc05b4e8c102538a74115c461dfc8646fd4` |

## What passed at this commit

| Job | Result |
|---|---|
| quality | SUCCESS |
| mutation | SUCCESS, 12 killed, 0 survived |
| reference | SUCCESS, 1,503 passed, 47 skipped, 0 failed |
| native (Docker lab, PostgreSQL 18.6, x86_64 hosted runner) | SUCCESS, 5 phases |
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

1. Re-read live `main`; confirm it is still `132a5440df88ce2ce2ce5d8ec40bbf845d5f34b6`.
2. Run `python tools/public_audit.py --publication`; require exit 0.
3. Create an annotated tag `v0.1.0b1` on that commit; push the tag only.
4. Review the tag's Actions run; compare its artifact hashes with the table above.
   A different hash is a different artifact; stop and record.
5. Create the GitHub prerelease with the release notes. No PyPI upload.
