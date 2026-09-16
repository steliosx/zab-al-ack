# Contributing

Start with README.md and the isolated quickstart. Python 3.12 is the alpha's
supported interpreter. Install exact development pins from requirements.lock;
install the separate hash-pinned requirements-quality.lock as described in
`docs/quality-checks.md`; install the project with `--no-deps -e .` to avoid an implicit Alerta upgrade.

```bash
python tools/run_quality.py
python tools/run_sql_checks.py --isolated-lab
python tools/run_reference_suite.py --wasm
python tools/update_acceptance.py
python tools/public_audit.py
python tools/build_release.py
```

Native integration CI uses `python tools/native_ci.py --isolated-lab` with Docker
Compose. Real Zabbix tests require the explicit synthetic fixture in
`lab/real_zabbix.md`. Unit/mocked, WASM, native and deployment evidence are
separate; a skipped case is never a pass. Don't add tests that merely mirror an
implementation detail. New tests should demonstrate a meaningful behaviour or
failure boundary and map to the acceptance catalogue where applicable.

Keep changes narrowly scoped. Explain the problem, resulting behaviour, actual
validation and compatibility implications in the pull request. Changes to
signing, actor/tenant authority, SQL grants, delivery/lease state, logging or
migrations require review by a maintainer familiar with that boundary. Never
weaken a test or broaden a grant to get a green run. Record source incompatibility
as a narrow amendment in `docs/SPEC_AMENDMENTS.md`.

Do not edit migration files that shipped in a tagged public release or were
applied to any tracked installation; add a versioned forward migration and
document non-destructive rollback/hold behaviour. Before the first public tag,
migrations may still be corrected in place, and `sql/migrate.py` refuses to
rerun a changed file on an installation that already applied it. Alpha Python internals may
change, but signature formats, persisted identities and ledgers still need an
explicit compatibility plan. Keep production configuration inert.

Submit only material you have the right to contribute. Contributions are offered
under the project's Apache-2.0 licence. Include a `Signed-off-by` trailer using
your normal Git identity to certify the [Developer Certificate of Origin 1.1](https://developercertificate.org/).
There is no separate CLA in this alpha. Do not attach real tokens, signing keys,
private histories, customer identifiers or sensitive inventories to an issue.
Follow SECURITY.md for vulnerability reports.

`requirements.lock`, `requirements-runtime.lock` and `requirements-scanner.lock`
are refreshed by hand (Dependabot only tracks the `pyproject.toml` pins); CI runs
`pip check` to catch pyproject/lock drift. The quality, SQL and reference
runners rewrite committed files under `evidence/` (`quality-run.json`,
`runtime_wasm_sql.json`, `test-summary.json`, `*.xml`, `reference-run.json`,
`batch-*-supplemental.json`), so an ordinary local check leaves the tree
modified. Run checks in an isolated checkout or worktree that holds no other
uncommitted work, and inspect what changed afterwards (`git status --porcelain
evidence`, `git diff -- evidence`) before cleaning up. Records the task needs,
including failed or interrupted attempts, stay retained: `tools/run_review.py`
keeps each attempt under `build/review-runs/`, and other private output stays
under `build/private/`; copy anything else you must keep out of the checkout
before restoring. Do not use a blanket restore such as `git checkout --
evidence` on a checkout that may hold the only copy of a required record or
unrelated work; restore only the files a check you ran rewrote, and stage only
intended changes. Commit regenerated evidence only in a deliberate
release-preparation change, with `TZ=UTC` so timestamps carry no local zone.
Run records carry `code_digests` of the implementation and harness they ran
on; a change under `src/`, `sql/`, `tests/`, `tools/`, `lab/`, `zabbix/`,
`collectors/` or the project metadata/lock files makes the existing native and
real-Zabbix records stale, and `tools/update_acceptance.py` and
`tools/public_audit.py` will refuse them until those runs are repeated.

Quality changes must pass `python tools/run_quality.py`; Ruff checks runtime
code and strict mypy starts with config, crypto, policy and RPC. Expand the
checked boundary when adding new trust decisions. Never add blanket ignores
or weaken assertions to make generated cases pass. Hypothesis PR and nightly
profiles are bounded; keep shrunk failures under `build/private/`, review them
for secrets before turning a failure into a synthetic regression fixture.
See `docs/stateful-testing.md` and `docs/native-fault-testing.md`.

CI clears generated evidence and joins successful quality/reference/native jobs
from one run and commit before building. It never imports committed service
PASS records into a new build. See `docs/ci-evidence.md`.

The a9 `zabalack` SQL baseline is a separate installation namespace, not an
edit to an applied Ackline migration. See docs/RENAME.md and release amendment
R13. Never alter an old schema or its checksums to bypass the legacy guard.

Use the task-specific routes in AGENTS.md and `docs/agent-workflows.md`. Focused
changes need affected checks; candidate exports retain the release gates. Private
review attempts must survive retries. New skills are committed with the source
and tested on the representative tasks in `evals/agent-workflows/cases.json`;
record actual behaviour and limitations separately from metadata validation.
