# Docker requirements inventory (22 September 2026)

Prepared by Fable for the Docker-removal handoff from a read-only inventory of
`a3317dc` (main, a17) and `3e6fd20` (the a18 candidate, PR #29). It classifies
every Docker dependency as **REQUIRED** by the routine Actions path, **OPTIONAL**
retained support, or **HISTORICAL** record, and names what the Docker-free
routine CI change (R26, `claude/docker-free-native-ci-20260922`) alters. The
raw inventory with line numbers is retained with the handoff evidence; this
page is the reviewable summary. Nothing here uninstalls Docker or changes a
host: those are prepared operational steps, listed at the end.

## Routine Actions path

| Workflow / job | Docker at a17 and a18 | After R26 |
|---|---|---|
| `ci.yml` / `native` | **REQUIRED**: the one Docker step, `tools/native_ci.py … --retire-empty-networks --network-evidence-root …` (Compose lab, relays, network retention) | Docker-free: `tools/native_local.py --isolated-lab` on the runner's installed PostgreSQL 18 / PgBouncer under the same shared lab lock |
| `ci.yml` / `package` | blocked by `native` (`needs`) | Docker-free |
| `ci.yml` / `quality`, `reference`, `mutation` | Docker-free (Docker appears only in the job preamble comments) | unchanged |
| `security.yml` / `scan`, `codeql.yml` / `analyze` | Docker-free | unchanged |
| `deployment-e2e.yml` / `service-lab` | **OPTIONAL**: manual dispatch behind `vars.ZABALACK_DEPLOYMENT_E2E_ENABLED`; `tools/run_deployment_e2e.py` needs a local Docker daemon | unchanged; the Docker-free form is `tools/run_deployment_e2e_local.py` |
| `release/publish.yml.example` | inactive template; `pypa/gh-action-pypi-publish` runs a container on a hosted runner | outside `.github/workflows`; unchanged |

No active workflow uses `container:`, `services:`, `docker://` or a container
action; every pinned action is a `node20`/`node24` JavaScript action.

## Tools, labs and evidence

| Item | Class | Note |
|---|---|---|
| `tools/native_ci.py`, `tools/native_docker_relay.py`, `tools/native_network_retention.py`, `lab/compose.postgres.yml`, `lab/Dockerfile.pgbouncer` | OPTIONAL after R26 (REQUIRED before) | Compose form of the native phases; importable without Docker; R26 adds the shared run-start preservation to `native_ci.py` |
| `tools/native_local.py`, `lab/local_runtime.py`, `lab/build_postgres.sh`, `tools/lab_retention.py` (R26), `lab/native_service_identities.json` (R26) | DOCKER-FREE | the routine form; PgBouncer 1.24+ policy and reviewed identities live here |
| `tools/run_deployment_e2e.py`, `lab/deployment_e2e*.Dockerfile`, `lab/deployment_e2e_wheel.py` | OPTIONAL | deployment lab Compose form; the exact-wheel `supplied_wheel` receipt mode exists only in this form |
| `tools/run_deployment_e2e_local.py`, `lab/deployment_e2e_runtime.py`, `lab/deployment_e2e_zabbix.py` | DOCKER-FREE | Compose-shaped topology (hostnames, ports, private directory) that the local runner supplies on one host |
| `lab/compose.zabbix.yml` | OPTIONAL | manual real-Zabbix rig; the Docker-free instance is `lab/zabbix_native_lab.py` (R27) |
| `tools/collect_ci_evidence.py`, `tools/update_acceptance.py`, `tools/public_audit.py`, `tools/native_failure_summary.py`, `tools/verify_release.py`, `tools/build_release.py` | DOCKER-FREE | the native predicates never required Compose identities; R26 adds the reviewed-identity requirement for a `not_docker` native record |
| `tools/deployment_evidence.py` | DOCKER-FREE | validates both Docker (`image_ids`) and local (`not_docker`, `components`) deployment records |
| `tools/self_hosted_job.py` | DOCKER-FREE | performs no Docker operation; R26 adds the live-service guard |
| Network-retention records under `/var/lib/zabalack-evidence/native-networks` | HISTORICAL after R26 | private host retention of the Compose form; consumed by no validator |

## Tests

Every Docker-related test in `tests/release` and `tests/native_support` is
mocked and runs in the Docker-free `reference` job. Before R26 exactly one test
pinned the routine Docker command
(`test_both_service_entrypoints_hold_the_shared_host_lock_for_the_whole_runner`);
R26 repins it to the local form and adds a test that the native job runs no
Docker command. The offline relay, network-retention and Compose-runner tests
stay valid while those modules are retained.

## Documentation

Statements that Docker is required by the routine path (class a) are corrected
by R26: `CONTRIBUTING.md`, `docs/ci-evidence.md`, `docs/RELEASING.md`,
`docs/self-hosted-runner.md` (workflow command, network retirement marked as
the Compose form, host requirements without Docker), `lab/README.md` and the
scope sentence in `docs/local-services.md`. Statements about the runner
account's root-equivalent Docker access (workflow preambles, the runner trust
boundary, the a18 amendment R25 "with Docker inside the guest") describe the
host as provisioned and stay true until the operational change below is
applied and reviewed. Historical records (CHANGELOG, amendments R3–R21,
BETA_READINESS, ALPHA_REPORT, evidence/README, the independent reviews and
`docs/compatibility_manifest.json`) keep their wording.

## Remaining native-form items (prepared with this inventory)

- Workflow preamble comments in all eight jobs, the docstring of
  `test_alpha_workflow_refuses_untrusted_revisions_before_any_checkout` and the
  "root-equivalent Docker access" wording in `docs/self-hosted-runner.md` and
  `docs/ci-evidence.md`: reword once the runner account leaves the `docker`
  group; until then they are accurate.
- `lab/local_runtime.py` BLOCKED_ENVIRONMENT message pointed at the Compose
  lab; corrected here to name the local form.
- Two scope statements disagreed about local records: `tools/deployment_evidence.py`
  and `docs/release-provenance.md` said local-process records cannot establish
  installed-wheel coverage, `docs/local-services.md` and R14 said they establish
  the installed-wheel path on the named local services. The precise statement
  is the R14 one for the deployment lab (the local runner verifies the installed
  bytes against the wheel) while the exact-artifact `supplied_wheel` receipt
  remains Compose-only; both files now say so.
- `Makefile` names a `deployment-e2e-local` target without a rule; a rule is added.
- Per-phase pytest bounds differ (900 s local, 600 s Compose) and
  `ACK_TEST_PGBOUNCER_EXPECTED_PORT` is set only by the Compose runner (the
  local runner's fixed pool port equals the test default). Recorded; unchanged.

## Operational steps prepared, not applied

Separate from the repository changes and not authorized by them:

1. Remove `zabalack-runner` from the `docker` group (or remove Docker from the
   guest) once routine CI runs the Docker-free form and the deployment lab's
   Docker form is no longer needed there; verify with
   `tools/runner_account_check.py`.
2. Reword the "root-equivalent Docker access" statements above after that
   change is reviewed.
3. Retire the orphaned Compose project recorded on the former ARM64 runner
   (`docs/beta_readiness_snapshot_2026-09-21.md`) on that host only, under its
   own retention rules.
