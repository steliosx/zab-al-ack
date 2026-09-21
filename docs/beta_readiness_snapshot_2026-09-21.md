# Beta-readiness snapshot — 21 September 2026

Documentation and evidence reconciliation only, prepared while Astra is unavailable.
It changes no runtime, SQL, dependency, workflow, acceptance data, original evidence
or release artifact, and it authorises nothing. Historical reports keep their
bindings; this page says what is established today, by whom, and what remains.

## Identities

| Item | Value | How established |
|---|---|---|
| `main` | `a3317dc41890ef56411e7597b32c44c70b878416` — "Integrate the a17 development candidate (#25)", merged 2026-09-18 16:32 UTC | read from GitHub on 2026-09-21 |
| Candidate under qualification | PR #26 (draft, open, not merged) head `3ac7829d0ae9c21d23d4f3cf7d522722ab3fc590`, branch `codex/wheel-toolchain-048-20260918`, base `a3317dc` | read from GitHub on 2026-09-21 |
| What PR #26 changes | `docs/RELEASE_AMENDMENTS.md` (R23), `docs/tooling_reference_manifest.json`, `pyproject.toml`, `requirements.lock`: `wheel==0.48.0` in both pin sites. `sql/`, `src/` and `tests/` are byte-identical to `main` (tree ids in [5766446629](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5766446629); diff read directly) | diff read; tree ids reported by Opus |
| Version | `0.1.0a17` (`pyproject.toml`), schema version 4, migrations 001–012 | tree read |
| Candidate artifact | wheel `e5c9e2127501f8c6fe9d0d534bbcd27a7e7d7744acf706f342829906b4a229f4` (56,599 B), built at `a3317dc` by run [35363346604](https://github.com/steliosx/zab-al-ack/actions/runs/35363346604) (PR #25 head `f12663b`) and byte-identical at the `main` push run [35369115934](https://github.com/steliosx/zab-al-ack/actions/runs/35369115934) and at PR #26 run [35375901535](https://github.com/steliosx/zab-al-ack/actions/runs/35375901535); sdist and source ZIP carry distinct identities per producer run | PR #25 body and receipts [5733472124](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5733472124), [5734224881](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5734224881); wheel bytes not inspected here |
| Superseded artifact | a16 wheel `1f072f04…` (PR #24) and the interim a17 wheel `aa0eecf7…`; their qualification is historical and is not carried forward | PR #25 body; `docs/BETA_READINESS.md` historical section |
| Hash binding in the repository | At `a3317dc` no document, manifest or CSV records `e5c9e212…` or `aa0eecf7…`; the only wheel hash any document carries is the a16 `1f072f04…` (`docs/INSTALLED_ACCOUNT_QUALIFICATION.md`, `docs/ARM64_BATCH_QUALIFICATION.md`, `docs/COLD_SOURCE_FAILURE.md`, `docs/RETAINED_LEDGER_ASSESSMENT.md`). This page is the first in-tree record of the a17 candidate hash, taken from PR #25 and the receipts | grep over `*.md`, `*.json`, `*.csv` at `a3317dc` |
| Tree digests at `a3317dc` | source `f09a9b345b3101bf9fd2576848f43a357d5ee1ac94b8140899829178ed3223b9`, harness `15754bd27c14a735de5f2c9e4c161352d4ea0e74dba08633f763bcb7e7d5c8f0` | computed locally with `tools/evidence_utils.py`; match what run 35363346604 recorded per PR #25 |
| Tree digests at PR #26 head | source `f09a9b34…` (unchanged), harness `dc7a8463db4eda8fbb5cfe80656053f9c6a7162484f48254a64292d89f69182a` (pin files are in harness scope) | reported in [5734224881](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5734224881); not recomputed here |

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

## Actions state on 21 September 2026 (read directly)

| Run | Event / head | State | Class |
|---|---|---|---|
| [35363346604](https://github.com/steliosx/zab-al-ack/actions/runs/35363346604) | pull_request, PR #25 head `f12663b` | five jobs success (2026-09-18) | passing producer/package run for the merged content; artifacts expired |
| [35369115934](https://github.com/steliosx/zab-al-ack/actions/runs/35369115934) | push, `main` `a3317dc` | five jobs success (2026-09-18), `pr` profile | passing run at the merged identity; artifacts expired |
| [35375901535](https://github.com/steliosx/zab-al-ack/actions/runs/35375901535) | pull_request, PR #26 head `3ac7829` (tested merge `91c5706…`) | five jobs success (2026-09-18) | passing run for the candidate; artifacts expired |
| [35431246096](https://github.com/steliosx/zab-al-ack/actions/runs/35431246096) | schedule, `main`, `nightly` profile (2026-09-19) | **failure**: every job was abandoned by the service exactly 600 s after it started; the runner kept executing (quality and mutation finished their checks, then `upload-artifact` was refused with `403 job is completed`; reference and native were cancelled mid-phase) | infrastructure: runner ↔ Actions session lost, consistent with the runner VM being paused; **not a test failure** (no test failed in any job) |
| [35500268131](https://github.com/steliosx/zab-al-ack/actions/runs/35500268131) | schedule, `main` (2026-09-20 08:41 UTC) | **cancelled** at 2026-09-21 08:41 UTC after 24 h queued, never picked up | queued run expired: no runner |
| [35581496129](https://github.com/steliosx/zab-al-ack/actions/runs/35581496129) | schedule, `main` (2026-09-21 09:07 UTC) | **queued**, not picked up as of 19:45 UTC | no runner online |
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
| 5 | Populated-database upgrade through 008–012 (`.agents/skills/zabalack-migration-review/SKILL.md` "exercise upgrade and fresh install paths") | `a3317dc` / PR #26 (SQL identical) | verified: `tests/postgres/test_native_generation_fence.py` upgrades a populated schema-3 capture/staging database through the runner and reseal; **unavailable** for a populated delivery ledger through 009–012 (no test drives `claim_delivery`/`start_attempt` before upgrading) | Opus claim [5766446629](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5766446629), 2026-09-21 | pending | **Opus populated-ledger verification: PENDING**, result not yet posted; record it when it exists | Opus (claimed) |
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
scope: 1, 3, 4 (fresh install), 6, 7 (lab scope), 10. Items whose evidence is a
receipt only: 2, 3, 4, 6, 7, 19. Items with no evidence yet: 5 (pending), 8, 9, 11,
12, 13, 14, 15, 16, 17, 18.

## Wording reconciled on this page (historical text left as written)

| Where | What it says | What is true on 21 September 2026 |
|---|---|---|
| `docs/BETA_READINESS.md`, "a17 revalidation decision" | a17 "does not change SQL, dependency pins or recovery behavior relative to head `1f23cc25…`" and lists the remaining a17 qualification | That paragraph predates the merge. The merged a17 (`a3317dc`) adds migrations 011 and 012 and the reader-guard rebinding correction relative to `1f23cc25`; the required producer/package run, the one exact supplied-wheel installed path and the bounded recovery observations have since been done for wheel `e5c9e212…` at PR #26 head (rows 1, 4, 6). The paragraph is kept as the decision record; this page is the current state. |
| `ALPHA_REPORT.md`, a17 header | "Forward migration 010 requires explicit resealing"; cites the preceding head `1f23cc25` run 35300274553 | 010, 011 and 012 each require explicit resealing; the run cited is historical for `1f23cc25`, and the a17 runs are those in the identities table. The retained a16 report below it is verbatim history. |
| `evidence/README.md` and the committed `evidence/*.json` | "Zab-al-ack a16 evidence" | Correct as labelled: those records are a16's and are not a17 results. a17's records are in the expired run artifacts and the private stores. |
| `docs/INSTALLED_ACCOUNT_QUALIFICATION.md`, baseline paragraph | baseline `0.1.0a16`, PR #24 wheel `1f072f04…` | The procedure itself says to refresh the candidate before execution; the candidate today is wheel `e5c9e212…` at PR #26 head. |
| `docs/BETA_READINESS.md`, historical PR #24 assessment | "supplied-wheel Compose qualification (four cases, 13 observations …)" | Historical, bound to the a16 wheel; superseded for a17 by row 4. Not rebound. |
| Receipts describing fresh-install resealing | "explicit fenced fresh-install resealing" passed | True for a fresh install. **Fresh install is not populated-upgrade proof**; the populated delivery-ledger upgrade through 009–012 is Opus's pending claim (row 5). |
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

- Row 5: populated delivery-ledger upgrade through 009–012 (Opus, pending).
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
- Row 20: which dependency bumps enter the candidate, and whether the Fable
  advisory corrections (branch `claude/github-repo-access-check-w09pak`: pin
  gate, forward migration 013, scheduled-lane concurrency, four Python fixes) are
  adopted before or after beta; adopting 013 changes the SQL identity and reopens
  rows 1, 4 and 6 for the new identity.

**Contradictions flagged for decision, not resolved here:**

1. `docs/ci-evidence.md` makes the daily schedule the source of nightly-profile
   evidence; `docs/agents/astra.md` and the receipts treat a local ARM64 nightly at
   the exact head as closing "the requested nightly-profile gap in this scope". Both
   cannot be the requirement at once.
2. `docs/INPUTS_REQUIRED.md` still asks to "obtain Opus review of the explicit
   execution-policy change" for the runner migration; the migration is complete and
   in use, and no such review is recorded as a distinct item. Decide whether that
   input is satisfied by the merged review or still open.
3. `docs/BETA_READINESS.md` names Astra's Compose-form labs and `docs/local-services.md`
   the local forms as acceptable for the same requirement; the receipts mix both.
   State which form the beta record must carry.
4. `docs/security-automation.md` L51–52 and `docs/release-provenance.md` L38–40 both
   say the self-hosted execution policy "needs Opus review"; no record names that
   review as done. Either record the merged review of PR #25 as satisfying it or
   keep it open (same decision as item 2).
5. `docs/release-provenance.md` L104–107 has the attestation consumer enforce "a
   GitHub-hosted runner" while L37–38 route routine CI to the self-hosted runner with
   no hosted fallback (reconciled later in the file at L42–46 by denying self-hosted
   signers). The attestation policy decision in the worksheet has to say which runner
   signs, if any.
6. No repository document binds a hash to the current candidate (identities table);
   four documents still name the a16 wheel `1f072f04…` as the baseline. Decide where
   the a17 candidate identity is recorded in-tree before the beta record is written.

Beta, publication, deployment and stable-support boundaries are as the documents
draw them: beta needs rows 1–11 and 19 plus the owner inputs the worksheet lists;
publication additionally needs row 17 (and the audit in `docs/RELEASING.md`);
deployment needs rows 12–14 and 16; stable support additionally needs the
independent external review, native/HA/fleet acceptance and the compatibility and
support policy in `docs/ROADMAP.md`. No percentage is given because none of the
documents defines one.

## Astra-return handoff: smallest remaining execution sequence

Prerequisite for anything in Actions: the runner host is back and the queued
schedule run has either run or been cancelled deliberately; then receipt the 19
September attempt's snapshots and retire the orphaned Compose project.

1. Read Opus's populated-ledger result (row 5) when posted; if it found a defect,
   stop here and return to correction.
2. At PR #26 head `3ac7829`: the six real-Zabbix source-contract cases, the
   quickstart, and strict read-only verification of the retained package (rows 8, 9),
   each with its retention receipt.
3. Owner answers the worksheet items that gate beta (nightly-profile form, security
   review arrangement, dependency and advisory-correction adoption). If any adopted
   change alters `sql/`, `src/`, `tests/`, the pins or the workflows, freeze a new
   identity, run the required checks on the runner and repeat rows 4 and 6 for it;
   otherwise the candidate identity stays `3ac7829`.
4. Record the beta decision against the exact identity; publication and deployment
   remain separate decisions with their own rows.
