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

Results recorded in the 21 September snapshot and worksheet stay at their original
identities (`a3317dc`, `3ac7829`, `0bb3749`, `a4b3779`, `8221a3c`, `a43f64f`). Still pending for this
version: required Actions runs, a release build, exact-artifact qualification, independent security
review, approved private-store retention and the remaining owner inputs. Local development-host
checks are recorded outside the repository and are not acceptance evidence.
