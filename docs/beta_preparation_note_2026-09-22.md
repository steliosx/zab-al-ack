# Beta preparation note — 22 September 2026

This is an owner-selected **replacement source candidate**. The owner chose worksheet option (b)
instead of freezing PR #26 as-is. Version `0.1.0a18` is an alpha under qualification toward beta.
It is not a beta record, not a frozen artifact and not release acceptance. No wheel has been built
from this tree, and the a17 wheel `e5c9e212…` does not represent it.

Base: `a3317dc41890ef56411e7597b32c44c70b878416`. Selected content, each applied
once with its original authorship (`cherry-pick -x`):

- PR #26 `3ac7829d0ae9c21d23d4f3cf7d522722ab3fc590` (wheel build-tool pin at 0.48.0).
- Advisory `a031d2fa` (pin consistency), `59737a8c` (migration 013), `a4b3779b`
  (four Python corrections).
- Logging `a880d13c`, `8221a3c0`, `a43f64f3`.
- PR #28 `12d5a9bf`, `b2ac61e`, `0bb3749b` (populated delivery-ledger upgrade).
- PR #27 through `c880b2cf` (readiness snapshot and worksheet).

The version was assigned afterwards in a separate commit (R24). Excluded: dependency PRs #1–#8,
PR #2, scheduled-lane concurrency `2a4c42c`, website and publishing work, and any generic
access-log sanitizer.

Migration 013 has author verification at its original revision `a4b3779`: the integrity closeout in
[issue #13 comment 5770342725](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5770342725)
(2026-09-22T02:26Z). That closeout sits alongside the cross-review in comment 5767542122.
It is not an independent external review. Every SQL file it names is byte-identical here; its
results stay at `a4b3779`.

An earlier preparation snapshot said there had been no coordination activity after
2026-09-21T22:13Z. That was superseded by the closeout above, which was the newest issue #13
activity when this note was corrected.

## Checkpoints on this branch

Each identity below is a separate checkpoint. Results belong only to the revision they ran on.

| Checkpoint | Commit | What it adds | Source / harness digest |
|---|---|---|---|
| Pre-version preparation | `32f4994483efc85b7210bbab158dcf9d53d8cb33` | the 21 imported commits plus this note | `8947d5a1…` / `4cbbdbfe…` |
| Versioned a18 (24 commits on `a3317dc`) | `4715e37d5e351b5b8f6dc5e78c21fe22c6b05729` | R24 version commit and note correction | `19570eb1…` / `c5866398…` |
| Runner migration | `a8c0b0d`, `752234c`, `3e8f204` | native x64 runner labels, hook platform policy, x64 Gitleaks pin, R25, trust-boundary re-verification | `19570eb1…` / `72299e81…` |
| Integration-review documentation | the commit that adds this table | documentation only (this section, superseding pointers, ALPHA_REPORT label, populated-upgrade addendum) | unchanged from the runner-migration checkpoint |

- **The statement that the workflows and `tests/release/test_ci_collection.py` are unchanged from base is true only at `4715e37`.**
  The runner migration changes all of them.
- **At `32f4994`:** the native (all five phases, separate invocations), WASM, SQL and quickstart results.
- **At `4715e37`:** only quality, pin consistency, `tests/release` and the public audit.
- **Neither is evidence for a later checkpoint.** Required Actions runs at the branch head are pending the replacement x64 runner.
- **The one retained failed local attempt** was caused by the preparer's `umask 0077` wrapper. The three failing cases were the review-runs and release-builder permission checks
  (`test_review_runs.py::test_existing_public_leaf_is_refused_without_chmod` and the two
  `test_sql_manifest.py::test_release_builder_refuses_*` cases), not product defects.

Results recorded in the 21 September snapshot and worksheet stay at their original
identities (`a3317dc`, `3ac7829`, `0bb3749`, `a4b3779`, `8221a3c`, `a43f64f`). Still pending for this
version: required Actions runs, a release build, exact-artifact qualification, independent security
review, approved private-store retention and the remaining owner inputs. Local development-host
checks are recorded outside the repository and are not acceptance evidence.
