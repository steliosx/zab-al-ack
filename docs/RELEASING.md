# Preparing and publishing the alpha

The candidate under qualification is 0.1.0b2. Creating its source archive/wheel does not
publish it, assign a public namespace or authorize production changes. No
publication workflow is active: `release/publish.yml.example` is a template.

## Local release preparation

Use a clean Python 3.12 environment with requirements.lock and the pinned npm
lab dependencies, then install `requirements-quality.lock` with `--require-hashes
--only-binary=:all:`. Run:

```bash
python tools/quickstart.py --isolated-lab
python tools/run_quality.py
python tools/run_mutation_checks.py --profile pr
python tools/run_sql_checks.py --isolated-lab
python tools/run_reference_suite.py --wasm
python tools/run_property_nightly.py --isolated-lab
# Service suites: Compose forms (tools/native_ci.py, tools/run_deployment_e2e.py)
# or, without Docker, the local forms described in docs/local-services.md
# (tools/native_local.py, tools/run_deployment_e2e_local.py) and
# tools/record_real_zabbix.py --run for the real-Zabbix contract.
python tools/update_acceptance.py
python tools/dependency_inventory.py
python tools/public_audit.py --write-report
python tools/build_release.py
```

Every executed-run record (`evidence/reference-run.json`, `evidence/native-run.json`,
`evidence/real-zabbix-run.json`) carries `code_digests`: SHA-256 digests of the
implementation (`src/`, `sql/`) and of the harness (`tests/`, `tools/`, `lab/`,
`zabbix/`, `collectors/`, `config/`, `deploy/`, `release/`, `.github/`,
`pyproject.toml`, build/quality configuration and the lock files) as they were when
the run started (`tools/evidence_utils.py`). `tools/update_acceptance.py`
refuses to rebuild the acceptance mapping, and `tools/public_audit.py` /
`tools/build_release.py` refuse to export, while any record disagrees with the
tree they are invoked on. Editing a test, runner, lab file or migration
therefore requires the affected runs to be repeated before packaging;
documentation and evidence edits do not. Record a real-Zabbix run with
`python tools/record_real_zabbix.py --run` with the protected
fixture described in `lab/real_zabbix.md` (or `--not-run REASON`). The recorder
owns pytest execution, redacts both report formats and checks tree stability;
importing an arbitrary historical JUnit is refused. Run native checks on a
supported lab host before mapping; an unavailable native runner writes
`NOT_RUN` and returns 2. Local alpha builds may retain this explicit limitation;
the Actions packaging job requires native success. Active workflows select the
GitHub-hosted `ubuntu-24.04` runner (owner decision of 10 October 2026, while
the dedicated ARM64 runner is held); see `docs/ci-evidence.md`. Review that execution-policy change with Opus and retain
actual Actions allocation, same-run producer/package artifacts and exact tested
SHA before marking CI validated. Local wrapper runs remain local evidence.

The builder performs two wheel/sdist builds, normalises sdist archive metadata,
compares their SHA-256 hashes, and creates a source ZIP from an explicit public
file selection. Raw logs, private packets, Git history, virtual environments,
dependency trees and old artifacts are excluded. `dist/BUILD_PROVENANCE.json`
records what was compared; `dist/SHA256SUMS` covers each release file. This local
provenance is not a signed attestation. The wheel contains Python code/licences;
the source distributions include SQL, deployment tooling, examples and tests.

Run native integration CI with `python tools/native_ci.py --isolated-lab` on a
local host with Docker Compose. Each phase creates a uniquely named fresh
cluster and stops its containers while preserving synthetic volumes. The runner
rejects remote Docker contexts. Local trust authentication is confined to the
isolated lab; actual SCRAM/TLS/PgBouncer identities remain deployment evidence.
Native suite success cannot be inferred from a supplied CI workflow.

## Security scans

Use pip-audit 2.9.0 and Gitleaks 8.24.3, then run
`python tools/security_scan.py --public-advisory-lookups`. Only public package
names/versions go to advisory services. Source/history scans stay local and
raw findings are excluded from release artifacts. Exit 2 means a required
scanner did not run, and is never a clean scan. The source export check is a
separate targeted check. Current availability and results are in
`evidence/security-scans.json`. The active security workflow uses the hosted
`ubuntu-24.04` runner and verifies the pinned Linux x64 Gitleaks release archive;
workflow configuration alone is not a scan result. On hosted runners its
three-day Actions artifacts are the only retained copies (there is no durable
runner host while the ARM64 runner is held). Run
`npm audit --prefix lab --legacy-peer-deps --json` for Node lab dependencies.
Review all findings; do not add broad path exclusions for test fixtures.

## Before the first public upload

Complete the single publication register, `config/publication.pending.json`:

1. Assign the GitHub organisation/repository, package owner and PyPI namespace.
   Add matching `[project.urls]` Repository/Issues/Documentation links in
   pyproject.toml. No unowned/fictitious URL is shipped in package metadata.
2. Assign primary and backup maintainers and a private conduct contact. Enable
   GitHub private vulnerability reporting and verify recipients receive it.
3. Review project copyright/third-party obligations and public content. Inspect
   the release tree and the new repository history; do not publish the private
   development archive or wheelhouse. Review SBOM licence ambiguities separately.
4. Record explicit publication authorization. This is distinct from G1–G8 and
   does not authorize any operational acknowledgement or production change.
5. Run `python tools/public_audit.py --publication`. It returns 2 until required
   publication inputs are recorded, and 1 for a public-export check failure.

Create a reviewed tag `v0.1.0b2` only after those edits and final reruns. Review
the exact GitHub Actions run for that commit and clearly disclose unavailable
native/real-source checks in the prerelease notes. Source-only alpha evaluation
is possible while such gaps remain, but stable production support is not claimed.
The stable-release requirements are tracked in `docs/ROADMAP.md`; the beta boundary and its open items are tracked in `docs/BETA_READINESS.md`.

For PyPI, register a Trusted Publisher bound to the chosen repository, exact
workflow filename and `pypi` environment. Restrict who can release and which tags
can use that environment. Move the reviewed publishing template into workflows
only after authorization. It builds separately from the OIDC publishing job,
passes only wheel/sdist artifacts to PyPI and requests publishing attestations.
The template has not been run against GitHub/PyPI here. Package availability,
ownership, attestation issuance and a successful upload are not claimed.

Primary interface references inspected for these files:
[PyPI publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/),
[Python project metadata](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/),
[GitHub PostgreSQL services](https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers).
Action commits and fetched action.yml provenance are in ci_reference_manifest.json.

## Withdrawing a release

Stop recommending the affected alpha and publish an advisory with the affected
version and mitigation. If necessary, a package owner can deliberately yank the
PyPI release; do not erase its source/tag or audit evidence. Follow
`docs/rollback.md` for deployment holds/rollback: preserve capture/ledger data,
fence workers, and never automatically unacknowledge source events. Remote
release changes and operational rollback each require their own authorization.

Both inactive publication and attestation templates now call the same required
quality/reference/native/mutation CI workflow. The publication template verifies
the current-run candidate, adopts only those verified reports, and checks the
publication-owner register before handing wheel/sdist bytes to the separate
publishing job. Neither template has been activated or executed here.

## Repository guidance in the release

Use `python tools/run_review.py --plan` for selected commands and retained attempts
(see `docs/agent-workflows.md`). Run profiles separately; both profile reports
are retained and validated when present. The source ZIP and sdist include the
three `.agents/skills` entrypoints and evaluation cases. The wheel remains the
runtime plugin. `BUILD_PROVENANCE.json` separately records all guidance hashes;
`python tools/verify_release.py` compares them against the checkout and archives.
This verifies bytes, not agent quality, signed attestations or deployed gates.
