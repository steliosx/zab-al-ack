# Beta readiness

The candidate under qualification is 0.1.0a18, an alpha under qualification toward beta
(its identity is recorded in `docs/beta_preparation_note_2026-09-22.md`). A beta requires the intended automation to
execute successfully on its exact commit in supported environments, plus the
publication-owner inputs. See `ALPHA_REPORT.md` for current executed results.
The original a16 release narrative below is historical. Its reference, mutation,
property, native, real-Zabbix and deployment-path records retain their original
revision bindings (42 native, 6 real Zabbix 7.0.10, 4 deployment-path cases with
13/13 observations from the installed wheel). Use the dated current ARM64
assessment below for the later PR #24 qualification; do not rebind older results.
Unchanged service-path code supports an inference about a14 and a15; a14's own
records remain NOT_RUN and a15's stand on a15's runs. The independent a12 and
a15 reviews' records — including the a15 review's local Gitleaks result — and
a14's qualitative agent evaluations are retained in the handoff as received and
are not rebound here. Earlier Ackline results remain historical as well.

Implemented in a14: task-specific agent instructions, three focused repository
skills, retained review attempts, separate mutation profiles and packaged
guidance provenance, with qualitative independent workflow evaluations. These
maintenance controls do not establish beta readiness or deployed permissions.

Implemented in a15: published evidence records carry a public file mode and the
export audit rejects an exported file that is not readable to group and other
(R20).

Implemented in a16: the independent a15 review is reproduced and adopted — ten
writer regressions across both public writers, both private writers and both
umasks, and corrections to release prose that overstated the R20 history, called
the profile mutation records required, attributed a15's service runs to a14 and
described the release verifier as stdlib-only (R21). Still required before beta:
G5's effective-role read-back and positive and negative permission tests on the
approved installed account (A4).

Implemented in a6: lint and gradual strict types, generated trust-boundary and
ledger tests, native worker crash/race tests, execution-owned service recording,
and current-run CI artifact collection with fail-closed packaging.

Implemented in a7: curated mutation gates, normal-process service-lab tooling,
opt-in CodeQL and inactive signed-attestation workflow plus offline verifiers.

Executed for the first time in a8, on source-built services (no Docker):

- Native PostgreSQL 18.3 / PgBouncer 1.24.1: all five phases including the
  worker crash/race tests, the generated ledger sequences and the 006→007
  settled-expiry correction (historical Ackline a8 evidence).
- Real Zabbix 7.0.10 API contract and end-to-end case through the a7 recorder
  (historical Ackline a8 evidence).
- The deployment-path lab: installed Alerta HTTPS process and worker CLI,
  ordinary signup/login, distinct password-authenticated SQL logins, media
  import, macro-expanded ingestion, neutral-update filtering, human update and
  recovery (historical Ackline a8 evidence). This run exposed and closed the
  reference webhook's cause-macro defect (A3) and three harness faults (R12).

Executed for the renamed namespace in a10, through the supplied
`tools/native_local.py` and `tools/run_deployment_e2e_local.py` (R14):

- Native PostgreSQL 18.3 / PgBouncer 1.24.1: 41 passed across the five phases,
  including the a9 legacy-schema guards executed on a real cluster.
- Real Zabbix 7.0.10: 5 passed through `tools/record_real_zabbix.py --run`.
- Deployment-path lab: 4 passed, 13/13 observations, from the installed a10
  wheel in a non-editable runner environment (closes the a8 review's F2).

Executed again in a13 on the reviewed tree (native 42, real Zabbix 6 including
the A4 excluded-action denial, deployment lab 4 from the installed a13 wheel).
Still required before beta: effective-role read-back and positive/negative
permission tests on the approved installed account (A4/G5).

Executed in a12 on the a11 (illustrated) tree through the same runners:
native 42 passed (walkthrough case added to the native `alerta` phase), real
Zabbix 5 passed, deployment lab 4 passed from the installed a12 wheel; browser
captures added, including a real Zabbix frontend screenshot of the lab event.

## Current state (21 September 2026)

> **Superseded on 22 September 2026.** The candidate under qualification is now
> `0.1.0a18`, draft PR #29; see [beta_preparation_note_2026-09-22.md](beta_preparation_note_2026-09-22.md).
> The paragraph below is kept unchanged as the 21 September record.

`main` is `a3317dc41890ef56411e7597b32c44c70b878416` (PR #25 merged 2026-09-18) and
the candidate under qualification is the open draft PR #26 at
`3ac7829d0ae9c21d23d4f3cf7d522722ab3fc590`, wheel `e5c9e212…`. The dated
[beta-readiness snapshot](beta_readiness_snapshot_2026-09-21.md) carries the
requirement-to-evidence table, the Actions state, the classification of what
remains and the smallest remaining execution sequence; the
[owner decision worksheet](owner_decision_worksheet_2026-09-21.md) lists the
unresolved inputs. The revalidation decision below is retained as written; it
predates the merge, and the merged a17 additionally carries migrations 011 and
012 and the reader-guard rebinding correction, each of which requires explicit
resealing. PR #26 evidence is evidence at that head, not evidence that PR #26 has
merged.

## a17 revalidation decision (18 September 2026)

PR25 assigns version `0.1.0a17` to the integrated correction. This version change
does not change SQL, dependency pins or recovery behavior relative to head
`1f23cc25a0c4b386f4799a7cc71bd3f1c6451e21`, but it changes the source/harness
identities used by export validation. Freeze these inputs before the new required
Actions producer/package run; do not rebind the completed a16-labelled run.
Keep both a16 artifacts and all failed/interrupted attempts at their actual hashes.

Remaining technical qualification for a17: authentic final-input Actions and
producer/package proof, one exact supplied-wheel installed path, bounded cold-source
and conservative API-error recovery observations, and applicable final local gates.
Test the retained wheel without silently rebuilding it. A naturally successful
cold pair is not proof that the upstream allocator race is fixed; an unobserved
failure path remains unobserved unless separately exercised and labelled.
Do not repeat the historical 500-case warmed batch or unchanged scanner scopes
merely because the candidate version changed. Required check names and evidence
binding rules remain intact. The owner decisions and release sequence below apply.

## Historical PR24 ARM64 qualification assessment (18 September 2026)

The frozen candidate is PR #24 head
`7ae972e8488ab9602c60a4895a98e1204d534207`, base
`013fbc6f99c171cc484cbbaafcec4de418b4dfe7`, version `0.1.0a16`.
The qualified wheel SHA-256 is
`1f072f0443a2499cecb27d45ffcee7401c3d5c22b5dd7c3ae5be627ff8463774`;
the retained local candidate ZIP SHA-256 is
`12dc5fb265d9bb006eb7df20d649ac2cf387acacc497ca1216c88d064140341c`.
This assessment supplements those bytes; it does not rebuild or relabel them.
New documentation is not retroactively included in the qualified artifact.

- Completed: authentic ARM64 Actions producer-to-package execution,
  [run 35264945955, attempt 2](https://github.com/steliosx/zab-al-ack/actions/runs/35264945955/attempts/2),
  tested merge `57bcbf6988bd75a4df92efddcd1ea7959ec6e489` for the head/base above.
  Retained results include native 309 passed, reference 1427 passed/47 skipped,
  12 mutation kills, quality/SQL and package validation. The failed first
  attempt remains separately retained. Required checks for any later integration
  identity still apply.
- Completed separately: supplied-wheel Compose qualification (four cases,
  13 observations at `1401f42db80730f554829b2fd01d87ac437fede0`; identical wheel), six real-Zabbix cases at the
  frozen head, required property-nightly 46/0/0 and quickstart. The local package
  mapping, inventory, export audit, reproducible build and strict context
  verification passed. Raw evidence is privately retained in two stores.
- Completed security dispositions: Actions 35264945934/1, tested merge as above,
  checked 58 exact Python pins with zero advisories/skips and Gitleaks source
  and history with zero findings. A separate local Node audit of the pinned lab
  lock checked two dependencies with zero vulnerabilities. These completed
  scopes must not be rerun merely because this checklist changes.
- Unavailable: a separate current-candidate whole-repository/security report.
  The accessible Daybreak report concerns PR #11 at
  `757806d7ab69e2846b1d2791c93334e667cc7144`, covering cleanup starvation/timing,
  retirement, permissions/resealing, regressions and review/CI history. It is
  historical technical review, not PR #24 approval or the separate Codex
  Security scan. CodeQL run 35264945850 is **SKIPPED**, not passed.
- Pending: installed-account A4/G5, amendment A2, manual T2/T27 and all applicable
  deployment gates. Forty mapped reference cases passed; two remain manual.
  The full deployment catalogue's 42 cases remain pending. Synthetic batch
  observations cannot close full T30 or G6.
- Completed supplemental [native 50/200 batches](ARM64_BATCH_QUALIFICATION.md):
  500 `ok`, 250 fresh writes/comments, 250 already-acknowledged no-write cases,
  with native service/database observations and separately retained failures.
  The warmed pass does not close the disclosed upstream cold-start ID-counter
  race. The [source-backed disposition and recovery correction](COLD_SOURCE_FAILURE.md)
  identifies an additional product API-error classification defect. The frozen
  wheel lacks that correction; a new candidate needs its own focused recovery,
  migration, required Actions and installed-package qualification. Installed-source
  cold-start acceptance remains open.
- Completed bounded [ledger plan assessment](RETAINED_LEDGER_ASSESSMENT.md)
  on the 500-row same-run dataset. Production-like retained volume, state/age
  distribution, arrival/concurrency targets and SLOs remain owner inputs.

Fable is advisory. An unanswered request does not block implementation, focused
tests or candidate preparation. Consume substantive findings from all reviewers;
material permission, migration, concurrency and failure-recovery changes still
need appropriate review before readiness. Record each reviewed SHA and scope.
Automatic Codex PR review counts only for its actual coverage; same-account
technical review is not formal GitHub approval. Independently required approvals
and branch protections remain in force.

Still required before calling this beta ready:

- Preserve the completed exact-candidate Actions and Compose results above.
  Do not restart the completed runner migration. Use the functioning ARM64
  self-hosted runner without paid-hosted fallback. Reassess required checks and
  affected validation if the implementation or final integration identity changes.
  Native x86_64 is **DEFERRED** to its authorized qualification milestone; it is
  not a pass or acceptance. ARM64 and x64 installation pins do not establish
  native x64 execution or installed-environment acceptance.
- Resolve reference webhook transport amendment A2 with inspected installed
  controls and negative certificate/hostname/redirect tests before deployment.
- Observe media, macro and update-operation behaviour on a production-like
  Zabbix instance (the lab instance is fresh and synthetic).
- Execute the [installed-account A4/G5 procedure](INSTALLED_ACCOUNT_QUALIFICATION.md)
  after the exact account, environment, cohort, evidence and authorization inputs
  are supplied. Continue independent authorized lab work meanwhile.
- Review long-retained ledger query plans and production-like batch behavior.
- Assign the ten publication inputs in `config/publication.pending.json`, review
  licence obligations and arrange independent security review.
- Execute reviewed attestation issuance/verification to establish signed
  build/evidence provenance and a second-platform reproduction if those are
  part of the chosen beta release policy.

## Owner decisions and proposed integration/release sequence

The ten publication inputs in `config/publication.pending.json` remain unset:
repository URL/namespace, package owner, primary and backup maintainers, verified
private security reporting, private conduct contact, copyright/third-party
approval, public-content approval, PyPI namespace confirmation and explicit
publication authorization. Repository visibility is not publication approval.

Name the independent security reviewer and agree scope, candidate revision,
private report destination and disposition process. Arrange review before beta
under the existing checklist; completed external review is also a stable-support
requirement. A dependency/secret scan is not an application-security review.
Decide the applicable CodeQL, signed-attestation and second-platform release
policy without silently waiving an existing requirement. Native x64 remains
deferred until an authorized native host and bounded qualification inputs exist.

Proposed sequence, subject to the existing gates and separate authorization:

1. Finish the frozen ARM64 assessments and retain exact-artifact evidence;
   resolve demonstrated defects with a documented revalidation decision.
2. Obtain installed-account inputs and applicable acceptance/review evidence;
   resolve substantive findings and publication-owner decisions.
3. Prepare and review the integration order and exact candidate/base identities.
   Do not merge any PR until explicitly authorized and required protections pass.
4. After an authorized integration, run required checks and affected qualification
   for its actual resulting identity; never relabel old results.
5. Obtain separate tag/publication authorization, complete release audit and
   applicable provenance/attestation steps. Release only with that authorization.
6. Treat deployment as a separate decision requiring G1–G8, A2, A4/G5,
   A5/T40/T41 and every applicable acceptance gate.

G1–G8 remain required for each deployment. Lab results cannot close them.
No push, tag publication, deployment or external notification is authorized by
these instructions. Follow `docs/RELEASING.md` for the reviewable release steps.
