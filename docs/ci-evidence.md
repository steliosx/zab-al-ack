# CI evidence and candidate packaging

The reference workflow runs four required jobs against the same checkout:
`quality`, `reference`, `native` and `mutation`. The reference job executes unit/mock and
PostgreSQL WASM phases; the native job requires the disposable Docker Compose
PostgreSQL/PgBouncer lab, including the multi-process delivery fault phase.
The package job depends on all four jobs succeeding. An unavailable native
runtime prevents CI candidate packaging; it does not become a passing native
result. A local development candidate can still record native `NOT_RUN` under
the normal release tooling and must describe that limitation.

Each producer first runs `collect_ci_evidence.py prepare`, which discards
generated reports inherited from Git and records the source/harness digests and
Actions repository, run ID, attempt and commit. After its required commands
succeed, `pack` checks the unchanged digests, required phase set, JUnit outcomes,
exact counts, native cleanup, required Ruff/mypy/pip checks and the complete
selected mutation catalogue with behavioral assertion kills. Invalid mutants,
survivors, errors and timeouts fail the mutation gate. It copies only
the selected redacted reports and records their byte hashes and sizes in a
manifest. Raw logs and Hypothesis examples remain in the private build area.

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
the required native gate.

Pull requests, pushes and manual runs select the bounded `pr` Hypothesis
profile. A daily 03:23 UTC schedule selects `nightly`, with larger bounded
generated sequences. Failed examples are minimized by Hypothesis and retained
only under the private build directory for local replay. Hosted scheduled jobs
have not been executed by creating this candidate; their first run requires a
separately authorized repository push and GitHub Actions availability.

## Inspected artifact-action interface

Existing checkout, Python, Node and upload action commit pins are preserved.
The new download action is pinned to
[`actions/download-artifact` v4.3.0](https://github.com/actions/download-artifact/tree/d3f86a106a0bac45b974a628896c90dbdf5c8093),
commit `d3f86a106a0bac45b974a628896c90dbdf5c8093`, inspected from the official Git
repository on 2026-09-11. `action.yml` and `src/download-artifact.ts` establish
that omitting `github-token` selects the current workflow run; `pattern`
filters artifact names and `merge-multiple: false` retains separate directories.
The implementation warns about artifact-service digest mismatches, so the
collector independently requires matching manifest byte hashes and refuses a
mismatch. The workflow retains `contents: read` and does not supply a token for
cross-run or cross-repository downloads.

| Inspected file | SHA-256 |
|---|---|
| `action.yml` | `f81d687a5bb7c2c9428a2cfc32fc099c823d56faf533e7d0e1f3cdc0e10f9cc7` |
| `src/download-artifact.ts` | `1c67eb1bb4f77a462522341189f433422c4d35a6999d0dea2a1846e6edfedd80` |
| `README.md` | `c088294825e648f9f1baf71deefd0a401984d97f0fc43d5c5be9a4ed64211639` |

`tests/release/test_ci_collection.py` exercises synthetic artifacts and checks
the workflow dependency/collection order. These are unit tests of the evidence
boundary, not evidence that a hosted workflow, native lab or real Zabbix ran.

The workflow can also be called by the deliberately inactive attestation template.
That template uses the same required jobs and current-run evidence; see
`docs/release-provenance.md`. CodeQL runs separately with narrowly scoped
security-results permissions; it does not grant write access to packaging jobs.

Changes to deployment configuration, service definitions, release templates and
all `.github/` policy files now invalidate the harness digest as well. A passing
run against an older security or deployment policy cannot be exported as current.
