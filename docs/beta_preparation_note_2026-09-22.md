# Beta preparation note — 22 September 2026

This is a local, unpublished assembly of an owner-selected **replacement source
candidate**. The owner chose worksheet option (b), which replaces PR #26 as the beta identity. It is
not a beta record, not a frozen artifact and not release acceptance. The version
remains `0.1.0a17`. No wheel has been built from this tree, and the a17 wheel
`e5c9e212…` does not represent it.

Base: `a3317dc41890ef56411e7597b32c44c70b878416`. Selected content, each applied
once with its original authorship (`cherry-pick -x`):

- PR #26 `3ac7829d0ae9c21d23d4f3cf7d522722ab3fc590` (wheel build-tool pin at 0.48.0).
- Advisory `a031d2fa` (pin consistency), `59737a8c` (migration 013), `a4b3779b`
  (four Python corrections).
- Logging `a880d13c`, `8221a3c0`, `a43f64f3`.
- PR #28 `12d5a9bf`, `b2ac61e`, `0bb3749b` (populated delivery-ledger upgrade).
- PR #27 through `c880b2cf` (readiness snapshot and worksheet).

Excluded: dependency PRs #1–#8, PR #2, scheduled-lane concurrency `2a4c42c`,
website and publishing work, and any generic access-log sanitizer.

The results recorded in the 21 September snapshot and worksheet stay at their
original identities (`a3317dc`, `3ac7829`, `0bb3749`, `a4b3779`, `8221a3c`,
`a43f64f`). This combination has a new source and harness identity. Required
Actions runs, a release build, exact-artifact qualification, independent
security review, approved private-store retention and the remaining owner inputs
are all still pending for it. Local development-host checks of this assembly are recorded
outside the repository and are not acceptance evidence.
