# Beta-readiness snapshot — 21 September 2026

Documentation and evidence reconciliation only, prepared while Astra is unavailable.
It changes no runtime, SQL, dependency, workflow, acceptance data, original evidence
or release artifact, and it authorises nothing. Historical reports keep their
bindings; this page says what is established today, by whom, and what remains.
Revised later the same day: PR #28's result, the three evidence classes, the
deduplicated contradictions, the reordered handoff and the regression index.

## Identities

| Item | Value | How established |
|---|---|---|
| `main` | `a3317dc41890ef56411e7597b32c44c70b878416` — "Integrate the a17 development candidate (#25)", merged 2026-09-18 16:32 UTC | read from GitHub on 2026-09-21 |
| Candidate under qualification | PR #26 (draft, open, not merged) head `3ac7829d0ae9c21d23d4f3cf7d522722ab3fc590`, branch `codex/wheel-toolchain-048-20260918`, base `a3317dc` | read from GitHub on 2026-09-21 |
| What PR #26 changes | `docs/RELEASE_AMENDMENTS.md` (R23), `docs/tooling_reference_manifest.json`, `pyproject.toml`, `requirements.lock`: `wheel==0.48.0` in both pin sites. `sql/`, `src/` and `tests/` are byte-identical to `main` (tree ids in [5766446629](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5766446629); diff read directly) | diff read; tree ids reported by Opus |
| Related draft (not a candidate change) | [PR #28](https://github.com/steliosx/zab-al-ack/pull/28) (draft, open, not merged) head `0bb3749bf16d7ba8a0aa50e7fbf4d2948b6079d1` (three commits: `12d5a9b`, `b2ac61e`, `0bb3749`), branch `claude/magical-fermi-feobf7`, base `a3317dc`: adds `tests/postgres/test_native_populated_delivery_upgrade.py` (nine cases; ten at `12d5a9b`), `docs/populated_upgrade_assessment.md`, one `tools/native_ci.py` phase registration and, from `0bb3749`, the optional-services registration in `tools/run_reference_suite.py`; `sql/` and `src/` untouched. Tree digests at `0bb3749`: source `f09a9b34…` (unchanged), harness `721b87fa10cbc162d00a4978e3b1a3779593d390c355557b22cb86e9bd50f499`; test file sha256 `dc36f7120ddbe9e9377658cd6a468a5edd695922f5e680dc846dac095db74ae7` (at `b2ac61e`: harness `04a8a087…`, file `244b240e…`, the revision the 013 receipt names) | read from GitHub and checked out locally on 2026-09-21; digests computed with `tools/evidence_utils.py` |
| Fable advisory branch (proposed corrections, not in the candidate) | `claude/github-repo-access-check-w09pak`, reviewed revision `a4b3779b26bf2fcd9c49b1765214c6f7189c355a` (commits `a031d2f`, `59737a8`, `2a4c42c`, `a4b3779`; tree digests source `6c75de54981486fa397aebf2645a5f12eda965de2f9b4a5ea2a78125345142ca`, harness `c0615ee6cde245ff6ae7ddeac4611f08469ae9feda3c32646ce5d12810cf2158`), the revision Opus cross-reviewed and the 013 receipt binds. The remote branch pointer sits one commit ahead at `a880d13`, the same commit as the logging branch's first commit below, not a second fix; the owner's decision is to leave it there with no force-push, revert or history rewrite. `a4b3779` is the exact reviewed baseline. No PR is open for it | read from GitHub; digests computed with `tools/evidence_utils.py` |
| Fable focused logging change (the one additional fix the owner authorised) | `claude/json-log-url-redaction-20260921` head `8221a3c0052b4f3d37eba5bc4f44532e820d4ee6`, based on `a4b3779`: `a880d13` (redactor keeps the request path and drops the query string of the logged URL; `docs/security_and_data.md`, `CHANGELOG.md`) and `8221a3c` (the regression inspects the whole emitted record and a control proves every record still arrives). `sql/` byte-identical to `a4b3779`. Tree digests: source `8947d5a1b0667ff8daf9397afac315b67f8425fa60807aafeab3c8a35f1a2c7a`, harness `05d384b48327c45cd2352d3ad7925ac030b638f1d3edf0f7d69a93e611b5263b`. Opus's focused review of `a4b3779..8221a3c` is complete for its stated scope ([5767926498](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5767926498)): sound, no defect, not reopened. Reviewable development change, not an accepted release | pushed and read back; digests computed with `tools/evidence_utils.py` |
| Fable access-log route follow-up (tracked finding `opus-qparam-access-log-20260921`) | `claude/access-log-route-20260921` head `a43f64f30373f39895ec5e8721a19f39686b7cec`, based on `8221a3c`: one commit, `tests/security/test_access_log_route.py`, `docs/security_and_data.md`, `docs/deployment.md`, `CHANGELOG.md`; no `src/` or `sql/` change. Tree digests: source `8947d5a1…` (unchanged from `8221a3c`), harness `31577b8a072dac9499c3a643dbe63d9749eb2c2fb83d13866b3efa4eb232269c`. Awaiting Opus's review of this delta only | pushed and read back; digests computed with `tools/evidence_utils.py` |
| Version | `0.1.0a17` (`pyproject.toml`), schema version 4, migrations 001–012 | tree read |
| Candidate artifact | wheel `e5c9e2127501f8c6fe9d0d534bbcd27a7e7d7744acf706f342829906b4a229f4` (56,599 B), built at `a3317dc` by run [35363346604](https://github.com/steliosx/zab-al-ack/actions/runs/35363346604) (PR #25 head `f12663b`) and byte-identical at the `main` push run [35369115934](https://github.com/steliosx/zab-al-ack/actions/runs/35369115934) and at PR #26 run [35375901535](https://github.com/steliosx/zab-al-ack/actions/runs/35375901535); sdist and source ZIP carry distinct identities per producer run | PR #25 body and receipts [5733472124](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5733472124), [5734224881](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5734224881); wheel bytes not inspected here |
| Superseded artifact | a16 wheel `1f072f04…` (PR #24) and the interim a17 wheel `aa0eecf7…`; their qualification is historical and is not carried forward | PR #25 body; `docs/BETA_READINESS.md` historical section |
| Hash binding in the repository | At `a3317dc` no document, manifest or CSV records `e5c9e212…` or `aa0eecf7…`; the only wheel hash any document carries is the a16 `1f072f04…` (`docs/INSTALLED_ACCOUNT_QUALIFICATION.md`, `docs/ARM64_BATCH_QUALIFICATION.md`, `docs/COLD_SOURCE_FAILURE.md`, `docs/RETAINED_LEDGER_ASSESSMENT.md`). This page is the first in-tree record of the a17 candidate hash, taken from PR #25 and the receipts | grep over `*.md`, `*.json`, `*.csv` at `a3317dc` |
| Tree digests at `a3317dc` | source `f09a9b345b3101bf9fd2576848f43a357d5ee1ac94b8140899829178ed3223b9`, harness `15754bd27c14a735de5f2c9e4c161352d4ea0e74dba08633f763bcb7e7d5c8f0` | computed locally with `tools/evidence_utils.py`; match what run 35363346604 recorded per PR #25 |
| Tree digests at PR #26 head | source `f09a9b34…` (unchanged), harness `dc7a8463db4eda8fbb5cfe80656053f9c6a7162484f48254a64292d89f69182a` (pin files are in harness scope) | reported in [5734224881](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5734224881); not recomputed here |

## Three things kept apart

| | What it is | Revision | Evidence class | Retention |
|---|---|---|---|---|
| 1. Baseline a17 / PR #26 evidence | The candidate's required Actions runs, Astra's local ARM64 nightly and installed-path receipts, Opus's x86_64 development-lab runs at `a3317dc` | `a3317dc` / PR #26 head `3ac7829`; wheel `e5c9e212…` | verified where the requirement table says so, otherwise receipts. This is the only evidence about the artifact `e5c9e212…` | Astra's runs: two stores by receipt; Opus's: unretained; the Actions artifacts expired |
| 2. PR #28 test-and-harness changes | Nine populated delivery-ledger upgrade regressions (ten at the first commit; the source-fragment case was removed when the real `Worker.check_health()` replaced a copied predicate), an assessment page, the `tools/native_ci.py` phase registration and the `tools/run_reference_suite.py` optional-services registration; no `sql/` or `src/` change | PR #28 head `0bb3749bf16d7ba8a0aa50e7fbf4d2948b6079d1`; source digest unchanged, harness `721b87fa…` | **reported** (PR body and [5767697688](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5767697688), not inspected): `tools/native_local.py --isolated-lab --phase postgres` at `0bb3749`, PASS, 216 passed / 0 failed / 0 skipped, shutdown 0; `tests/release` 771 passed; the 217/0/0 run at `12d5a9b` and the 216/0/0 run at `b2ac61e` are kept as history by Opus's statement. **Inspected** (Fable, x86_64 native PostgreSQL 18.3 development lab): at `0bb3749` the module 9 passed, 009–012 removed 2 failed / 7 passed, 012 alone removed 1 failed / 8 passed, matching the reported current-head counts; at `12d5a9b` 10 passed, 2/8 and 1/9. Technical review: Fable's same-account read ([5767561108](https://github.com/steliosx/zab-al-ack/pull/28#issuecomment-5767561108)) tracked to disposition below, all twelve items addressed and verified in the tree at `0bb3749`; no review by another party | **pending**: Opus's runs are single-store in an ephemeral session; Fable's re-runs are in `zabalack-pr28-rerun-2026-09-21-fable.tar.gz` (sha256 `98a39050…`, at `12d5a9b`) and `zabalack-pr28-rerun-0bb3749-2026-09-21-fable.tar.gz` (sha256 `ca9c66e420ce285c8d9e736b1ef68bc6006ad4dc738c15dddae3e877966848e4`, at `0bb3749`), held by the owner, not a second store of Opus's runs |
| 3. Proposed runtime/SQL corrections (Fable advisory) | Forward migration 013 (background-fence null safety), four Python fixes (`health.py`, `config.py`, `plugins.py` body and arguments, `query_guard.py`), a pin-consistency gate and a scheduled-lane concurrency change, each with a regression that fails when the fix is removed; plus, on its own branch, the JSON-log URL query-string redaction from the cross-review | `claude/github-repo-access-check-w09pak` at `a4b3779` (cross-reviewed); `claude/json-log-url-redaction-20260921` at `8221a3c` (review complete for its scope); `claude/access-log-route-20260921` at `a43f64f` (docs and tests for the access-log route, awaiting review of that delta). **Not in `a3317dc`, not in PR #26, not in any built wheel** | verified locally on the engines the index names; the first branch cross-reviewed by Opus at `a4b3779` (dispositions below: both SQL findings and the four Python fixes confirmed); the logging change was the one further finding, corrected and reviewed complete; the access-log route follow-up documents where a request line can reach a log and is awaiting review; same-account work, not an external review | `zabalack-reviews-2026-09-21-fable.tar.gz` (sha256 `f6c7dd39399b02bb1ca1cf8a906cb6bfb95bca10cf28b72a5dec9ae10b181391`) and `zabalack-url-redaction-v2-2026-09-21-fable.tar.gz` (sha256 `d460a21b1492b32fa8614b399dc4a121cd21a1a69cd544ac352eca67d38c5124`), held by the owner |

Adopting anything in class 3 that touches `sql/` or `src/` replaces the candidate: new
source digest, new wheel, new required run. Class 2 changes only the harness digest.
Neither class adds evidence about wheel `e5c9e212…`.


### PR #28 results by revision: historical, current, and the separate 013 receipt

| Revision | Cases | Result | Standing |
|---|---|---|---|
| `12d5a9b` (first commit) | 10 | reported: native postgres phase 217 passed / 0 failed / 0 skipped. Inspected by Fable: module 10 passed; 009–012 removed 2 failed / 8 passed; 012 alone removed 1 failed / 9 passed | **historical**; preserved by Opus as its own run and in Fable's archive `98a39050…`; not restated as current |
| `b2ac61e` (review corrections) | 9 | reported: 216 / 0 / 0; the source-fragment case removed when the real `Worker.check_health()` replaced the copied predicate | **historical**; preserved separately by Opus's statement; not inspected by Fable |
| `0bb3749` (current head) | 9 | reported: 216 / 0 / 0, `tests/release` 771. Inspected by Fable: module 9 passed; 009–012 removed 2 failed / 7 passed; 012 alone removed 1 failed / 8 passed | **current**; Fable's archive `ca9c66e4…` |
| 013 compatibility, run `opus-013-compatibility-20260921` | 9 | reported: 9 passed / 0 failed with 013 applied; product `a4b3779`, test revision `b2ac61e` | **separate receipt**, not part of PR #28's results and not a review of 013 (table below); supersedes the 10-passed run at test revision `12d5a9b` |

### Opus's 013 compatibility receipt, separately attributed

Recorded from [5767579558](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5767579558) as a receipt. It supersedes, by Opus's
statement, the 10-passed run at test revision `12d5a9b` in
[5767313592](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5767313592).

| Item | Value | Status |
|---|---|---|
| Run id | `opus-013-compatibility-20260921` | receipt |
| Product under test | `claude/github-repo-access-check-w09pak` `a4b3779b26bf2fcd9c49b1765214c6f7189c355a`, unmodified, base `a3317dc` | receipt; the URL correction now lives on `claude/json-log-url-redaction-20260921` (`8221a3c`, based on `a4b3779`) with `sql/` byte-identical, 013 sha256 `98c35711…` at both (verified here); the advisory branch's remote pointer stays at `a880d13` by the owner's decision, the same commit, not a second fix |
| Test revision | `claude/magical-fermi-feobf7` `b2ac61e5220c7021c80d1403771385be672c0c7b`, test file sha256 `244b240e628434834a8663b7c2ff3d6fc583154448595e59681befc3cf362f92` | receipt; the file digest matches the checked-out head (verified here) |
| Applied migrations | thirteen `APPLIED` hashes, posted as prefixes: 001 `6bb752d0`, 002 `2a1640ce`, 003 `b30b50fc`, 004 `58e06b86`, 005 `2e07b4c1`, 006 `b7dcb6f7`, 007 `bacfd9f7`, 008 `8dc04f4b`, 009 `b30a846b`, 010 `557549c5`, 011 `47b4d98e`, 012 `2b1ec1dc`, 013 `98c35711` | receipt; the thirteen prefixes match the tree files at `a880d13` (verified here); the runner's own lines are in the private archive |
| Result | 9 passed / 0 failed, native PostgreSQL 18.3, x86_64 development lab | receipt, not inspected |
| Command | not stated in the receipt | — |
| Evidence | archive sha256 `af941e51a924ee9e3494d5616e710709fc36d41303c41327dd77e0e7c6ceedf5`, manifest sha256 `09e16a67a66dc77dc29b9b35a44558ad7e941f752189cabe6048f124af4018dd`, delivered privately to the owner | receipt, not inspected |
| Retention | pending, single store, no second copy verified | receipt |
| What it shows | a populated schema-3 installation upgrades 001→013 in one runner pass with the delivery ledger, whole-row proofs, uncertainty ladder and settled/delivered fencing intact | receipt |
| What it does not show | not a correctness review of 013 and not a test-strength conclusion: **a passing compatibility run closes neither**. Eight of the nine cases pass without 009–012 and are not evidence about those migrations either | as the receipt itself states |

### Review findings tracked to disposition

Opus's cross-review of the advisory branch at `a4b3779` ([5767542122](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5767542122);
native PostgreSQL 18.3, x86_64 development lab; review authority only, not an
external security review):

| Finding | Opus's result | Disposition |
|---|---|---|
| 013: background fence bypassed by omitting `worker_id` with no lease held (`record_reconciliation`, `record_host_check`, `record_host_inventory`) | reproduced at 012 (`DID NOT RAISE`, all three); refused at 013 | confirmed, correction sound; no change |
| 013: reconciliation `ok` accepted with no `completion_evidence` key | reproduced at 012; refused at 013; a valid evidence value still accepted (control) | confirmed, correction sound; no change |
| `health.py`, `config.py`, `plugins.py`, `query_guard.py` | 8 failed / 86 passed against the unfixed `src` at `a3317dc`; all four classed as product fixes, sound | confirmed; no change |
| `2a4c42c` scheduled-lane concurrency | tooling, an operational preference rather than a defect fix | agreed; recorded as harness-only in class 3 |
| `plugins.py` incomplete: `record.url` still carries the query string, so a reader search filtering on the staged intent attribute logs the value under Alerta's default `LOG_METHODS` when `LOG_FORMAT=json`; Fable's fixture used a narrower method list | reproduced by Opus; correction proposed, not applied by him | **accepted; corrected on the separate branch `claude/json-log-url-redaction-20260921` at `8221a3c`** (the one additional fix the owner authorised): the redactor keeps the request path and drops the query; the regression runs under Alerta's default `LOG_METHODS` including GET, inspects every field of the emitted record by value, and a control shows five records (three inside a GET search with a query, one on a query-free URL, one outside any request) all arrive with their messages and levels, the path kept and the query-free URL untouched. Fix removed: both cases fail with the value in the emitted record; fix present: module 12 passed, unit/security/rpc/property 326, quality PASS (index). Opus's focused review of the whole `a4b3779..8221a3c` difference is **complete for its stated scope** ([5767926498](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5767926498)): sound, closes the carrier, keeps context, suppresses nothing; discrimination re-verified by him (2 failed / 10 passed against the unfixed source; 326 passed and quality PASS at `8221a3c`). Not reopened; the 326-test run not repeated |
| Free-text `q=` value in an access-log **message** (`opus-qparam-access-log-20260921`, [5767973307](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5767973307)): the redactor rewrites `record.url` but not a query string embedded in a message, and a free-text search term does not match the restricted-evidence pattern | shown by Opus with a **constructed** message (`app.logger.info('"GET %s HTTP/1.1" 200 12', route)`); originating logger not identified by him; no production exposure claimed | **affected route identified and disposition documented** on `claude/access-log-route-20260921` (`a43f64f`), tests and documentation only. Read from the pinned Alerta 9.1.0 and werkzeug 3.1.8 and driven through the real setup: (1) Alerta's own access line is the after-request record `Logger.setup_logging` registers, formatting `request.path`, so the search term never reaches it; it is emitted only at `LOG_LEVEL` `INFO`/`DEBUG` (default `WARNING`); with the adapter, `url` is the path and `reqargs` blank; method, path, protocol, status and length stay. (2) The development server's request line does carry the query but goes to the `werkzeug` logger, which Alerta's configuration holds at `WARNING`; werkzeug warnings still flow; the development server is the e2e lab server, not a supported deployment; a custom `LOG_CONFIG_FILE` raising it to `INFO` would re-expose the query there. (3) A WSGI server's access log (Gunicorn's `gunicorn.access`) and a reverse proxy's access log are separate loggers that do not pass through the adapter; Gunicorn is not in the pinned environment, so its default request line (`%(r)s`, with the query) is documented, not demonstrated; the mitigation is a path-only format at that originating logger (`%(m)s %(U)s %(H)s`), now in `docs/deployment.md` and `docs/security_and_data.md`. No log message is rewritten and no `src/` changes. Tests use a `q=` value the evidence pattern does not match, cover the `json` and `default` formats, keep the evidence redaction on the same chain, and include a negative control showing the assertions would catch a full-path access line. Module 3 passed; unit/security/rpc/property 329; `tests/release` 776; quality PASS. **Awaiting Opus's review of this delta only.** No claim of universal log sanitisation, production exposure or deployment acceptance |
| Not reviewed by Opus: 013 line by line beyond its four deltas, the routine digest not re-derived, `2a4c42c` beyond reading, the `docs/` edits | stated as limits | open; whether that depth is required is the owner's call (worksheet A4) |

Fable's read of PR #28 at `12d5a9b` ([5767561108](https://github.com/steliosx/zab-al-ack/pull/28#issuecomment-5767561108)), Opus's disposition
([5767697688](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5767697688)), checked in the tree at `0bb3749`:

| Item | Opus's disposition | Verified at `0bb3749` |
|---|---|---|
| 2, the "outbound delivery is disabled" wording; 6, the copied capability predicate | resolved at `b2ac61e`: the real `Worker.check_health()` is awaited; the negative control against the production source (`functions_ok` term deleted, test fails, `src/` restored to `ae031554…`) is reported, not inspected | yes: no copied predicate or source-fragment assertion remains |
| 1, `A5`/`T42` tags and 009 not discriminated | fixed: tags `A6, T41`; both coverage gaps named (009 never isolated; 011's positive case not asserted) | yes: docstring reads `(A6, T41)`; the assessment and PR body name both gaps |
| 3, `run_reference_suite.py` optional-services list | fixed | yes: the module is listed |
| 4, PR-body counts | fixed: current-head controls measured (2 failed / 7 passed; 1 failed / 8 passed); the 230/1 and 223/1 pair withdrawn as different target sets | yes: Fable's own controls at `0bb3749` give the same counts |
| 5, `ambiguous` not compared after the stale callback | fixed: every seeded action except the two under test, whole rows | yes |
| 7, `assert_baseline` missing from four tests | fixed: all nine assert the baseline before upgrading | yes: nine call sites, nine tests |
| 8, `reconcile_due` placement | fixed: called in the `abandoned` seed block and said so | yes |
| 9, `before_write` observation unasserted | fixed: asserted in the unsealed-state case | yes |
| 10, 11, 12 (wording; the `expire_lease` exception; test renamed to `test_a_stale_callback_after_the_upgrade_does_not_reopen_settled_work`; control 3 restores and re-asserts; `issued_at`/`execute_before` named; `tests/` tree hash, PR and branch in the candidate table) | fixed | yes |

None of the twelve is declined; nothing is left open from that read. 013 is not adopted into
PR #28's scope (the assessment stays 001–012), which is correct while 013 is advisory only.

## Where the evidence is

The repository's committed `evidence/` records at `a3317dc` were produced on the **a16** tree
(`code_digests` source `15ec73b6…` / harness `503e26c4…`, last changed in `d68fa91`,
2026-09-13). They are historical for a17: `tools/update_acceptance.py`,
`tools/public_audit.py` and `tools/build_release.py` refuse to use them against this
tree (verified: `tools/public_audit.py` at `a3317dc` reports
`evidence-not-bound-to-tree` for every executed-run record), and the `package` job
regenerates them from the same-run producer artifacts.
The a17 producer/package records therefore exist in two places only:

- **GitHub Actions artifacts** of runs 35363346604, 35369115934 and 35375901535 —
  all expired on 2026-09-21 (three-day retention; verified from the artifact
  listings, every `expired: true`).
- **The two private retention stores** on the runner host and the controller,
  described by Astra's receipts. Those receipts are agent reports; the private files
  were not inspected in preparing this page.

Every "result reported in a receipt" below is exactly that.

Fable's advisory work products (copies of the 19–20 September Actions job logs and
the local x86_64 validation of `a3317dc`; the review reports and the 013/Python
validation logs; the PR #28 re-run) are three archives held by the owner, named with
their hashes in the index below. They are not in the repository, are not acceptance
evidence, and contain no raw runner-host records.

## Actions state on 21 September 2026 (read directly)

| Run | Event / head | State | Class |
|---|---|---|---|
| [35363346604](https://github.com/steliosx/zab-al-ack/actions/runs/35363346604) | pull_request, PR #25 head `f12663b` | five jobs success (2026-09-18) | passing producer/package run for the merged content; artifacts expired |
| [35369115934](https://github.com/steliosx/zab-al-ack/actions/runs/35369115934) | push, `main` `a3317dc` | five jobs success (2026-09-18), `pr` profile | passing run at the merged identity; artifacts expired |
| [35375901535](https://github.com/steliosx/zab-al-ack/actions/runs/35375901535) | pull_request, PR #26 head `3ac7829` (tested merge `91c5706…`) | five jobs success (2026-09-18) | passing run for the candidate; artifacts expired |
| [35431246096](https://github.com/steliosx/zab-al-ack/actions/runs/35431246096) | schedule, `main`, `nightly` profile (2026-09-19) | **failure, run incomplete**: the service closed every job exactly 600 s after it started while the runner was still executing. The quality and mutation steps ran to their end on the runner but their uploads were refused (`403 job is completed`); the reference and native phases were cancelled mid-phase. No job produced a complete result; no test failed in the portions that ran, and none of it counts as a pass | infrastructure: runner ↔ Actions session lost; **VM suspension suspected** from the monotonic-clock timing pattern, cause not established until the host diagnostics are read (worksheet B4); not a test failure |
| [35500268131](https://github.com/steliosx/zab-al-ack/actions/runs/35500268131) | schedule, `main` (2026-09-20 08:41 UTC) | **cancelled** at 2026-09-21 08:41 UTC after 24 h queued, never picked up | queued run expired: no runner |
| [35581496129](https://github.com/steliosx/zab-al-ack/actions/runs/35581496129) | schedule, `main` (2026-09-21 09:07 UTC) | **queued**, not picked up as of 20:36 UTC | no runner online |
| [35590681456](https://github.com/steliosx/zab-al-ack/actions/runs/35590681456) | schedule, CodeQL (2026-09-21) | skipped under `vars.ZABALACK_CODEQL_ENABLED` | not a security pass |

Consequences, stated without inferring a green pipeline from the 18 September runs:

- No `nightly`-profile Actions run has ever completed on the ARM64 runner. The
  nightly-profile evidence for the candidate is Astra's local ARM64 run at PR #26
  head (receipt, below) and Opus's WASM/native runs at `a3317dc` on x86_64 (receipt,
  below). `docs/ci-evidence.md` names the daily schedule as the source of the nightly
  profile; that source has produced no result since the runner migration.
- The self-hosted runner `zabalack-fusion-arm64` has not accepted a job since
  2026-09-19 10:34 UTC. Until it is back, nothing that requires "authentic Actions
  allocation" can be produced, including a re-run of the candidate's required checks
  at any new identity.
- The Compose project the 19 September native phase left running
  (`ack-alpha-5b5effab44-postgres`) was not stopped by the hooks, by design; it must be
  inspected and retained before the next native job so the address pool is not
  consumed.

Remaining action for this item: runner host inspection by its owner (power/sleep
settings on the Fusion host, `_diag` logs for 2026-09-19 08:49–10:45 UTC, one
listener), then let the queued schedule run. No workflow, runner or schedule change
is proposed here.

## Requirement-to-evidence table

Legend for the *Result* column: **verified** = read or computed directly for this page;
**receipt** = reported in an agent comment, raw evidence private; **unavailable** =
neither the record nor a receipt exists. Nothing in this table is deployment
acceptance; G1–G8 are `planned/NOT_VERIFIED` in `docs/deployment_gates.csv` and
`evidence/acceptance-results.json` keeps every row `MANUAL_GATE_PENDING`.

| # | Requirement (source) | Candidate / artifact context | Result | Source | Retention | Remaining action | Responsible |
|---|---|---|---|---|---|---|---|
| 1 | Authentic Actions producer-to-package run on the exact commit (`docs/BETA_READINESS.md` "a17 revalidation decision"; `docs/RELEASING.md` "the Actions packaging job requires native success") | `a3317dc` push run 35369115934; PR #26 tested merge `91c5706…` run 35375901535 | verified: both runs show five jobs success on `zabalack-fusion-arm64`; run artifacts expired 2026-09-21 | [35369115934](https://github.com/steliosx/zab-al-ack/actions/runs/35369115934), [35375901535](https://github.com/steliosx/zab-al-ack/actions/runs/35375901535) | receipt: second-store copies verified for both ([5733472124](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5733472124), [5734224881](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5734224881)) | none for these identities; any new identity needs its own run and the runner is offline | Astra (runs), owner (runner host) |
| 2 | Required nightly-profile evidence (`docs/ci-evidence.md` schedule paragraph; issue [5733199366](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5733199366)) | PR #26 head `3ac7829`; `a3317dc` | receipt: local ARM64 native PostgreSQL 207/0/0 and WASM/pure 46/0/0, seed 20260912 ([5734224881](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5734224881)); receipt: x86_64 development lab WASM 183 passed/2 skipped, native 185/0 at `a3317dc` ([5736536191](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5736536191), [5736717695](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5736717695)); verified: no Actions nightly has completed (table above) | as cited | Astra's: receipt, two stores; Opus's: single-store, unretained by its own statement | decide whether the local ARM64 nightly receipt satisfies the requirement or an Actions nightly is required; the latter needs the runner | owner decision; Astra |
| 3 | Reconciliation-gap replay at the merged identity (post-merge handoff, [5733199366](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5733199366)) | `a3317dc` | receipt: fixed sequence passes on native PostgreSQL 18 and WASM ([5734021326](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5734021326)); receipt: old `main` `89a93b1f` fails at the reported assertion and 001–009 pass, attributing the fix to 009 ([5736536191](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5736536191), [5736717695](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5736717695)) | as cited | Astra's: receipt, two stores; Opus's: unretained | none | — |
| 4 | Installed-path qualification of the exact retained wheel with checked migrations and explicit reseal (`docs/BETA_READINESS.md` "one exact supplied-wheel installed path"; PR #25 "has not been performed") | wheel `e5c9e212…`, source `3ac7829`, fresh PostgreSQL 18 and Zabbix 7.0.10 | receipt: four installed/reseal cases, 13 observations passed, all 12 migration checksums, fenced fresh-install resealing ([5734497897](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5734497897)); the first attempt was a private-adapter setup failure, retained as a failure | as cited | receipt, two stores | none for fresh install. **Fresh install is not populated-upgrade proof** (see 5) | Astra |
| 5 | Populated-database upgrade through 008–012 (`.agents/skills/zabalack-migration-review/SKILL.md` "exercise upgrade and fresh install paths") | `a3317dc` / PR #26 (SQL identical) | verified: `tests/postgres/test_native_generation_fence.py` upgrades a populated schema-3 capture/staging database through the runner and reseal; verified: at `a3317dc` no shipped test drives `claim_delivery`/`start_attempt` before upgrading. **Reported** (PR #28 body, not inspected): at head `0bb3749bf16d7ba8a0aa50e7fbf4d2948b6079d1`, `tools/native_local.py --isolated-lab --phase postgres` PASS, 216 passed / 0 failed / 0 skipped (217/0/0 at the first commit `12d5a9b`, kept as history); nine cases seed a schema-3 delivery ledger through `worker_call`/`operator_call` and upgrade it through 009–012 with the runner and explicit reseal; the unsealed window is refused by the real `CaptureHealthProbe` and the real `Worker.check_health()`; no product defect; 009 is never isolated and 011's positive case is not asserted, by the PR's own statement. **Inspected** (Fable, x86_64 native PostgreSQL 18.3 development lab): at `0bb3749` the module 9 passed, 009–012 removed 2 failed / 7 passed, 012 alone removed 1 failed / 8 passed; at `12d5a9b` 10 passed, 2/8, 1/9; migrations restored byte-identically after each control. The SQL exercised is the same object at `a3317dc` and PR #26 head. Development-lab results only, not x64 qualification | PR #28 body; Opus reports [5766923002](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5766923002), [5767697688](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5767697688); Fable bundles `98a39050…` and `ca9c66e420ce285c8d9e736b1ef68bc6006ad4dc738c15dddae3e877966848e4` | **pending**: Opus single-store, ephemeral; Fable's bundles are held by the owner and are not a second store of Opus's runs | technical review of PR #28: Fable's read tracked to disposition above, all twelve addressed at `0bb3749`; no review by another party; disposition of the draft (worksheet A7): merging changes `tests/` and `tools/`, so the harness digest | owner (disposition) |
| 6 | Bounded ambiguous-API recovery observations on the installed wheel (`docs/BETA_READINESS.md` "conservative API-error recovery observations"; `docs/COLD_SOURCE_FAILURE.md`) | wheel `e5c9e212…` at `3ac7829` | receipt: after-effect case (one request, one forwarded write, one source effect; read-only reconciliation confirmed) and no-effect case (pending uncertainty retained) ([5734497897](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5734497897)) | as cited | receipt, two stores | none. **Injected recovery is not a reproduction of the upstream cold-start allocator race**, which remains unrepaired upstream | Astra |
| 7 | Reader-facing confidentiality on the installed wheel (A5 / T40 / T41, `docs/acceptance_matrix.csv` rows 42–43) | wheel `e5c9e212…` at `3ac7829` | receipt: 45 grouped assertions with genuine HTTP authentication ([5734497897](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5734497897)); verified: the repository suites for T40/T41 pass on PGlite and native PostgreSQL 18.3 at `a3317dc` (x86_64 lab) | as cited | receipt, two stores; local run unretained | none for the lab scope; installed-account T40/T41 observations are part of A4/G5 (row 12) | Astra |
| 8 | Real Zabbix 7.0.10 six-case source contract at the candidate (`docs/BETA_READINESS.md`; issue [5734360041](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5734360041)) | PR #26 head | **open**: claimed by Astra as the next bounded task ([5734360041](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5734360041)); the current package marks real-Zabbix NOT_RUN; the earlier six-case pass is bound to the PR #24 a16 head | as cited | — | run the recorder-owned six cases at `3ac7829`; needs the lab and Astra's return | Astra |
| 9 | Quickstart and strict read-only verification of the original Actions package (`docs/RELEASING.md` local preparation; issue [5734360041](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5734360041)) | PR #26 head, package from run 35375901535 | **open**: claimed, not reported; the run's artifact has expired on GitHub, so verification must use the retained private copy | as cited | private copy receipted in [5734224881](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5734224881) | perform against the retained copy; record its hash | Astra |
| 10 | Dependency and secret scans with zero advisories (`docs/RELEASING.md` "Security scans") | `a3317dc` and PR #26 | verified: "Public dependency and secret checks" success at both ([35369115901](https://github.com/steliosx/zab-al-ack/actions/runs/35369115901), [35375901300](https://github.com/steliosx/zab-al-ack/actions/runs/35375901300)); receipt: pip-audit 58 packages, zero advisories, Gitleaks pass ([5733472124](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5733472124)) | as cited | receipt, two stores | none; **a dependency/secret scan is not an application-security review** | — |
| 11 | Current whole-repository security coverage (CodeQL) and independent application-security review (`docs/BETA_READINESS.md` owner decisions; `config/publication.pending.json` `independent_external_security_review: PENDING`) | `a3317dc` | verified: every CodeQL run is **skipped** under `vars.ZABALACK_CODEQL_ENABLED`; verified: no record of the separate Codex Security scan; **unavailable**: no independent external review. A Fable advisory read of `a3317dc` (two automated passes, findings on [#13](https://github.com/steliosx/zab-al-ack/issues/13)) is same-account advisory work, not the independent review | [35590681456](https://github.com/steliosx/zab-al-ack/actions/runs/35590681456) | — | owner: enable the CodeQL gate or record the policy; name the independent reviewer, scope and destination | owner |
| 12 | Installed-account A4/G5 procedure (`docs/INSTALLED_ACCOUNT_QUALIFICATION.md`; `docs/INPUTS_REQUIRED.md` token-permissions row) | approved installation, not the lab | **unavailable**: inputs unassigned; the procedure's baseline paragraph still names the a16 PR #24 wheel | `docs/INSTALLED_ACCOUNT_QUALIFICATION.md` L31–43 | — | supply environment, identities, cohort, fixtures, containment, test authority and evidence destination; then execute against wheel `e5c9e212…` | owner and installation owners |
| 13 | Amendment A2, reference webhook transport controls with negative certificate/hostname/redirect tests (`docs/SPEC_AMENDMENTS.md` A2; `docs/BETA_READINESS.md`) | installed Zabbix | **unavailable** | `docs/INPUTS_REQUIRED.md` "Webhook transport" row | — | inspected installed controls and negative tests before deployment | Zabbix/platform/security owners |
| 14 | G1–G8 deployment gates and the 42-case deployment catalogue (`docs/deployment_gates.csv`; `docs/deployment_acceptance.md`; `docs/BETA_READINESS.md` "two remain manual… 42 cases remain pending") | a deployment, not the lab | verified: `planned/NOT_VERIFIED` for all eight; reference mapping 40 PASS / 2 manual is bound to PR #24 a16 | `docs/deployment_gates.csv` | — | deployment-time; lab results cannot close them | accountable release owner |
| 15 | Native x86_64 qualification (`docs/BETA_READINESS.md` "DEFERRED"; `docs/agents/astra.md`) | a stable beta/RC candidate, a READY `X64_REQUEST` | verified: DEFERRED, no `X64_REQUEST` posted. **Opus's x86_64 development-lab runs at `a3317dc` are not formal x64 qualification** and say so | [5736717695](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5736717695) | unretained | none until a candidate is frozen and Astra publishes the request | Astra, then Opus |
| 16 | Retained-workload targets and long-retained ledger plans (`docs/RETAINED_LEDGER_ASSESSMENT.md`; `docs/BETA_READINESS.md`) | 500-row same-run dataset | receipt/historical: bounded assessment done on a16 data; production-like volume, distribution, arrival/concurrency and SLOs remain owner inputs | `docs/RETAINED_LEDGER_ASSESSMENT.md` | — | owner inputs | owner |
| 17 | Publication register (`config/publication.pending.json`, ten inputs; `docs/RELEASING.md` "Before the first public upload") | `0.1.0a17` | verified: all ten unset (`null`/`false`/`PENDING`), `publication_authorized: false` | `config/publication.pending.json` | — | owner worksheet | owner |
| 18 | Signed attestation and second-platform reproduction policy (`docs/release-provenance.md`; `docs/BETA_READINESS.md` last bullet) | — | verified: templates inactive, never executed | `release/attest.yml.example` | — | owner policy decision | owner |
| 19 | Private retention of every run behind the candidate (`docs/agents/astra.md`; `docs/self-hosted-runner.md`) | runs 35369115934, 35375901535 and the local qualification sets | receipt: archive and manifest hashes, member counts and readback verified for each set ([5733472124](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5733472124), [5734224881](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5734224881), [5734497897](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5734497897)); Opus's runs unretained by his statement; the 19 September failed nightly's hook snapshots exist on the runner host only, unreceipted | as cited | see left | receipt the 19 September attempt when the host is back; nothing else | Astra |
| 20 | Dependency corrections before freezing a replacement candidate (R23 as proposed in PR #26; at `a3317dc` `docs/RELEASE_AMENDMENTS.md` ends at R22; PRs #1–#8) | PR #26 carries wheel 0.48.0; #1, #3 leave `requirements.lock` stale; #4–#8 leave the inspection manifest stale; #7 red | verified: read directly; per-PR dispositions on [#13](https://github.com/steliosx/zab-al-ack/issues/13) (Fable advisory) | PR list | — | owner decides which bumps enter the candidate; each changes the harness identity and needs a new required run | owner, Astra |

Items that are complete for the candidate and need no further action in this
scope: 1, 3, 4 (fresh install), 5 (development lab; regressions in unmerged draft
PR #28), 6, 7 (lab scope), 10. Items whose evidence is a receipt only: 2, 3, 4, 6,
7, 19. Items with no evidence yet: 8, 9, 11, 12, 13, 14, 15, 16, 17, 18.

## Wording reconciled on this page (historical text left as written)

| Where | What it says | What is true on 21 September 2026 |
|---|---|---|
| `docs/BETA_READINESS.md`, "a17 revalidation decision" | a17 "does not change SQL, dependency pins or recovery behavior relative to head `1f23cc25…`" and lists the remaining a17 qualification | That paragraph predates the merge. The merged a17 (`a3317dc`) adds migrations 011 and 012 and the reader-guard rebinding correction relative to `1f23cc25`; the required producer/package run, the one exact supplied-wheel installed path and the bounded recovery observations have since been done for wheel `e5c9e212…` at PR #26 head (rows 1, 4, 6). The paragraph is kept as the decision record; this page is the current state. |
| `ALPHA_REPORT.md`, a17 header | "Forward migration 010 requires explicit resealing"; cites the preceding head `1f23cc25` run 35300274553 | 010, 011 and 012 each require explicit resealing; the run cited is historical for `1f23cc25`, and the a17 runs are those in the identities table. The retained a16 report below it is verbatim history. |
| `evidence/README.md` and the committed `evidence/*.json` | "Zab-al-ack a16 evidence" | Correct as labelled: those records are a16's and are not a17 results. a17's records are in the expired run artifacts and the private stores. |
| `docs/INSTALLED_ACCOUNT_QUALIFICATION.md`, baseline paragraph | baseline `0.1.0a16`, PR #24 wheel `1f072f04…` | The procedure itself says to refresh the candidate before execution; the candidate today is wheel `e5c9e212…` at PR #26 head. |
| `docs/BETA_READINESS.md`, historical PR #24 assessment | "supplied-wheel Compose qualification (four cases, 13 observations …)" | Historical, bound to the a16 wheel; superseded for a17 by row 4. Not rebound. |
| Receipts describing fresh-install resealing | "explicit fenced fresh-install resealing" passed | True for a fresh install. **Fresh install is not populated-upgrade proof**; the populated delivery-ledger upgrade through 009–012 is verified separately, in a development lab, by PR #28 and its re-run (row 5), not by these receipts. |
| Receipts describing ambiguous-outcome recovery | after-effect and no-effect cases passed | True for injected recovery. **Injected recovery is not a reproduction of the upstream cold-start acknowledgement-counter race**, which remains unrepaired upstream (`docs/COLD_SOURCE_FAILURE.md`). |
| Opus's native runs at `a3317dc` | "native PostgreSQL 18.3 … 311 passed" on x86_64 | Local development-lab runs on a packaged 18.x build; **not formal x64 qualification**, which stays DEFERRED behind a READY `X64_REQUEST`. |
| `docs/ARM64_BATCH_QUALIFICATION.md` L102–104 | "No runtime retry, concurrency limit or source database correction was introduced." | True of the PR #24 batch it describes; at `a3317dc` the API-error classification correction (010–012, `docs/COLD_SOURCE_FAILURE.md` L48–55, L70–86) has been introduced. The sentence is scoped by its context, not its wording. |
| `ALPHA_REPORT.md` retained a16 report, L152–153 | "existing schema-3 `zabalack` installations need no migration for a16" | True for a16 and retained verbatim; for a17 the shipped set is 001–012 at schema 4, and `docs/deployment_acceptance.md` L71–91 describes 008 moving 3 to 4 with its backfill. |
| `ALPHA_REPORT.md` a16 report L128, `docs/INDEPENDENT_REVIEW.md` L106–107 | "hosted Actions … remain NOT_RUN" | Historical a16 wording; routine CI now runs on the self-hosted ARM64 runner (`docs/release-provenance.md` L37–38, `docs/security-automation.md` L49–50). |
| `docs/self-hosted-runner.md` L246–247 | "G1–G8/A5/T40/T41/T42 retain their existing requirements" | No T42 exists in `docs/deployment_acceptance.md` (T1–T41) or `docs/acceptance_matrix.csv`; read as T40/T41. |
| Every CI summary | "CodeQL skipped" | **A skipped CodeQL run is not a security pass** (`docs/security-automation.md` L26–29 says the same). |
| PR #26 body and receipts | qualification at PR #26 head | **Evidence at PR #26 head is not evidence that PR #26 has merged.** PR #26 is an open draft; `main` is `a3317dc`. |

## Classification of what remains

**Software verification an agent can perform now** (no authorised access needed):

- Row 5 is performed (PR #28, re-run here at both revisions); Fable's read is closed
  at `0bb3749`; the draft's disposition (owner, worksheet A7) remains.
- Row 9: strict read-only verification of the retained PR #26 package against the
  private copy and hash; quickstart at `3ac7829` (Astra's claim; needs Astra's stores).
- Row 20: dependency corrections for #1, #3–#8 as reviewed, if the owner wants them
  in the candidate; each changes the harness identity and needs a new required run.
- The 19 September nightly attempt's runner-host retention receipt (row 19).

**Installed-environment evidence requiring authorised access:**

- Row 8: six real-Zabbix source-contract cases at the candidate (needs the lab and
  Astra's recorder).
- Row 12: installed-account A4/G5 on the approved installation.
- Row 13: A2 transport controls on the installed Zabbix.
- Row 14: G1–G8 and the 42-case deployment catalogue, at deployment.
- Row 16: production-like retained-workload targets.
- Row 15: native x86_64, only after a frozen candidate and a READY request.
- The runner host itself: nothing in Actions moves until it is back.

**Owner, reviewer or publication decisions** (the worksheet below):

- Row 2: whether the local ARM64 nightly receipt satisfies the nightly-profile
  requirement or an Actions nightly is required.
- Row 11: CodeQL gate, the separate Codex Security scan, and the independent
  application-security reviewer, scope and destination.
- Row 17: the ten publication inputs.
- Row 18: attestation and second-platform policy.
- Class 3 (worksheet A4): whether the proposed runtime/SQL corrections on
  `claude/github-repo-access-check-w09pak` are adopted before the beta identity is
  frozen. Opus's cross-review at `a4b3779` and his focused review of the logging change
  (`8221a3c`) are complete; his review of the access-log route delta (`a43f64f`) is
  pending. Adopting 013 or a Python fix replaces the artifact and reopens rows 1, 4
  and 6 for the new identity.
- Row 20 (worksheet A8, separate): which optional dependency bumps, if any, enter
  the candidate, and whether before the freeze or after the beta record.
- Class 2 (worksheet A7): the disposition of draft PR #28 after its technical
  review.

**Contradictions flagged for decision, not resolved here** (six items reduced to three
decisions; the other two are not contradictions):

1. Nightly-profile evidence source. `docs/ci-evidence.md` makes the daily schedule the
   source; `docs/agents/astra.md` and the receipts treat a local ARM64 nightly at the
   exact head as closing the gap. Both cannot be the requirement at once (worksheet A1).
2. Runner-migration review record. `docs/INPUTS_REQUIRED.md`, `docs/security-automation.md`
   L51–52 and `docs/release-provenance.md` L38–40 each still say the self-hosted
   execution-policy change "needs Opus review"; the migration is merged and in use and no
   record names that review as done. One decision covers all three sentences: record the
   merged review of PR #25 as satisfying them, or keep a single open item. A missing
   record, not a new gate.
3. Attestation signer. `docs/release-provenance.md` L104–107 has the verifier require a
   GitHub-hosted signer while L37–38 route routine CI to the self-hosted runner with no
   hosted fallback (L42–46 deny self-hosted signers). The attestation policy answer
   (worksheet C11) has to say which runner signs, if any.

**Acceptable alternatives, not contradictions:** `docs/BETA_READINESS.md` accepts Astra's
Compose-form labs and `docs/local-services.md` the local forms for the same requirement,
and the receipts use both. Both forms are documented as acceptable; the beta record only
needs to name which form each receipt used. No gate is added.

**Missing documentation, not gates:** no repository document binds a hash to the current
candidate, and four documents still name the a16 wheel `1f072f04…` as their baseline
(identities table; worksheet A6). Recording the a17 identity in-tree is documentation
work, not a qualification step.

Beta, publication, deployment and stable-support boundaries are as the documents
draw them: beta needs rows 1–11 and 19 plus the owner inputs the worksheet lists;
publication additionally needs row 17 (and the audit in `docs/RELEASING.md`);
deployment needs rows 12–14 and 16; stable support additionally needs the
independent external review, native/HA/fleet acceptance and the compatibility and
support policy in `docs/ROADMAP.md`. No percentage is given because none of the
documents defines one.

## Handoff: order of the remaining work

Prerequisite for anything in Actions: the runner host is back and the queued
schedule run has either run or been cancelled deliberately; then receipt the 19
September attempt's snapshots and retire the orphaned Compose project.

1. **Review and decide the proposed product corrections first.** Opus's cross-review
   of class 3 at `a4b3779` and of the logging change (`a4b3779..8221a3c`) are complete;
   his review of the access-log route delta (`claude/access-log-route-20260921`, `a43f64f`) remains with him;
   PR #28's read items are all closed at `0bb3749`; the owner decides adoption
   (worksheet A4, A7). Any adopted runtime or SQL correction replaces
   the candidate artifact, so further release qualification of wheel `e5c9e212…`
   before this decision would qualify an artifact likely to be replaced. The two
   harness-only items (pin gate, concurrency lane) ride the same decision and change
   no wheel.
2. **Freeze the resulting identity.** Either `3ac7829` unchanged, or a new head carrying
   the adopted corrections: required checks on the runner, the wheel rebuilt twice
   byte-identically, rows 1, 4 and 6 repeated for it, retention receipted.
3. **Release qualification at that identity only:** rows 8 and 9 (the six real-Zabbix
   source-contract cases, the quickstart, strict read-only verification of the retained
   package), each with its retention receipt.
4. Owner answers the remaining worksheet items that gate beta (A1 nightly-profile
   form, A2/A3 security review and CodeQL, A6 in-tree identity).
5. Record the beta decision against the exact identity; publication and deployment
   remain separate decisions with their own rows.

**Kept separate: optional dependency upgrades.** PRs #1, #3–#8 (worksheet A8) are not
on this path. If any is adopted it enters before step 2's freeze or waits until after
the beta record; each changes the harness identity and needs its own required run.
None is required for beta by any document.

## Baseline and fixed-regression index (for Opus)

Baselines: `main` `a3317dc` (source `f09a9b34…`, harness `15754bd2…`); PR #26 head
`3ac7829` (`sql/`, `src/` and `tests/` identical to `main`); PR #28 head `0bb3749b…`
(source unchanged, harness `721b87fa…`); advisory branch
`claude/github-repo-access-check-w09pak` at its reviewed revision `a4b3779` (source
`6c75de54…`, harness `c0615ee6…`), four commits that apply on `a3317dc` in order, the
revision Opus cross-reviewed and the 013 receipt binds; the focused logging branch
`claude/json-log-url-redaction-20260921` at `8221a3c` (source `8947d5a1…`, harness
`05d384b4…`), two commits on `a4b3779`, `sql/` byte-identical; the access-log route
branch `claude/access-log-route-20260921` at `a43f64f` (source unchanged, harness `31577b8a…`),
one docs-and-tests commit on `8221a3c`. Every regression below
fails when its fix is removed and passes with it, on the engines named; the logs are in
the archives held by the owner (evidence `zabalack-evidence-2026-09-20-fable.tar.gz`, sha256
`a2299d481690ecea735c77e81bfd4d251a4fde2cbd989deadaec9be419a3eeba`; reviews
`zabalack-reviews-2026-09-21-fable.tar.gz`, sha256 `f6c7dd39…`; PR #28 re-runs
`zabalack-pr28-rerun-2026-09-21-fable.tar.gz`, sha256 `98a39050…` at `12d5a9b`, and
`zabalack-pr28-rerun-0bb3749-2026-09-21-fable.tar.gz`, sha256 `ca9c66e4…` at `0bb3749`;
URL redaction `zabalack-url-redaction-v2-2026-09-21-fable.tar.gz`, sha256
`d460a21b1492b32fa8614b399dc4a121cd21a1a69cd544ac352eca67d38c5124`, which supersedes the
earlier `0e5b4fbb…` archive taken at `a880d13`; access-log route
`zabalack-access-log-route-2026-09-21-fable.tar.gz`, sha256
`32acaa0a58670a4ef7d66ebd26ca69f0e5e20f3a05f49d5ef8326acf312c42bc`). Opus's 013 compatibility archive
(`af941e51…`) is his, held by the owner. Development-lab runs on x86_64, PostgreSQL 18.3
native and PGlite WASM; not the ARM64 runner.

Lab forms (from the repository's own tools): WASM
`.venv/bin/python lab/pglite_exec.py --isolated-lab -- .venv/bin/pytest <targets> -q -p no:cacheprovider`;
native, whole phase, `.venv/bin/python tools/native_local.py --isolated-lab --phase postgres`;
native, targeted, `ACK_LAB_PG_BIN=<pg18/bin> .venv/bin/python lab/local_runtime.py start --isolated-lab`
then `ACK_TEST_ADMIN_DSN=<lab dsn> ACK_TEST_DESTRUCTIVE_LAB=YES_ISOLATED_LAB TZ=UTC .venv/bin/pytest <targets> -q -p no:cacheprovider`;
SQL assertion groups `.venv/bin/python tools/run_sql_checks.py --isolated-lab`. Restore
`evidence/` after `run_quality.py`/`run_reference_suite.py`; never commit their rewrite.

| Correction (commit on the advisory branch) | Files | Regression: fails at `a3317dc` or with the fix removed | Targets | Engines | Evidence member |
|---|---|---|---|---|---|
| Forward migration 013: `worker_call` background fence refuses an absent worker id and an absent lease (`background lease lost`), allowlist gains `record_reconciliation`, `record_host_check`, `record_host_inventory`, evidence guard null-safe (`invalid reconciliation evidence`); 012's body carried whole, three predicate edits, digest re-recorded, explicit reseal (`59737a8`) | `sql/migrations/013_background_fence_null_safety.sql`, `tests/postgres/assertions_extended.sql` (T29 blocks), `docs/sql_manifest.json`, `docs/sql_api.md`, `CHANGELOG.md` | with 013 removed: SQL group T29 fails "reconciliation ok accepted without completion_evidence"; `test_T29_forward_background_fence_migration_and_guards` fails on PGlite; `test_T29_background_leased_operations_refuse_anonymous_callers_and_absent_leases` fails on native (`worker_id required` not raised) | `tests/postgres/test_ambiguous_api_migration.py -k T29`; `tools/run_sql_checks.py --isolated-lab` | PGlite (17 passed / 2 skipped with 013; 1 failed without); native PG 18.3 (24 passed on the affected targets with 013; 1 failed without); SQL groups PASS with 013 | reviews: `validation/wasm_013.log`, `validation/wasm_no013.log`, `validation/native_013_final.log`, `validation/sql013_checks.log` |
| `health.py`: post-fork branch under `_state_lock` with a local lock binding (`a4b3779`) | `src/zabalack/health.py` | `tests/security/test_probe_fork.py::test_T15_T34_two_threads_racing_the_post_fork_branch_stay_mutually_exclusive` | `tests/security/test_probe_fork.py` | unit (mocked) | reviews: `validation/pyfix_validation.log` ("fix-removed spot checks": 8 of 14 fail with the four source fixes reverted; the 6 that pass are positive controls) |
| `config.py`: an endpoint whose `logical_instance` differs from its key is refused (`a4b3779`) | `src/zabalack/config.py` | `tests/unit/test_crypto_policy.py::test_endpoint_without_logical_instance_is_refused_by_config_check` (`::test_endpoint_accepted_by_config_is_resolvable` is the positive control) | `tests/unit/test_crypto_policy.py` | unit | same |
| `plugins.py`: the JSON-log redactor blanks `data` and `reqargs` (`a4b3779`) | `src/zabalack/plugins.py` | `tests/security/test_proof_redaction.py::test_T19_request_body_never_reaches_the_json_log`, `::test_T19_restricted_evidence_is_still_redacted_in_the_json_log` | `tests/security/test_proof_redaction.py` | unit | same |
| `plugins.py`: the redactor drops the query string from the logged `url`, keeping the path (`claude/json-log-url-redaction-20260921`: `a880d13` fix, `8221a3c` regression strengthened; from Opus's cross-review) | `src/zabalack/plugins.py`, `tests/security/test_proof_redaction.py`, `docs/security_and_data.md`, `CHANGELOG.md` | `tests/security/test_proof_redaction.py::test_T19_query_string_never_reaches_the_json_log` (GET reader search with the staged intent value in the query under Alerta's default `LOG_METHODS`; every emitted field checked by value; path, method, endpoint, request id, logger, level and message kept) and `::test_T19_url_redaction_keeps_every_record_and_its_path` (five records across two requests and none, all emitted; query-free URL untouched; `-` outside a request). Without the fix both fail with the value in the emitted record | `tests/security/test_proof_redaction.py` | unit (module 12 passed; unit/security/rpc/property 326; ruff, mypy, pip-check PASS) | URL redaction v2 archive: `url-redaction-validation.log` (includes the complete emitted JSON record), `a4b3779..head.diff` |
| Access-log route for a free-text `q=` value (`claude/access-log-route-20260921`, `a43f64f`; tests and docs, no product change) | `tests/security/test_access_log_route.py`, `docs/security_and_data.md`, `docs/deployment.md`, `CHANGELOG.md` | `test_alerta_access_line_carries_the_path_and_not_the_query` and `..._in_the_default_format_is_path_only_too`: a GET with `q=escalated+per+synthetic-RUNBOOK-77` through Alerta's real `Logger.setup_logging` and the adapter at `LOG_LEVEL=INFO` emits the access line `"GET /synthetic/alerts HTTP/1.1" 200 12` with no query, `url` path-only, `reqargs` blank, a werkzeug warning kept, the werkzeug INFO request line dropped, the evidence redaction intact; `test_the_assertions_would_catch_a_full_path_access_line` is the negative control (a `request.full_path` access line puts the term in the message) | `tests/security/test_access_log_route.py` | unit (module 3 passed; unit/security/rpc/property 329; `tests/release` 776; ruff, mypy, pip-check PASS) | access-log route archive: `access-log-route-validation.log` (route facts from the pinned sources and the complete emitted records), `8221a3c..head.diff` |
| `query_guard.py`: key pattern anchored `\A…\Z`, a key with a newline is refused, not normalised (`a4b3779`) | `src/zabalack/query_guard.py` | `tests/unit/test_plugins_dispatcher.py::test_T40_attribute_key_with_a_newline_is_refused_not_normalised`; `tests/alerta/test_postgres_search_confidentiality.py::test_T40_direct_filter_key_with_a_newline_is_refused` | those two modules | unit; PGlite (88 passed for search confidentiality and reader); native PG 18.3 | same |
| Pin-consistency gate: every `pyproject.toml` pin repeated in `requirements.lock`, runtime lock a subset, every `uses:` commit equal to its inspected commit in the manifests (`a031d2f`; harness only) | `tests/release/test_pin_consistency.py`, `.github/dependabot.yml`, `CONTRIBUTING.md`, `docs/ci-evidence.md` prose | passes at `a3317dc` (pins consistent there); fails when any of PRs #1, #3–#8 is applied without its lock or manifest update, which is its purpose | `tests/release/test_pin_consistency.py` | release suite (874 passed / 1 skipped with `tests/release` and native-support) | reviews: `reviews/dependabot-prs-1-8-review.md`, `validation/pyfix_validation.log` |
| Scheduled-lane concurrency: `schedule` runs get their own group, only pull-request pushes cancel in progress (`2a4c42c`; workflow only, `ci.yml` and `codeql.yml`) | `.github/workflows/ci.yml`, `.github/workflows/codeql.yml`, `tests/release/test_ci_collection.py`, `docs/ci-evidence.md` | `tests/release/test_ci_collection.py::test_scheduled_runs_keep_their_own_concurrency_lane_and_only_pull_request_pushes_cancel` fails against `a3317dc`'s workflows | `tests/release/test_ci_collection.py` | release suite | same |
| PR #28 (class 2, Opus, `0bb3749`): populated delivery-ledger upgrade through 009–012 | `tests/postgres/test_native_populated_delivery_upgrade.py`, `tools/native_ci.py`, `tools/run_reference_suite.py` | inspected at `0bb3749`: `test_an_api_error_on_a_carried_over_attempt_suspends_only_while_it_is_live` fails with 009–012 removed and with 012 alone removed; `test_required_checks_refuse_the_unsealed_intermediate_state` fails with 009–012 removed because the real `Worker.check_health()` requires 010's `api_error_recovery_version`, not because of seal behaviour; the other seven pass without 009–012 and are not evidence about them; 009 is never isolated | `tests/postgres/test_native_populated_delivery_upgrade.py` (native only; skips on WASM) | native PG 18.3 (at `0bb3749`: 9 passed; controls 2 failed / 7 passed and 1 failed / 8 passed; at `12d5a9b`: 10 passed, 2/8, 1/9; reported phase 216/0/0 at `0bb3749`) | PR #28 re-run archives (`12d5a9b` and `0bb3749`) |
