# Tag proposal: v0.1.0b1 (beta candidate)

Prepared 11 October 2026. This document proposes a tag. It does not create one. The tag
is created only after the owner and the independent reviewer (Codex) each
approve this exact commit in writing, and after the publication audit returns
zero. No upload, release or deployment is authorized by this proposal.

**Amendment, 13 October 2026 (r2, after Codex review TP1-TP5): the tag is on a final producer,
not on candidate #4.** A dry run of the publication audit showed that the procedure could not succeed
on `f85212e2`. The publication register is `config/publication.pending.json` (read by
`tools/public_audit.py`, `publication_inputs`). `config` is in the harness digest scope
(`tools/evidence_utils.py`, `HARNESS_SCOPE`), and `MANIFEST.in` ships `config/*.json` in the sdist.
Completing the register therefore changes the harness tree and the sdist, and needs one new producer
run: the *final producer* (candidate #5). Precedent: candidate #3 became #4 through register inputs
(#44). No code changes.

Everything in "Exact identity", "Artifact hashes" and "What passed at this commit" below is
**HISTORICAL CANDIDATE #4, NOT A TAG TARGET**. Those results and any approvals of #4 are not
approvals of the final producer. The final producer gets its own record ("Final producer record",
below) and fresh exact-commit approvals.

**Publication scope.** The register fields are recorded inputs, not fresh authority and not proof that a
control works. `publication_authorized` is already `true` in the register. It covers the public
repository, the tag and a GitHub prerelease only. `pypi_namespace_confirmed` is a retained gate in the
validator, not permission to upload to PyPI. PyPI publication and production deployment stay separate
owner decisions. No agent edits an approval field to make the audit pass. Each value comes from the owner.

## Exact identity (HISTORICAL CANDIDATE #4, NOT A TAG TARGET)

| Item | Value |
|---|---|
| Tag name | `v0.1.0b1` |
| Candidate #4 frozen producer commit (historical; not the tag target) | `f85212e238fb6322802b163219096db48e428291` (11 October 2026) |
| Current `main` at this proposal's head | `ac0a5511a1d49af2b0d169b82bc58f17fc711f2b` (documentation-only descendants of the producer; see "Identity reconciliation") |
| Documentation review head | this PR's head (the docs below are reviewed here, not in the tag tree) |
| Version | `0.1.0b1` (pyproject, package, manifests, register) |
| CI run | 38187030741 attempt 1 (ci.yml, GitHub-hosted ubuntu-24.04) |
| Scan run | 38187030803 (security.yml) |
| Candidate artifact | `zabalack-candidate-38187030741-1` (7,649,222 B) |

## Identity reconciliation (independent review D1)

Four identities are distinct and must not be conflated:

1. **Frozen producer commit** `f85212e2…` (candidate #4, historical): built the wheel, sdist and
   source zip above in run 38187030741. The tag is no longer proposed on this commit (amendment of
   13 October 2026). The same rule applies to the final producer: the tag goes on the commit whose
   exact tree built the release artifacts.
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

## Artifact hashes (HISTORICAL CANDIDATE #4; SHA-256 from the run's own SHA256SUMS; verified)

| File | SHA-256 |
|---|---|
| `zabalack-0.1.0b1-py3-none-any.whl` | `bf5b017111742c7c6c90a8701286987b20649a05a8aa7fa12515803f1617719b` |
| `zabalack-0.1.0b1.tar.gz` | `ce346c9d562dc80ae094a16df4491d3812c47d1383f57eeaedf13051dbb0b821` |
| `zabalack-0.1.0b1-source.zip` | `9940da37c6b160eae3861e006f278864857fd68bb4898ab7b46011c84bea1cc7` |
| `SHA256SUMS` | `460b990ed078d957ee9b0c6d52890318accbeb299af6fe2a557f67f0bd014fda` |
| `BUILD_PROVENANCE.json` | `232501b7d14f2067e58d32cef53ecfa8a6d31d188f8a720f34ac7e2f6cdfd61b` |

## What passed at candidate #4 (HISTORICAL)

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

- Publication register: verified private security reporting, copyright/third-party approval,
  public-content approval, PyPI namespace confirmation (all `false` today; owner values only).
  Step 2 below states how completeness is verified.
- Independent external security review (xAI Grok 4.7, AI-assisted; starts after the
  repository is public): the request names one commit. Its findings cover the final producer only
  through a new request for the final producer, or through the reviewer's explicit disposition of the
  complete candidate #4 -> final producer delta. Pending external review is not completed coverage
  (see `SECURITY_REVIEW_REQUEST_v0.1.0b1.md`). Internal adversarial pass completed
  11 October 2026; report retained privately (sha256 0bd8c5a9…).
- Owner policy decisions recorded (R24): attestation deferred; environment statement
  as tested; CodeQL gate off until the repository is public, then one completed run
  and triage before the tag (see `POLICY_DECISIONS_v0.1.0b1.md`).
- Release notes reviewed (`RELEASE_NOTES_v0.1.0b1.md`).

## Final producer record (PENDING; filled before approvals)

| Item | Value |
|---|---|
| Register PR | number, base commit, head commit; its delta lists only `config/publication.pending.json` (and `pyproject.toml` only if the repository URL must match, which it already does) |
| Final producer commit (full SHA) | PENDING |
| Whole delta candidate #4 -> final producer | PENDING: path list, split into `docs/` paths (each named and reviewed) and non-`docs/` paths (only the register), with the source and harness digests of both trees recorded separately |
| CI run / attempt / tested SHA | PENDING |
| Scan run; CodeQL run | PENDING |
| Candidate artifact name and size | PENDING |
| Artifact hashes (from the run's own `SHA256SUMS`) | PENDING |
| Package job audit record (`evidence/public-audit.json` inside the artifact) | PENDING: status `PASS` |
| Register completeness (step 2b) | PENDING: empty list |
| Documentation review head | PENDING |

## Approval record (fill in after the final producer record is complete)

Approvals bind the full final producer SHA. They are given after the final inputs and artifacts are
reviewed and before the tag is created. Approvals or results recorded for candidate #4 do not count.

| Party | Decision | Exact final producer commit confirmed | Date (Nassau) | Reference |
|---|---|---|---|---|
| Owner | | | | |
| Codex (independent reviewer) | | | | signed message id |

## Procedure after approval

0. **Final producer.** The owner supplies the register values. A reviewed PR records them on `main`.
   The PR delta (base to head) lists only `config/publication.pending.json`. CI on the resulting
   `main` commit is the final producer run. Fill the "Final producer record". Any non-`docs/` path in
   the whole candidate #4 -> final producer delta, other than the register, stops this proposal.
   Intervening `docs/` changes are allowed only if each one is listed and reviewed. Codex reviews the
   whole delta and the final artifact bytes (owner item 3 applies to the final producer).
1. Re-read live `main`. Confirm that the final producer commit is an ancestor of it and that
   `git diff --name-only <final producer> main` lists only paths under `docs/`. Record the list.
   Any other path means a new producer run is required and this proposal is void.
2. Verify publication readiness in two parts. Neither part runs `collect_ci_evidence.py` locally or
   invents GitHub Actions variables (`docs/ci-evidence.md`):
   a. **Audit on same-run evidence (authentic Actions path).** The final producer run's package job
      must have succeeded. That job collects the run's own evidence, then runs `update_acceptance.py`,
      `dependency_inventory.py`, `public_audit.py --write-report` and `build_release.py` (`ci.yml`).
      Read `evidence/public-audit.json` from that run's candidate artifact and require
      `"status": "PASS"`. A failed package job, or any other status, stops the procedure.
   b. **Register completeness (offline, proposed path, needs review before use).** In a clean
      checkout of the final producer, evaluate only the register function and require an empty list:
      `python -c "import sys; sys.path.insert(0, 'tools'); from pathlib import Path; import public_audit as a; m = a.publication_inputs(Path('.')); print(m); raise SystemExit(1 if m else 0)"`.
      It reads only `config/publication.pending.json` and `pyproject.toml`. It does not evaluate
      evidence binding, which part a covers. Do not run `public_audit.py --publication` on a clean
      checkout: it fails by design (`evidence-not-bound-to-tree`), because committed evidence records
      are bound to earlier trees.
3. Create an annotated tag `v0.1.0b1` on the final producer commit (not on `main`'s head); push the
   tag only. The tag message names the `main` commit that holds the release notes and
   dispositions.
4. **Artifact policy.** The release artifact set is the final producer run's reviewed candidate
   artifact: the wheel, sdist, source zip, `SHA256SUMS` and `BUILD_PROVENANCE.json` recorded in the
   "Final producer record". The tag push starts a separate CI run. Its bytes are not assumed equal,
   because the sdist and source zip include collected evidence with run context and a collection time.
   Download the tag-run artifact and compare it file by file with the release set. Record every
   difference. A difference outside the evidence run-context fields (run ID, attempt, event, ref,
   tested SHA, collection time) stops the release. The prerelease attaches the reviewed release set,
   never tag-run files that were not reviewed.
5. Create the GitHub prerelease with the release notes and the reviewed release artifact set. No
   PyPI upload; PyPI and production deployment remain separate owner decisions.
