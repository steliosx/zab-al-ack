# Tag proposal: v0.1.0b4 (beta candidate)

Prepared 30 October 2026 (Nassau). This document proposes a tag. It does not create one. The tag is
created only after the owner approves the exact commit, the five-file release set and every exception
below, and after the independent reviewer (Codex) agrees on this record. No PyPI upload and no deployment
is authorized by this proposal.

Status: **proposed**. The approval table and the completion record are empty.

Authority to start: owner decision of 30 October 2026, given in the Zabalack Claude session (owner-local
record `OWNER-DECISION-ZABALACK-B4-TAG-20261030.md`): start the 0.1.0b4 tag procedure on `9cff02cb`; GitHub
prerelease only; no PyPI; 0.1.0b2 and 0.1.0b3 get no tag; the notes disclose what did not run; the owner
gives the last approval on the exact commit before the tag. The independent reviewer records it as a
**peer-reported owner decision** until the owner confirms the scope directly to the reviewer.

The tag was held until the scheduled nightly Hypothesis profile passed on the candidate. Three
nightly-profile runs passed on `9cff02cb` (below).

## Binding

| Item | Value |
|---|---|
| Release kind | GitHub prerelease only; **no PyPI upload; no deployment** |
| Tag | `v0.1.0b4`, annotated |
| Producer = tag target = required tag-run tested SHA | `9cff02cb80d98b17d256498797a86c7cb6c1c144` (merge of PR #66; version `0.1.0b4`) |
| Producer run | `ci.yml` run 41039220769, attempt 1 (daily schedule, nightly Hypothesis profile) |
| Producer artifact | `zabalack-candidate-41039220769-1`, artifact ID 12969448645, GitHub digest `sha256:000383ac423f749d0f02dbfddbb82343446245f8fa504c83083ecef1dc204632`; GitHub retention ends 2026-11-02T10:23:03Z, after which only the preserved copy remains |
| Release set (exactly five files from that artifact) | see "Release set" |
| Documentation review head | the exact head of the PR carrying this record, frozen at merge |
| Notes-bearing `main` commit | the merge commit of that PR, named in the tag message |
| Not tagged | `0.1.0b2` and `0.1.0b3`: superseded candidate identities (owner decision, 30 October 2026) |

### Release set

| File | Bytes | SHA-256 |
|---|---:|---|
| `zabalack-0.1.0b4-py3-none-any.whl` | 60236 | `035f7b69773f01493f9d3bdbcd7683853101b60d5b27294feb06b2f7a046afbc` |
| `zabalack-0.1.0b4.tar.gz` | 3715355 | `0ca641336501f85f3c587e05fb2ef5c711864cc3b6b37aa650c965b762aabd72` |
| `zabalack-0.1.0b4-source.zip` | 3936397 | `8ca85bdd6c1bb2f91b3a777f9a41f7f9cf3f802e3ddb205fbb310c66c914b7d6` |
| `SHA256SUMS` | 382 | `93dce58d7cc72000d4f8212915be27b75c69ce4f0574aebb0403e945201e184a` |
| `BUILD_PROVENANCE.json` | 4010 | `befc22463ea42cbfb1030c0a0199eee67c649ab3ddf7507cb977190b707ad914` |

### Runs on the producer commit

All four `ci.yml` runs on `9cff02cb` passed every job. Each built the same wheel
(`035f7b69…`), byte-identical twice inside the run with build backend `setuptools==84.1.0`.

| Run | Event | Hypothesis profile | Native PostgreSQL phase | Candidate artifact digest |
|---|---|---|---|---|
| 40968133596 | push (merge of PR #66) | `pr` | 209 passed, 0 failed | `sha256:053711d0…` |
| 41013846096 | schedule (ledger soak) | `nightly` | 209 passed, 0 failed | `sha256:e7c2d734…` |
| **41039220769** (producer) | schedule (daily) | `nightly` | 209 passed, 0 failed | `sha256:000383ac…` |
| 41063710126 | schedule (ledger soak) | `nightly` | 209 passed, 0 failed | `sha256:12df6d15…` |

The producer run is the daily scheduled run because the owner's condition rests on nightly-profile
evidence and the sdist and source archive ship the producer run's evidence files. The other runs
corroborate it. The sdist, source archive, `SHA256SUMS` and `BUILD_PROVENANCE.json` differ between runs.
Between runs 40968133596 and 41039220769 the sdist and source-archive differences are confined to the 21
files under `evidence/` and the generated `docs/acceptance_matrix.csv`, and `BUILD_PROVENANCE.json`
differs only in the two derived archive hashes.

The Zabalack session downloaded all four candidate artifacts on 30 October 2026, matched each archive to
GitHub's digest and retains the copies privately. The prerelease uploads the five files of the preserved
producer artifact. Those files are evidence from the Zabalack session only, not reviewer inspection,
unless the reviewer inspects them before GitHub's retention ends.

## Exceptions

Each exception needs the owner's explicit approval in the final approval below. None is reported as passed.

1. **Publication register incomplete.** At `9cff02cb`, `config/publication.pending.json` leaves four
   inputs unset: `private_security_reporting_verified`, `copyright_and_third_party_review_approved`,
   `public_content_review_approved` and `pypi_namespace_confirmed`. `tools/public_audit.py --publication`
   does not pass. Register completeness is **waived**, not passed, as for `v0.1.0b1`. The producer run's
   ordinary export audit (`evidence/public-audit.json`) reads `PASS` with 0 findings.
2. **Exact non-documentation delta after the producer.** For `v0.1.0b1` the delta from the tag target
   to `main` was documentation only. Here the only permitted delta from `9cff02cb` to the notes-bearing
   `main` is exactly these five paths, with exactly this content:

   | Path | Content | Source |
   |---|---|---|
   | `.github/workflows/forgehold-smoke.yml` | blob `31327954ef868560b69cca77b990a003c8d8254e` | PR #67 (a dispatch-only CI workflow) |
   | `tests/release/test_ci_collection.py` | blob `60d565ea82f3380998426dfa7ab703247daedf36` | PR #67 (its policy test) |
   | `docs/ci-evidence.md` | blob `7dbaa6706d8c833062fea178b41e225ef3b3594d` | PR #67 |
   | `docs/release/TAG_PROPOSAL_v0.1.0b4.md` | the blob at the documentation review head | this record |
   | `docs/release/RELEASE_NOTES_v0.1.0b4.md` | the blob at the documentation review head | these notes |

   PR #67 merged as `5c607100c33eb1fbea442855164511f51097c7e8`. The tag stays on `9cff02cb`, so the
   released files do not contain PR #67. No other path and no other content is permitted; any later change
   to `main`, including a documentation-only one, stops the procedure until this record names it and the
   owner and reviewer approve it again.
3. **Deferred, as for `v0.1.0b1`:** the private security-report test, the external security review
   (`independent_external_security_review: PENDING`), and reviewer inspection of the release bytes (stays
   **NOT_INSPECTED** unless the reviewer inspects them).
4. **Declared tag-run residuals.** The tag run rebuilds the candidate. Differences from the release set
   in these classes are expected and do not stop the release, provided each differing checksum is shown
   to derive from them:
   - files under `evidence/` that record run context (timings, run identifiers, Hypothesis exploration,
     native-run hashes of raw files that are not retained, SBOM entries for compiled bytecode);
   - the generated `docs/acceptance_matrix.csv`;
   - the sdist, source-archive, `SHA256SUMS` and `BUILD_PROVENANCE.json` hashes that derive from them.
   A wheel difference, or a difference in any other file, stops the release.

## Operative sequence

1. Re-read live `main`. Confirm that `9cff02cb` is an ancestor of it and that `main` is the merge commit
   of the documentation PR carrying this record. List `git diff --name-only 9cff02cb main`. Stop unless the
   list is exactly the five paths of exception 2 and each path's blob at `main` is exactly the one that
   exception names. Record the list and the blob IDs.
2. Owner approval (recorded below) of the exact commit, the release set and exceptions 1 to 4, and the
   independent reviewer's signed agreement on this record. No advisor objection.
3. Create the annotated tag `v0.1.0b4` on `9cff02cb`. Its message names the notes-bearing `main` commit.
   Push the tag only.
4. Tag-run check:
   - the tag run's tested SHA and the resolved tag target must equal `9cff02cb`;
   - every job must pass, and the wheel must be byte-identical to `035f7b69…`;
   - compare the tag run's artifact file by file with the release set, without changing either
     original; only the residual classes of exception 4 are allowed;
   - anything else stops the release;
   - tag-run bytes are never approved through this comparison.
5. Create the GitHub prerelease with `docs/release/RELEASE_NOTES_v0.1.0b4.md` as the notes and **only**
   the five files of the release set as uploaded assets. GitHub also attaches its own "Source code"
   archives of the tag commit; those are not part of the reviewed release set and are not inspected.
   After upload, download the five assets into a new directory and verify them with `sha256sum -c`
   against `SHA256SUMS` and against the table above. Record the completion time.

Each step after step 2 is reviewed by Codex before the next one starts. A clock read is recorded next to
each command.

## Approvals

| Party | Decision | Exact commit confirmed | Date (Nassau) | Reference |
|---|---|---|---|---|
| Owner | pending | | | |
| Codex (independent reviewer) | pending | | | |

## Completion record

Empty until the steps run.
