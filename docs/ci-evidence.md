# CI evidence and candidate packaging

The reference workflow runs four required jobs against the same commit:
`quality`, `reference`, `native` and `mutation`. The reference job executes unit/mock and
PostgreSQL WASM phases; the native job requires the disposable Docker Compose
PostgreSQL/PgBouncer lab, including the multi-process delivery fault phase.
The package job depends on all four jobs succeeding. An unavailable native
runtime prevents CI candidate packaging; it does not become a passing native
result. A local development candidate can still record native `NOT_RUN` under
the normal release tooling and must describe that limitation.

Alpha validation runs for every pull request, including stacked PRs, and for
pushes to `main` or any tag. Other branch pushes do not start a second Alpha
run; their validation runs through the pull request. No PR base-branch or path
filter is applied. The schedule, manual dispatch and reusable workflow entrypoint
are retained, as are the workflow/job check names and package dependencies.
The tag filter is explicit because a branch-only push filter would also stop
tag-triggered validation. The dependency/secret and CodeQL workflows retain
their existing events, check names and enablement conditions. Automatic PR
reviews, the separate daily security scan and website automation are unchanged.

## Dedicated self-hosted execution (superseded on 2026-10-10 — see "Hosted execution for the beta" below)

All active workflow jobs select `[self-hosted, Linux, ARM64, zabalack]`. This
explicitly replaces GitHub-hosted execution with one repository-specific Linux
runner on the authorized lab VM; Windows remains the Codex controller. There is
no paid-hosted fallback. An unavailable matching runner leaves work queued or
unexecuted, never passing. The policy change requires Opus technical review and
actual Actions execution before it establishes candidate readiness. Linux ARM64
success does not establish x64 coverage. The selected policy keeps all jobs on
the authorized ARM64 VM; it does not retain a hosted x64 lane or fallback.
Target x86_64 execution is an **open acceptance gate**: retain final-candidate
quality/reference/native/package and installed-environment results on an
authorized x86_64 environment before claiming that target is validated. Added
ARM64 wheel hashes preserve x64 installation pins, not x64 execution evidence.

| Workflow | Preserved entrypoints | Preserved jobs |
|---|---|---|
| Alpha reference checks | Every PR, main/all-tag push, daily schedule, manual, reusable | quality, reference, native, mutation, package |
| Public dependency and secret checks | Every PR/push, manual | scan |
| CodeQL security analysis | Every PR/push, Monday schedule, manual | CodeQL (python), CodeQL (javascript-typescript) |
| Isolated deployment E2E | Manual, explicitly enabled | service-lab |

The runner service must have a workspace and tool cache separate from direct
agent labs. Trusted host hooks preserve the preceding attempt outside the
disposable workspace before resetting it and retain completed, failed and
interrupted attempts before cleanup. Each Python job refuses an existing
`$RUNNER_TEMP/venv`, creates a new virtual environment after the pinned Python
setup action, and selects its interpreter for subsequent installs and commands.
Do not reuse the agent's editable environment. Provision only the required
Python 3.12, Node 22, Git, archive/download tools, Docker Compose and compatible
pinned wheels/images; keep dependency versions and action commits unchanged.
The job UID can rewrite the private archives, and its Docker access is
root-equivalent on the lab VM. These copies are diagnostic retention, not
tamper-evident evidence; preserve reviewed copies outside the VM. The runner
cleans the remaining temporary directory on a best-effort basis. See
[host boundaries and cleanup](self-hosted-runner.md) for the exact lifecycle.

Both native service entrypoints run their complete service/test/cleanup command
under nonblocking `flock` on the host's shared Linux lab lock. Direct agent lab
users must hold that same lock. Contention fails the attempt without resetting
another owner's services. The runner's existing cleanup stops only its generated
projects and preserves synthetic volumes. Cancellation or process termination
does not prove cleanup; preserve the interrupted attempt and inspect its exact
owned resources before another lab starts. Never prune shared Docker resources.

The security job installs the official Gitleaks 8.24.3 Linux x64 release binary (ARM64 before 2026-10-10)
instead of requiring a Go compiler. The release corresponds to the existing
`107a41827bb6698dbbff756b2300537774a1d84c` source pin. The
[`gitleaks_8.24.3_linux_x64.tar.gz` archive](https://github.com/gitleaks/gitleaks/releases/tag/v8.24.3)
is verified before extraction against SHA-256
`9991e0b2903da4c8f6122b5c3186448b927a5da4deef1fe45271c3793f4ee29c`, read from the
[official checksum file](https://github.com/gitleaks/gitleaks/releases/download/v8.24.3/gitleaks_8.24.3_checksums.txt)
on 2026-10-10 (the ARM64 archive used before that date was
`5f2edbe1f49f7b920f9e06e90759947d3c5dfc16f752fb93aaafc17e9d14cf07`, inspected
in the same file on 2026-09-16). The executable must report 8.24.3 before scanning.

CodeQL retains its explicit repository enablement gate and pinned action/CLI.
[CLI 2.27.0 supports native Linux ARM64](https://github.com/github/codeql-cli-binaries/releases/tag/v2.27.0),
and the pinned action selects its ARM64 bundle. Repository Code Scanning
availability, resource sufficiency and successful ingestion remain separate
checks; self-hosting does not turn a skipped analysis into a clean scan.
Before enabling the existing gate, verify that the pinned bundle executes on
this runner and that a real analysis uploads and is ingested successfully.
This migration does not enable CodeQL or satisfy the separate daily scan.

Branches that still contain older hosted-runner definitions retain those
definitions until their owners explicitly incorporate the reviewed migration.
Do not edit another agent's branch or claim the repository has no hosted
execution while such branches still run. Default-branch schedules and manual
entrypoint availability also remain governed by the installed default-branch
workflow. The disabled publication/attestation templates are outside this
migration; their hosted trust policy remains an explicit pending decision in
[release-provenance.md](release-provenance.md).

## Same-run evidence and retention

Each producer first runs `collect_ci_evidence.py prepare`, which discards
generated reports inherited from Git and records the source/harness digests and
authentic Actions repository, run ID, attempt and commit. `GITHUB_SHA` must equal
the checkout's `HEAD`; a PR run normally tests the synthetic merge commit.
For PR events, the context also contains `pull_request.number`, `head_sha` and
`base_sha` selected from the Actions `GITHUB_EVENT_PATH` payload. The event and
base repository must match the run repository; a fork's head repository may
differ. The payload's `merge_commit_sha` never replaces the tested checkout SHA.
Only these identities are exported, not the raw event, PR body or branch names.
All producer preparation records, artifact manifests, the collection and package
verification must agree on the entire context. A changed or missing PR identity
fails that comparison. Historical four-field contexts remain readable without
invented head/base identities, but cannot satisfy a current PR context. Retain
head, base and tested SHA alongside the authentic run in the private record too.
Never invent `GITHUB_*` values for direct-local validation. After its required commands
succeed, `pack` checks the unchanged digests, required phase set, JUnit outcomes,
exact counts, native cleanup, required Ruff/mypy/pip checks and the complete
selected mutation catalogue with behavioral assertion kills. Invalid mutants,
survivors, errors and timeouts fail the mutation gate. It copies only
the selected redacted reports and records their byte hashes and sizes in a
manifest. Raw logs and Hypothesis examples remain in the private build area.

A local Preloop engine run is not an Actions run: it names the run with a UUID
and always reports attempt 1, so `prepare` refuses its context. For that lane
only, `prepare --kind quality --preloop-alpha` and `pack --kind quality
--preloop-alpha` record the engine's own UUID run ID with the producer
`preloop-local-alpha`. They require a `workflow_dispatch` event and accept only
the quality kind. The same checkout, digest, preparation and quality-report
checks apply. `collect`, package verification and release verification keep the
strict GitHub context, so they refuse such an artifact. It is local alpha
evidence for one job and never counts toward collection, packaging or
acceptance.

The package job downloads only artifact names containing its current run ID and
attempt. Each artifact stays in its own directory. Collection rejects missing
jobs, foreign context, stale source/harness digests, changed report bytes,
missing or unexpected files, duplicate JSON fields, noncanonical paths,
symlinks, invalid report schemas and failed/skipped required native tests.
Collection clears prior exported evidence before validation and writes no
selected reports until every artifact validates. It never rewrites report
digests to make old evidence appear current.

After collection, the job regenerates the acceptance mapping, dependency
inventory and export audit, then builds and compares the wheel, normalized
sdist and source archive. Real Zabbix has no service in this workflow and gets a
current, bound `NOT_RUN` record with zero executed counts and no JUnit path.
G1–G8 remain `MANUAL_GATE_PENDING`. Transport manifests are not signed build
attestations and do not replace review of the workflow or test implementation.
The workflow uploads candidate artifacts; it does not publish to a package
index, push a Git tag, deploy a service or authorize production operations.

Native jobs also produce a separately named, allowlisted failure-summary
artifact on success or failure. This contains bounded process and ledger/source
counts from the native diagnostic helper. It contains no raw log messages,
credentials, DSNs, private file paths or generated examples. This diagnostic
artifact is outside the release-evidence download pattern and cannot satisfy
the required native gate. All active artifact uploads now request a three-day
GitHub retrieval window. Alpha (including candidate packages) and deployment-lab
uploads previously requested 14 days; security used the repository default.
The host must retain durable private copies of required attempts, diagnostics,
cleanup results and candidate files before disposable workspace cleanup.
Save required review copies before GitHub expiry. This hosted-storage choice
does not authorize deletion of the VM's retained attempts.
Check artifact sizes and account storage usage against the free allowance;
short retention alone does not prove usage is within it. Upload only existing
allowlisted public reports, not the private retention tree. Preserve the
same-run artifact boundary even though jobs now execute sequentially on one
runner; leftover workspace files cannot substitute for producer artifacts.

Native pytest failure groups may include a `source_test` such as
`tests/postgres/test_stateful_ledger.py::test_generated_delivery_ledger`.
The exporter derives these identifiers from top-level `test_*` function
definitions in the current phase's fixed source files, without importing tests.
It matches the JUnit module/function against that catalog and discards the entire
parameter suffix. Unknown or unavailable source identities leave the existing
failure category and count intact, without an identifier. Different source tests
remain separate groups; parameter variants of the same source test and failure
classification are aggregated.

The bounds are on the source read and on the exported detail, not on how much of
the JUnit is examined. Every `<testcase>` in the document is counted and
classified — the document is already capped at 1 MiB before it is parsed, so
reading all of it is bounded — and `truncated` reports that distinct failure
*groups* were dropped at the export limit, never that cases went unread. A phase
whose failures come after its passes therefore reports them; a cap on cases read
would report `failed: 0` for it. Source is parsed at most once per module named
by the JUnit, resolved from the phase's fixed file list, so growth in one test
file cannot exhaust a shared budget and silently disable identification for
another. Identifier length remains bounded. These identifiers aid diagnosis; they
do not attest execution, recover historical reports or add failure artifacts for
reference/WASM jobs.

Pull requests, main/tag pushes and manual runs select the bounded `pr` Hypothesis
profile. A daily 03:23 UTC schedule selects `nightly`, with larger bounded
generated sequences. Failed examples are minimized by Hypothesis and retained
under the private build directory and the host's durable attempt copy for replay.
Changing workflow routing does not execute a schedule or establish a passing
run. Completion requires a real allocated Actions producer-to-package run on
the reviewed candidate, with run URL, attempt, exact tested SHA, architecture,
artifact results and independent review status retained together.

## Inspected artifact-action interface

GitHub removed the Node.js 20 runtime for JavaScript actions on 2026-09-23, so
every action pin moved to a release whose own `action.yml` declares
`runs.using: node24`: checkout v5.1.0, setup-python v6.3.0, setup-node v5.0.0,
upload-artifact v6.0.0 and download-artifact v7.0.0 (commits and fetched
`action.yml` hashes in `ci_reference_manifest.json`). Every input these
workflows pass exists in the new versions; upload-artifact's `action.yml`
differs from v4.6.2 only in its runtime.

The download action is pinned to
[`actions/download-artifact` v7.0.0](https://github.com/actions/download-artifact/tree/37930b1c2abaa49bbe596cd826c3c89aef350131),
commit `37930b1c2abaa49bbe596cd826c3c89aef350131`, inspected from the official Git
repository on 2026-10-09 (v4.3.0, commit `d3f86a106a0bac45b974a628896c90dbdf5c8093`,
was inspected on 2026-09-11). `action.yml` and `src/download-artifact.ts` establish
that omitting `github-token` selects the current workflow run; `pattern`
filters artifact names and `merge-multiple: false` retains separate directories
when more than one artifact matches. The one source change from v4.3.0 is that a
pattern matching a single artifact now extracts it directly into `path`; the
collector requires exactly the four named evidence directories, so that layout
is refused rather than accepted.
The implementation warns about artifact-service digest mismatches, so the
collector independently requires matching manifest byte hashes and refuses a
mismatch. The workflow retains `contents: read` and does not supply a token for
cross-run or cross-repository downloads.

| Inspected file | SHA-256 |
|---|---|
| `action.yml` | `834f2e0884ebc4b61150ecb61b3538786ee2f9e9910740a74463246268db3bd2` |
| `src/download-artifact.ts` | `f14131bb3c2023b678a13ae75a76523cd945eb58d18154817fd84ea9422b1916` |
| `README.md` | `9925d2eeb338b5462202a2babc074a5becdfd2d4e736c6cd4604dd8b0ed4c286` |

`tests/release/test_ci_collection.py` exercises synthetic artifacts and checks
the workflow dependency/collection order. These are unit tests of the evidence
boundary, not evidence that an Actions workflow, native lab or real Zabbix ran.

The workflow can also be called by the deliberately inactive attestation template.
That template uses the same required jobs and current-run evidence; see
`docs/release-provenance.md`. CodeQL runs separately with narrowly scoped
security-results permissions; it does not grant write access to packaging jobs.

Changes to deployment configuration, service definitions, release templates and
all `.github/` policy files now invalidate the harness digest as well. A passing
run against an older security or deployment policy cannot be exported as current.

## Hosted execution for the beta (owner decision, 10 October 2026)

The dedicated ARM64 runner (`zabalack-fusion-arm64`) is offline under an open
incident hold, so every active workflow job now selects the GitHub-hosted
`ubuntu-24.04` runner. The owner made this decision first-hand on 10 October 2026
to release the beta; the earlier "no paid-hosted fallback" policy above is
superseded for the beta and may be revisited once the ARM64 runner returns.
Consequences recorded honestly: hosted minutes are billed for this private
repository; the native lab's shared lock and its network-retention store move to
runner-local paths under `$HOME` (`tools/native_network_retention.prepare_store`
refuses `RUNNER_TEMP` and the workspace), so retained network evidence is
ephemeral on hosted runners and only the uploaded job artifacts persist; the
security job verifies the Linux x64 Gitleaks 8.24.3 archive against the hash
read from upstream `gitleaks_8.24.3_checksums.txt`
(`9991e0b2903da4c8f6122b5c3186448b927a5da4deef1fe45271c3793f4ee29c`); the
trust gate that refuses outside revisions is kept as defence in depth. Workflow
configuration alone is not a run result: the PR introducing this change is the
first execution of the hosted lane for the full job set.

## Forgehold lane (owner decisions, 9 and 15 October 2026)

`.github/workflows/forgehold-lane.yml` is the Forgehold session's dispatch-only
workflow, adopted onto `main` byte for byte from branch `forgehold/lane`
(commit `113235f0`, file SHA-256
`fdaa73abbc4ccde89d7aae088b41f57ada78a6f1d73c85de8bc01eb6024d0917`). It holds one
job, `quality` (ruff, mypy, `pip check` through `tools/run_quality.py`, the same
install and run as `ci.yml`'s quality job), on `runs-on: [self-hosted, Linux,
X64, forgehold]`. It triggers only on `workflow_dispatch`; pushing the file starts
nothing. The Forgehold lane dispatches it through its local Preloop engine in one
ephemeral SmolVM, not through GitHub's dispatch API, and reports one App check
run named `quality`. The owner permitted local self-hosted execution for the
Zabalack, Alrust, Voalack and Forgehold jobs on 9 October 2026 and approved this
adoption on 15 October 2026. It adds no evidence pack and no artifact upload; the
hosted `ci.yml` jobs remain the candidate evidence. The runner, its registration
and its host boundaries belong to the Forgehold session.

## Forgehold smoke (30 October 2026)

`.github/workflows/forgehold-smoke.yml` is a second dispatch-only workflow for
the same lane. It only checks that the Preloop App and the Forgehold micro-VM
lane reach this repository: one job, `smoke`, on `runs-on: [self-hosted, Linux,
X64, forgehold]`, a three-minute timeout, `contents: read`, a pinned
`actions/checkout` without persisted credentials, and no install or build. It
confirms the checked-out commit is `GITHUB_SHA` and prints two `FH_SMOKE`
lines. The file is copied byte for byte from the Forgehold session's template
(SHA-256 `7bbe12bf72e0d185731cf44bf363958f2d408c7b8de01159705d68ecb7747ac6`),
which is identical in the Alrust, Voalack, Forgehold and Zabalack repositories
and pinned by hash in the Forgehold queue; do not edit it here. Pushing it
starts nothing; the Forgehold queue dispatches it. It was added at the owner's
request as relayed by the Forgehold session on 30 October 2026, under the
9 October 2026 permission for local self-hosted execution. Like the lane's
`quality` job it produces no candidate evidence.

## Ledger soak (owner decision, 29 October 2026)

`.github/workflows/soak.yml` calls `ci.yml` every three hours (`41 */3 * * *`)
and does nothing else: one job, `uses: ./.github/workflows/ci.yml`, no inputs,
no secrets, `contents: read`, schedule only. A called workflow receives its
caller's `github` context, so every soak run is a scheduled run of the full
`ci.yml` (nightly unseeded Hypothesis profile, nightly mutation catalogue,
scheduled evidence collection) on the same free standard `ubuntu-24.04` hosted
runners, with its own concurrency group (`Ledger soak-<ref>`). Its purpose is
volume: rare ledger interleavings that the daily run reaches only every few
nights. The owner's condition is free usage only: this public repository's
standard hosted runners; no paid runners or minutes are to be enabled. If
GitHub stops starting these jobs, the soak moves to the Forgehold laptop lane
(above), coordinated with the Forgehold session, not to paid minutes. Soak runs
are exploration, not candidate evidence; failing focuses and minimized
sequences stay on the runner as in `docs/stateful-testing.md`.
