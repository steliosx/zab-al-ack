# Tag proposal: v0.1.0b1 (beta candidate)

Prepared 11 October 2026. This document proposes a tag. It does not create one. The tag
is created only after the owner and the independent reviewer (Codex) each
approve this exact commit in writing, and after the publication audit returns
zero. No upload, release or deployment is authorized by this proposal.

## Exact identity

| Item | Value |
|---|---|
| Tag name | `v0.1.0b1` |
| Proposed tag target = frozen producer commit | `f85212e238fb6322802b163219096db48e428291` (11 October 2026) |
| Current `main` at this proposal's head | `ac0a5511a1d49af2b0d169b82bc58f17fc711f2b` (documentation-only descendants of the producer; see "Identity reconciliation") |
| Documentation review head | this PR's head (the docs below are reviewed here, not in the tag tree) |
| Version | `0.1.0b1` (pyproject, package, manifests, register) |
| CI run | 38187030741 attempt 1 (ci.yml, GitHub-hosted ubuntu-24.04) |
| Scan run | 38187030803 (security.yml) |
| Candidate artifact | `zabalack-candidate-38187030741-1` (7,649,222 B) |

## Identity reconciliation (independent review D1)

Four identities are distinct and must not be conflated:

1. **Frozen producer commit** `f85212e2…`: built the wheel, sdist and source zip above in run
   38187030741. The tag `v0.1.0b1` is proposed on this commit, because the artifacts are
   its exact tree.
2. **Current `main`** `ac0a5511a1d4…`: descends from the producer by documentation-only
   merges (PR #45 CodeQL triage, and this PR once merged). These commits touch only
   `docs/` and `docs/release/`; no `src/`, `sql/`, `lab/`, `tools/`, `tests/`, `.github/`,
   `pyproject.toml` or lock file changes. The procedure below verifies that claim at tag time.
3. **Documentation review head**: the exact head of the PR that carries these documents.
   The release notes, this proposal and the review request are therefore NOT files in the
   tag's tree; they are referenced by the `main` commit that contains them.
4. **Frozen artifacts**: run 38187030741 / artifact `zabalack-candidate-38187030741-1`. They are
   never rebound to a later commit. If the tag target ever changes, a new producer run and
   new exact-commit approvals are required.

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
| CodeQL | SUCCESS (python, javascript-typescript) on the producer's push run 38187030664. Alert dispositions are in `docs/release/CODEQL_TRIAGE_20261011.md` at `main` `ac0a5511a1d4…` (merged by PR #45 after the producer; not a file in the frozen tree). A completed workflow is not a disposition or dismissal approval; dismissal needs owner + independent-reviewer agreement (R28). |

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

1. Re-read live `main`. Confirm that `f85212e238fb6322802b163219096db48e428291` is an
   ancestor of it and that `git diff --name-only f85212e2 main` lists only paths under
   `docs/`. Record the list. Any other path means a new producer run is required and this
   proposal is void.
2. Run `python tools/public_audit.py --publication`; require exit 0.
3. Create an annotated tag `v0.1.0b1` on the producer commit `f85212e2…` (not on `main`'s
   head); push the tag only. The tag message names the `main` commit that holds the
   release notes and dispositions.
4. Review the tag's Actions run; compare its artifact hashes with the table above.
   A different hash is a different artifact; stop and record.
5. Create the GitHub prerelease with the release notes. No PyPI upload.
