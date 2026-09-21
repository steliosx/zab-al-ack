# Owner decision worksheet — 21 September 2026

Unresolved inputs only. Facts are prefilled where they were read directly;
nothing here infers approval, maintainer acceptance, package ownership or a
completed security review. `config/publication.pending.json` is unchanged by this
page and remains the register of record. Context and evidence:
[beta_readiness_snapshot_2026-09-21.md](beta_readiness_snapshot_2026-09-21.md).

## A. Decisions that gate the beta record

| # | Decision | Prefilled facts | Owner's answer |
|---|---|---|---|
| A1 | Which form of nightly-profile evidence the beta record requires: the local ARM64 nightly at the exact head (Astra's receipt [5734224881](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5734224881): native 207/0/0, WASM/pure 46/0/0, seed 20260912) or a completed `schedule` run of `ci.yml` on the runner. | No scheduled run has completed on the ARM64 runner; the 19 September run is incomplete (the service closed every job at 600 s; VM suspension suspected, cause not yet established), the 20 September run expired queued, the 21 September run is queued. | |
| A2 | Application-security review: who reviews, against which identity (`a3317dc` or PR #26 head `3ac7829`), what scope, where the private report goes, and how dispositions are recorded. | `config/publication.pending.json`: `independent_external_security_review: PENDING`. No record of the separate Codex Security scan was found. A same-account Fable advisory read exists and does not satisfy this input. | |
| A3 | CodeQL: enable `vars.ZABALACK_CODEQL_ENABLED` for a real analysis on the runner, or record the policy that excludes it; with it, the Code Scanning availability and SARIF source-visibility review, and whether a ruleset requires CodeQL results (`docs/security-automation.md` L11–24, L45–47). | Every CodeQL run is skipped; the pinned bundle has not been shown to execute on the ARM64 runner. | |
| A4 | Whether the proposed runtime/SQL corrections on the Fable advisory branch `claude/github-repo-access-check-w09pak` (`a4b3779`: forward migration 013; four Python fixes in `health.py`, `config.py`, `plugins.py`, `query_guard.py`; plus the harness-only pin gate and scheduled-lane concurrency) are adopted before the beta identity is frozen, after Opus's technical review as runtime/SQL owner. | Not in `a3317dc`, PR #26 or any built wheel. Adopting 013 or any Python fix replaces the candidate artifact (new source digest, new wheel, new required run; snapshot rows 1, 4 and 6 repeated); the two harness-only items change the harness digest only. The snapshot's regression index lists each fix, its regression, targets and evidence. | |
| A5 | Disposition of PR #2 (superseded by PR #26) and whether PR #26 itself is merged before or after the beta record. | PR #26 is an open draft, `mergeable_state: clean`; it does not change `sql/`, `src/` or `tests/`. | |
| A6 | Where the a17 candidate hash is recorded in-tree: at `a3317dc` no document carries `e5c9e212…`, and four documents still name the a16 wheel `1f072f04…` as the baseline. | snapshot, identities table | |
| A7 | Disposition of draft [PR #28](https://github.com/steliosx/zab-al-ack/pull/28) (populated delivery-ledger upgrade regressions: tests, an assessment page, one `tools/native_ci.py` registration): merge before or after the beta record, or hold, once its technical review is cleared. | Head `12d5a9bf27549fbcddb645a0fffcc2e95b468cc9`, base `a3317dc`, open draft. Reported (PR body, not inspected): native postgres phase PASS 217/0/0. Inspected (Fable, x86_64 development lab): module 10 passed, both discrimination controls reproduce (snapshot row 5). Technical review: Fable's same-account read ([5767561108](https://github.com/steliosx/zab-al-ack/pull/28#issuecomment-5767561108)) lists four must-fix items (unsupported `A5`/`T42` tags and 009 not discriminated; a false "outbound delivery is disabled" sentence; missing `run_reference_suite.py` registration; inconsistent PR-body counts), five should-fix and wording items, none changing the no-defect result; corrections pending on the draft. Retention pending for both runs. Merging changes `tests/` and `tools/`, hence the harness digest, and adds ten cases to the native postgres phase. | |
| A8 | Optional dependency upgrades, kept separate from A4: which of PRs #1, #3–#8 enter the candidate, if any, and whether before the freeze or after the beta record. | Fable's read-only review (private archive, `reviews/dependabot-prs-1-8-review.md`): #1 and #3 leave `requirements.lock` stale, #4–#8 leave `docs/ci_reference_manifest.json` stale, #7's own run is red; PR #2 is superseded by PR #26. Each adopted bump changes the harness identity and needs its own required run. No document requires any of them for beta. | |

## B. Installed-environment inputs (no lab result can supply these)

| # | Input | Register | Owner's answer |
|---|---|---|---|
| B1 | Approved beta/staging installation, identities, cohort, fixtures, containment, test authority and evidence destination for the A4/G5 procedure. | `docs/INSTALLED_ACCOUNT_QUALIFICATION.md`, "Required inputs and owners" | |
| B2 | A2: installed webhook transport controls and who runs the negative certificate, hostname and redirect tests. | `docs/INPUTS_REQUIRED.md`, "Webhook transport" | |
| B3 | Production-like retained-workload inputs for the ledger assessment: retained days, volumes and distributions, cohort counts and arrival rates, approved hardware and settings, latency/throughput targets, contained environment and evidence destination. | `docs/RETAINED_LEDGER_ASSESSMENT.md` L63–77 | |
| B4 | The runner host: confirm power management is disabled while the VM serves jobs, and who receipts the 19 September attempt. | snapshot, "Actions state" | |

## C. Publication inputs (`config/publication.pending.json`, all currently unset)

| # | Field | Current value | Owner's answer |
|---|---|---|---|
| C1 | `repository_url` | `null` | |
| C2 | `package_owner` | `null` | |
| C3 | `primary_maintainer` | `null` | |
| C4 | `backup_maintainer` | `null` | |
| C5 | `private_security_reporting_verified` | `false` | |
| C6 | `private_conduct_contact` | `null` | |
| C7 | `copyright_and_third_party_review_approved` | `false` | |
| C8 | `public_content_review_approved` | `false` | |
| C9 | `pypi_namespace_confirmed` | `false` | |
| C10 | `publication_authorized` | `false` | |
| C11 | Attestation issuance and second-platform reproduction policy (`docs/release-provenance.md`), including which runner may sign: the consumer verifier requires a GitHub-hosted signer (L104–107) while routine CI is self-hosted with no hosted fallback (L37–38). | templates inactive, never run; `release-attestation` environment and reviewers unconfigured | |

## D. Later decisions, listed so they are not mistaken for beta inputs

- Native x86_64 qualification: DEFERRED until a frozen beta/RC candidate and a
  READY `X64_REQUEST` (`docs/agents/astra.md`).
- Deployment: G1–G8, the 42-case catalogue and `production_deployment_authorized`
  (currently `false`) are separate decisions per deployment.
- Stable support: independent external security review, native/HA/fleet
  acceptance, compatibility and support policy (`docs/ROADMAP.md`).
