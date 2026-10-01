# Public-alpha release amendments

These entries record release changes and focused implementation corrections. The application and SQL design
revision 1.2, final-review interpretations and amendment A1 remain the baseline.

## R1 — Build/test dependency advisories

An executed pip-audit 2.9.0 lookup of all 50 pinned reference Python dependencies
reported three advisories on 2026-09-09. The pre-fix result is retained separately
from the final scan. Fixed wheels were downloaded from PyPI and verified against
their PyPI SHA-256 digests; metadata and source URLs are recorded in
`tooling_reference_manifest.json`.

| Package | Previous pin | Alpha pin | Reason |
|---|---|---|---|
| pytest | 8.4.2 | 9.0.3 | Fix vulnerable temporary-directory handling, PYSEC-2026-1845 / CVE-2025-71176 |
| pytest-asyncio | 1.2.0 | 1.3.0 | 1.2.0 requires pytest <9; inspected 1.3.0 metadata supports pytest <10 |
| setuptools | 80.9.0 | 83.0.0 | Fix Unicode-normalisation exclusion bypass in source packaging, PYSEC-2026-3447 / CVE-2026-59890 |
| wheel | 0.45.1 | 0.46.2 | Fix path-traversal permission changes during unpack, GHSA-8rrh-rw8j-w5fx / CVE-2026-24049 |

Primary references: [pytest fix](https://github.com/pytest-dev/pytest/releases/tag/9.0.3),
[setuptools advisory](https://github.com/pypa/setuptools/security/advisories/GHSA-h35f-9h28-mq5c),
[wheel advisory](https://github.com/pypa/wheel/security/advisories/GHSA-8rrh-rw8j-w5fx).
Only toolchain pins changed; Alerta's inspected package contains no pkg_resources
import dependency that would require retaining an older setuptools. Compatibility
is established by the rerun results, not inferred from version numbers.

## R2 — Public source and compatibility labels

The public repository starts from a reviewed export without private Git history,
the private implementation packet, historical raw logs or the old wheelhouse.
`DESIGN.md` preserves the baseline with generic site and secret-manager labels.
The first candidate, 0.1.0a1, advertised Python support limited to the tested 3.12
series. No acknowledgement route, signature format, migration or runtime grant
was changed to prepare that candidate. Application/SQL byte comparisons are in
`evidence/implementation-integrity.json`.

## R3 — 0.1.0a2 corrections from the independent native run

0.1.0a1 had a local `candidate/v0.1.0a1` tag and disposable test installations.
It had no public release tag or publication, and no tracked operational
installation has been reported. An independent
review executed the native PostgreSQL 18 / PgBouncer phases for the first time
(on a source-built 18.3 cluster) and found the items below. They are corrected
in place rather than by forward migration, following the usual pre-release rule
for schema migrations: a migration is immutable from the moment it has been
applied to a tracked installation or shipped in a tagged public release. The
in-place correction assumes neither condition applies; confirm that before
deploying this candidate. `sql/migrate.py` still refuses to rerun a changed file on any
installation that already recorded it, so an evaluator who applied 0.1.0a1 SQL
to a lab database must recreate that disposable database.

| Change | Reason |
|---|---|
| `tests/conftest.py` yields the observed `database_name`; `tests/postgres/test_worker_path.py` and `tests/performance/test_reference_batches.py` use it instead of the literal `postgres` | `tools/native_ci.py` and `lab/compose.postgres.yml` create database `alerta`; with the literal name `Worker.check_health()` compared `alerta` with `postgres` and the `worker` and `performance` phases failed 4/4 and 1/1. The hosted `native` job could not have passed. |
| `sql/migrations/004_controls.sql`: `msp_worker` no longer receives direct `EXECUTE` on `msp.capture_health()` | Design baseline §12 grants the worker claim/completion/projection functions only; the worker reads health through `msp.worker_call('doctor')`, which calls `capture_health()` inside the `SECURITY DEFINER` boundary, so the direct grant was unused and wider than the design. The probe (`alerta_capture_probe`) and `msp_operator` keep their grants. |
| `sql/migrations/002_capture.sql`: `marker` is stored as `NULL` when the reserved prefix is present but the marker does not parse | A well-formed marker is exactly the bounded proof string. A malformed one is up to 256 characters of whatever followed `[msp-ack:`, which can be the operator's free-text note that §5.3/§12 exclude from the capture record. The diagnostic reason (`marker_malformed`) and all other allowlisted columns are unchanged; T14 assertions count diagnostics, not marker bytes. |
| `tools/evidence_utils.py` renders JUnit `timestamp` attributes in UTC; `tools/run_reference_suite.py` and `tools/native_ci.py` set `TZ=UTC`; batch evidence records `platform.system()-platform.machine()` instead of `platform.platform()` | Committed 0.1.0a1 evidence carried a `+05:00` offset and the lab host's kernel/glibc string (also in `docs/compatibility_manifest.json` `build_environment`, now reduced to interpreter version and OS/CPU). Neither is secret, but both fingerprint the build environment in a public repository. |
| `lab/local_runtime.py` places the Unix socket in a short per-checkout temp directory | `unix_socket_directories` pointed at the checkout's `.lab` directory; paths over ~107 bytes made `postgres` refuse to start. |
| `lab/build_postgres.sh` accepts PostgreSQL 18.3 in addition to the 18.0 pin, each with its published SHA-256 | 18.0 is no longer mirrored by common package archives; 18.3 is the engine inside the pinned PGlite and was used for the native evidence in this release. |
| `.github/workflows/security.yml` installs pip-audit from `requirements-scanner.lock` with `--require-hashes` and builds Gitleaks from the commit recorded in `docs/gitleaks_reference_manifest.json`, then asserts `gitleaks version` | The scanner and its 26 transitive dependencies were previously version-pinned only; Gitleaks was tag-pinned. |
| `.github/workflows/ci.yml` and the publishing template run `python -m pip check` after installation | Dependabot tracks `pyproject.toml` pins but not the `*.lock` files; `pip check` surfaces pyproject/lock drift. |
| `README.md` and `lab/README.md` add `--ignore-scripts` to `npm ci` | Matches the CI invocation; the two lab packages do not need install scripts. |
| `docs/DESIGN.md` §12 lists `msp_operator`; `docs/deployment.md` records the installation-principal/runtime-role question under G1; `docs/runbook.md` documents the fail-closed capture exceptions; `CONTRIBUTING.md` scopes the migration-edit rule and documents lock/evidence refresh | Documentation gaps found by comparing the SQL with baseline §5.3, §5.7, §8 and §12. |

Naming (`msp` schema, `msp_*` roles, `[msp-ack:v1` marker), the ten publication
inputs, licence headers and moving committed evidence to release assets are
deliberately unchanged: they are owner decisions, not defects.

## R4 — Review corrections on the a2 candidate (unreleased)

These changes are supplied as a review patch, not a retagged or republished a2.
Application modules and all migration bytes remain identical to the supplied a2.
The review report records fresh test results separately from a2's historical
committed evidence. The supplied 20 native passes were not rerun in this review.

- Set Gitleaks's version linker symbol during the commit-pinned build, preserving
  the exact-version assertion. The pinned `cmd/version.go` defaults to
  `version is set by build process`; upstream `.goreleaser.yml` supplies
  `-X=github.com/zricethezav/gitleaks/v8/cmd.Version={{.Version}}`.
  See `docs/gitleaks_reference_manifest.json` for the inspected source links.
  The hosted build remains unexecuted here.
- Aggregate repeated results by exact test identity and evidence layer: an
  executed native pass resolves that same test's WASM-runner skip, while failures
  and skips of other tests remain visible. Reject missing/failed selected native
  phase reports. Remove each runner's previous generated JUnit before launching
  its child, so a crash cannot recycle a previous pass; a zero exit with no
  reference report also fails.
- Exclude root handoff manifests, checksum lists and `START_HERE.md` from source
  exports. The supplied nested source ZIP had all 198 correct source files plus
  three handoff files; its manifest named an older candidate commit and its
  checksums predated the final outer packet. Outer packet checksums were valid.
- Disable unused local-lab Unix sockets. All lab clients already use loopback
  TCP. This avoids long checkout or `TMPDIR` paths and removes the predictable
  shared temporary-directory `mkdir`/`chown` path. PostgreSQL 18's inspected
  `doc/src/sgml/config.sgml`, `unix_socket_directories`, explicitly permits an
  empty value. The startup override also covers an already initialised, stopped
  lab cluster without deleting or rewriting its data.
- Extend T14's real SQL assertions to reject retained malformed marker bytes or
  free text; extend T38 to require denied direct worker health access and a
  working restricted `worker_call('doctor')` path. These are executed in the
  supplemental PostgreSQL engine in this review; native rerun is still required.
- Correct the a1 migration note's factual scope: a local candidate tag and
  disposable installations existed. If a tracked a1 installation is discovered,
  retain its migration checksums and provide a forward migration; never edit its
  migration ledger or recreate an operational database to accept a2.

## R5 — Ackline identity (0.1.0a3)

The project, package, module, CLI, database schema, roles, capture trigger,
signed-marker prefix, metrics, error codes, environment variables, filesystem
paths, systemd units, Ansible variables and archive names now all use the
Ackline name (mapping in `CHANGELOG.md` 0.1.0a3). Reasoning:

- The schema name, role names and marker prefix are persisted and signed
  formats. Renaming them after the first public tag or the first tracked
  installation would be a breaking, migration-bearing change; renaming before
  either exists costs a search-and-replace and a full re-run of the suites.
  The earlier reviews flagged this as the last cheap moment.
- The a1 name `msp` conflated a business context (managed service providers)
  with a product identifier. The design baseline rev 1.2 text that used "MSP"
  for the integration's own objects now says "Ackline"; the one sentence about
  administrative operators acting across customers now says so directly.
- Marker version stays `v1`. The grammar and length are unchanged
  (`[ackline:` and `[msp-ack:` are both 9 bytes), only the reserved prefix
  differs, and no marker signed under the old prefix exists outside disposable
  lab databases. A tracked installation carrying old markers would need a
  reviewed dual-prefix verifier and a forward migration; none exists.
- Alerta-facing attribute names (`zabbix_ack_sync*`) are Alerta data-model
  keys that operators already see in the UI and filters; they describe the
  attribute, not the vendor, and are unchanged. The lab-only `ACK_TEST_*` /
  `ACK_LAB_*` variables and `ack_test.*` SQL settings are unchanged for the
  same reason.
- `alerta_owner` and `alerta_app` are the upstream Alerta identities the lab
  reproduces; they belong to Alerta, not to this project.

Validation: the unit/mocked, WASM, SQL-assertion, webhook, quickstart and
native PostgreSQL 18.3 / PgBouncer phases were re-executed on the renamed code
(`ALPHA_REPORT.md`). `evidence/implementation-integrity.json` records that,
apart from the identifier substitution, application and SQL logic are
unchanged from the reviewed a2 commit `73d046b218afe2b883cc11e856ddbf668a822231`.

## R6 — Real-Zabbix lab tolerance (0.1.0a3)

`tests/real_zabbix/test_contract.py` accepted only an exact `apiinfo.version`
of `7.0.0`. The pinned 7.0.0 images/source are not obtainable from the archives
available to the review lab, while 7.0.10 is; the client (`ackline.rpc.Endpoint`)
already accepts `7.0.[0-9]+`. The fixture now accepts any 7.0.x patch release and
records the observed version as a JUnit property. The reference pin (7.0.0,
`docs/reference_manifest.json`) and the production endpoint registry's approved
version list are unchanged. The Zabbix comment written by `event.acknowledge`
now starts with `[ackline]` (R5); the webhook echo detector uses the same prefix.

## R7 — Independent a3 review corrections (adopted in 0.1.0a4)

The original a3 candidate remains unchanged at commit
`afdfb858909f113a0138b2fe23bb1b3f9d471eb6`. A clean build independently reproduced
its wheel, normalized sdist and source ZIP byte for byte. Its available reference
suite passed, but targeted review found gaps beyond those existing assertions.

### Expired responses and unknown delivery outcomes

In supplied `003_worker.sql`, completion after lease expiry records a
`late_response` outcome while leaving the action non-uncertain. Abandonment
checks treat any outcome row as settlement. The next claim can therefore
authorize another attempt before read-only reconciliation has settled the
previous send. This violates the selected T29/T31 unknown-outcome policy.
Additionally, replacement-worker shutdown can discard uncertainty inherited
from an earlier worker's attempt.

Forward migration `006_late_response_fencing.sql` preserves unsettled attempts
and action uncertainty across those transitions. It retains late callbacks as
evidence, respects replacement generations, preserves completed/explicitly
settled outcomes and requires a separate approved retry after a no-effect
settlement. It holds existing affected unresolved work for reconciliation.
The helper has no PUBLIC or runtime-caller grant. No signing secret, endpoint
access, trigger ownership or caller privilege is added.

Migrations 001–005 remain byte-identical to supplied a3. Database schema version
2 is required by the corrected probe and worker. The changed function manifest
fails health until the separately authorised installation principal explicitly
reviews and reseals it. See `deployment.md` for fencing, upgrade and rollback.
No migration or reseal has been applied to production by this review.

The new T29/T31 SQL group exercises expired success/unknown/definite-error
callbacks, worker replacement/shutdown, duplicate callbacks after success,
in-flight retry/pause and explicit settlement followed by deliberate retry.
The forward-upgrade test exercises existing affected work, blocked health and
explicit resealing. Both Python wrappers are selected by the reference and
native runners. Available execution is PostgreSQL 18.3 WASM with synthetic
lease expiry; it does not establish native concurrency, HA or remote effects.

### Current-run evidence must fail closed

The native runner previously cleared JUnit only after successful Compose
startup. A failed startup followed by a cleanup timeout could preserve both
the old PASS run record and its passing XML. It now invalidates its previous
reports and records RUNNING before service operations, and records startup,
malformed-report and cleanup failures. Acceptance aggregation now rejects
failed/interrupted native runs, failed phases/cleanup, empty or non-passing
JUnit and inconsistent recorded counts. Unit/mocked regressions exercise these
failures; they are not native service evidence.

### SQL identity manifest after the rename

All 11 digests in supplied a3's `docs/sql_manifest.json` still matched the
reviewed a2 files, rather than the renamed a3 files. Outer archive checksums
were valid. The internal SQL manifest is refreshed and includes migration 006.
Export auditing now requires exact coverage, canonical paths and matching
SHA-256 values for SQL scripts and migration runners. Missing, malformed,
duplicate, unsafe or stale entries stop `build_release.py` before a build.

Fresh review results are recorded under `source/evidence/` and in the review
handoff. Historical supplied records remain in the immutable original a3 ZIP
and base Git commit. Old native/real-Zabbix reports are removed from the current
acceptance inputs, so they cannot validate the changed migration. G1–G8,
native validation of these corrections, hosted CI/Gitleaks, production-like
media/notification behavior and publication inputs remain open.

PostgreSQL 18 documents that replacing a function retains its ownership and
permissions; its security-definer guidance also requires a safe search path and
restricted execute privileges. Those interfaces were checked for migration006,
and helper denial is exercised under the lab roles.
[PostgreSQL 18 CREATE FUNCTION](https://www.postgresql.org/docs/18/sql-createfunction.html).

## R8 — 0.1.0a4 integration of the a3 review

The a3 review's corrections (R7) are adopted unchanged, including the forward
migration rather than an in-place edit: no a3 installation exists, so R3's
edit-in-place rule would have allowed folding the fix into `003_worker.sql`,
but the review's 006 comes with data repair, a schema-version gate, an explicit
reseal requirement and two tested upgrade scenarios — exactly the upgrade path a
beta must be able to walk — and it keeps every shipped migration byte-identical.
Validation that the review could not perform was executed here: the seven-scenario
late-response group and both 005→006 upgrade scenarios on a native PostgreSQL 18.3
cluster with PgBouncer (22 native passes), and the real-Zabbix contract plus the
new end-to-end case on a disposable Zabbix 7.0.10 (5 passes). The review's
observation that the outer checksum list omitted nested checksum files is fixed.

Not adopted: nothing. Deferred to the owner, per the review's priority list:
source/test digest binding for evidence runs, retained-ledger query plans,
licence-label review (`python-dateutil` "Dual License"), and media/notification
behaviour on a production-like Zabbix. The review's suggestion to strengthen the
outside-scope fixture with positive existence evidence is partly addressed by
the end-to-end case, which proves the positive path with the same lab token; the
negative case still relies on the lab's own role/permission setup.

## R9 — 0.1.0a5 integration of the a4 review

The independent a4 review (`ACKLINE_A4_REVIEW.md`, patch `ff1f2854…`, kept
with its evidence under `reviews/0.1.0a4/` in the handoff) rebuilt the a4
wheel, sdist and source ZIP byte for byte, re-executed the reference suite
(250/13/0) and found two tooling defects, neither in production code: the
real-Zabbix fixtures could contact an undeclared URL before validating the lab
boundary, and the acceptance updater could promote contradictory or
cleanup-less service evidence. Both were reproduced here on unmodified a4 — the
review's HTTP-spy reproducer shows a client constructed and a version probe
sent for a synthetic remote URL, and 16 of the review's new validator
regressions fail against the a4 updater — and both are fixed by the seven-file
patch, which is adopted unchanged. The patch changes no runtime module, no SQL,
no dependency and no default.

Two items the review (and the a3 review before it) left open are closed here
because this lab can close them, and the reasoning is recorded so the next
review can judge the design rather than rediscover it:

- **Digest binding.** A run record is only evidence for the tree it ran on.
  Each record now carries the SHA-256 of the implementation (`src/`, `sql/`) and
  of the harness (tests, runners, labs, Node artifacts, project metadata and
  lock files), computed before the run starts so that an edit during a run
  cannot be what the record is bound to. The acceptance mapper and the export
  audit refuse a record that disagrees with the current tree. Documentation,
  evidence and built artifacts are deliberately outside both scopes: a README
  edit or a regenerated report must not invalidate a native run, while any
  change to what was executed must. Two digests rather than one so that a
  harness-only change is distinguishable from an implementation change when
  deciding which runs to repeat; a Merkle-style per-file manifest was
  considered and rejected as unnecessary for a refusal check. The binding is
  a fail-closed consistency check, not a signed attestation; signing remains
  a hosted-CI concern.
- **Positive proof for the negative visibility case.** `test_T11a` previously
  accepted an empty result or a generic permission error from the restricted
  token, which a nonexistent event or a wrong group filter would also produce.
  A separate read-only witness token that can see both host groups now
  proves, through the production client, that the outside event exists, is in
  the outside group and is not in the cohort group; only then does the test
  require the restricted token's read of the same event to be a valid empty
  result. The witness is created by the lab operator with `event.get`/`host.get`
  only and is never used for a write. Without a witness the case is NOT_RUN,
  not PASS.

Found while re-executing the SQL groups for this candidate: the boundary
group (`assertions_boundaries.sql`) passed or failed on PGlite depending on
whether three consecutive updates with identical marker text fell into the
same millisecond — the trigger's WHEN predicate (design baseline §5.3)
deliberately ignores an unchanged history head, and PGlite's
`clock_timestamp()` has millisecond resolution. The test now carries distinct
operator text before each marker, which is what a real acknowledgement does;
no product change. Every earlier candidate's WASM run of that group was a
genuine pass of the same assertions; the flake only lost captures, never
manufactured one.

Also adopted from the review notes: the canonical `selectAcknowledges`
parameter in the end-to-end read-back; `evidence/README.md` exempts explicitly
historical records from the "produced on this tree" statement. Found while
integrating: the patch's `from lab.real_zabbix_safety import …` resolved only
under `python -m pytest`; `pythonpath = ["."]` in the pytest configuration
restores collection under bare `pytest` from any working directory, with a
regression test.

Validation that the review could not perform was executed on the final tree:
native PostgreSQL 18.3 + PgBouncer 1.24.1 phases (22 passes) and the real
Zabbix 7.0.10 suites with the patched fixtures and the witness token (5 passes),
both recorded with digests matching this tree. Deferred to the owner, unchanged:
hosted CI and Gitleaks, media/notification behaviour on a production-like
Zabbix, retained-ledger query plans, licence-label review, external security
review, publication inputs and G1–G8.

## R10 — 0.1.0a6 quality gates and execution-owned evidence

The reviewed a5 packet showed four evidence-tooling defects: generated
setuptools metadata altered source digests; export/build did not require a
bound acceptance result; a recorder could relabel arbitrary old JUnit; and a
failed recording attempt could leave prior success. The reviewed seven-file
patch is adopted. Its recorder now owns pytest, snapshots the tree before/after,
invalidates prior output first, and redacts/leak-checks private staged JSON/XML.
The retirement of `--junit` is deliberate: old service results cannot prove a
new harness ran. No source payload or persisted schema change is introduced.

CI previously ran native and WASM independently but built in the WASM job,
where committed native/acceptance reports could be stale after a code edit.
The new collector clears generated evidence before each job, validates exact
report/phase sets and file hashes, and joins only one Actions repository,
commit, run and attempt. Packaging waits for successful quality, reference and
native jobs; real Zabbix is explicitly NOT_RUN in that workflow. Hash binding
is a consistency guarantee, not a signature or protection from a malicious
workflow author. Hosted execution remains unexecuted in this workspace.

Ruff bug/error rules cover runtime Python. Strict mypy begins with configuration,
cryptographic intent, policy and RPC boundaries, using types established only
after existing validation. Small unused bindings/imports and exception causes
are cleaned up; immutable timeout defaults are named constants. Shipped migration bytes,
role grants, signing encoding, verification order and release-one routes stay
unchanged; forward migration 007 below corrects a generated-test finding. Quality wheels are hash-pinned for CPython 3.12 Linux x86_64.

Generated tests exercise pure trust boundaries and actual restricted ledger
functions with independent identity/deadline/attempt/generation assertions.
Their PostgreSQL clock advancement is synthetic and sequential. Native fault
tests separately use processes, real elapsed leases, real Worker/client code,
a loopback HTTPS stub and actual process death. No native pass is inferred from
helper tests, test collection or PostgreSQL WASM. The daily scheduled profile
is implemented but has not executed in hosted Actions.

The broader generated run exposed a product defect, confirmed by a separate
short SQL reproduction: uncertain attempt → explicit fenced no-effect settlement
before expiry → original deadline passes → later human action. The settled
primary remained `failed` with an open effect because `expire_due_effects`
excluded that state. Retry was then forbidden by expiry and the new action
coalesced indefinitely. This violates §6.5's later explicit-action path.

Forward migration 007 replaces only the expiry helper. It additionally admits
`failed` primary actions whose reason is `operator_no_effect_confirmed`; every
existing deadline, uncertainty, live-lease and unresolved-attempt guard remains.
The completion boundary stays the original execution deadline. Settlement still
requires evidence and does not enqueue a retry; no source write is introduced.
The unchanged 006 behavior is reproduced inside an isolated rolled-back upgrade
test before 007 is applied. Worker/probe require schema version 3 and installation
readiness must be explicitly resealed after the approved migration.

This amendment preserves the frozen rev1.2 architecture. Migrations 001–006
remain immutable; 007 is a forward correctness amendment, not an in-place edit.
Current a6 native and real-Zabbix records are NOT_RUN. Historical a5 claims
remain historical; G1–G8 and publication inputs remain open.

## R11 — a7 mutation, service-path, security and provenance controls

This candidate continues the quality programme with four executable additions.
The curated Python mutation catalogue targets authority, binding, identity and
source-write guards. It does not claim exhaustive mutation coverage or native
SQL mutation evidence. A surviving wrong-event/trigger mutant exposed missing
negative tests; independent valid-shaped mismatched responses now have to block
all writes. No product invariant was weakened and no runtime fix was needed.

The normal-process deployment lab uses the installed Alerta app factory, normal
HTTP authentication and installed worker CLI, with password-authenticated native
DB roles and synthetic Zabbix resources. Alerta 9.1.0 performs schema setup at
normal startup, so its application login uses the documented alerts-owner trust
boundary. This is disclosed rather than hidden by a test storage adapter.

Source inspection exposed the distinct webhook transport incompatibility A2 in
SPEC_AMENDMENTS.md. Its affected production path remains gated. No HTTPS-only
fixture is treated as evidence of certificate or redirect enforcement.

CodeQL is commit-pinned and opt-in pending repository policy; only its analysis
job can upload security results. The inactive attestation template reuses the
required current-run build jobs, validates exact source/package/evidence bytes,
and stages only verified subjects. Its consumer owns a pinned cryptographic
verifier invocation and checks exact workflow/tag/commit policy. Offline verifier
regressions are separate from signed provenance, which has not been issued here.

The mutation gate is now mandatory for local export and CI packaging. Harness
bindings include all GitHub, deployment, configuration and release-policy inputs.
The README's unqualified historical native/Zabbix claims are corrected. Migrations
001–007 remain byte-identical and schema 3 remains required. There is no product
SQL change, production access, remote push, publication or website deployment.

## R12 — 0.1.0a8: first service execution of the a6/a7 upgrades

The a7 packet was verified first: its outer and nested checksum lists, the
bundle (`67f84543…`, three commits on the a5 candidate), the wheel, sdist and
source ZIP (byte-identical to a fresh build), the release verifier, the
reference suite (719/22/0), quality checks, 8 SQL groups, 12 webhook groups,
quickstart, and both mutation profiles (12/12 and 20/20 killed on a 107-test
baseline) all reproduced in a fresh pinned environment. Native, real-Zabbix
and deployment-path suites had never run. This lab ran them on source-built
services without Docker: PostgreSQL 18.3 and PgBouncer 1.24.1 on loopback
with the same phases, targets and environment as `tools/native_ci.py`; a
disposable Zabbix 7.0.10 (server, PHP frontend, PostgreSQL 16) for the API
contract; and, for the deployment-path lab, a second fresh Zabbix instance,
a fresh PostgreSQL 18.3 cluster with `scram-sha-256` host authentication on
port 5432, and the hostnames `postgres`, `zabbix-web` and `runner` resolved
to loopback, so that `lab/deployment_e2e_runtime.py` executed unmodified
through `pytest tests/deployment_e2e`. Records were written by the a7
recorders where they exist (`record_real_zabbix.py --run`), or by a local
equivalent that reports the same fields plus a `runner` description and
`not_docker: true` in place of image identities.

Four defects surfaced, none visible to the WASM, mocked or offline layers:

1. `zabbix/v1/webhook.js` treated `*UNKNOWN*` as an invalid cause identifier.
   Zabbix 7.0.x renders an unresolvable `{EVENT.CAUSE.ID}` that way for every
   event without a cause (`expression.c`, `STR_UNKNOWN_VARIABLE`), so every
   ordinary problem notification failed with `Invalid numeric identifier:
   cause` before any HTTP request. The 0.1.0a1 reading of the resolver — that
   the macro is left unexpanded — was wrong, and the synthetic Node contract
   had enforced the wrong behaviour by asserting the rejection. The script
   now treats `*UNKNOWN*`, the unexpanded macro and `0`/empty as "no cause"
   and still rejects arbitrary text; the artifact is rebuilt from the script;
   the Node contract gains the `*UNKNOWN*` fixture and two rejection cases
   (14 groups); amendment A3 records the source evidence. Deployments that
   imported the earlier artifact must re-import it. This is the only product
   change in a8.
2. `tests/postgres/test_postgres.py::test_T37` floods the outbox and claims
   32 verification leases (ten-second expiry) to prove no duplicate claims,
   then left them live; the a6 settled-expiry tests that follow it in the
   native `postgres` phase could not claim verification and failed with
   `NoneType` on the claim result. T37 now deletes its flood and asserts no
   live lease of its own remains. WASM never saw this because T37 is
   native-only.
3. `lab/deployment_e2e_zabbix.py::enable_media` required the imported media
   script to equal the shipped file byte for byte; a real import stores it
   with CRLF terminators. The comparison normalizes line endings and still
   rejects a changed, truncated or missing script (offline cases added).
4. The same lab raised its problem immediately after creating the action;
   the server evaluates actions from a configuration cache refreshed every
   `CacheUpdateFrequency` seconds (default 10), so a problem raised before
   the cache held the action never produced an escalation or webhook. The
   lab now waits a bounded 12-second grace after action creation (offline
   case added). A fifth observation is environmental, not a repository
   fault: libcurl inside `zabbix_server` honours `HTTPS_PROXY`; the lab
   server must run with a clean environment, as the Compose form does.

Design of the lab-record deviation: the Docker runner records image
identities because the images are the executed components; the local
equivalent records the built component versions instead and marks
`not_docker`. `tools/public_audit.py` accepts the record because its status,
binding and shape rules are unchanged; the Compose forms remain listed as
unexecuted in `BETA_READINESS.md` so the distinction is not lost.

The a8 bundle continues the a7 packet's lineage (a5 → a6 → a7 → a8) with one
UTC-dated release-preparation commit on branch `candidate/0.1.0a8`, so a
private repository can be created with history intact; the a6/a7 commits keep
their original tool author identities. Hosted CI, CodeQL, Gitleaks,
attestation and the Compose forms remain NOT_RUN; G1–G8 and the ten
publication inputs remain open.


## R13 — Zab-al-ack namespace baseline (0.1.0a9)

The user selected the new public name and authorised the project/source/site
rename. Persisted identifiers are not cosmetic: schemas, roles, history markers,
HMAC domains and source comments participate in capture and delivery. To avoid
silently relabelling existing evidence, a9 establishes a fresh `zabalack`
namespace and refuses databases containing the historical `ackline` schema.
Migration numbers 001–007 and schema version 3 describe the new namespace only.
The original Ackline migration bytes/checksums remain in Git and the supplied
historical archive. This is not an exception permitting changes to an applied
migration or a same-database upgrade. See docs/rename_manifest.json.

The revision 1.2 architecture, bounded delivery, least-privilege split, mandatory
pre-read and G1–G8 remain unchanged. docs/DESIGN.md is the current-name rendering;
prior rendering and every older release amendment remain available unchanged
in the historical archive. No source incompatibility is hidden by this rename.

Both proof namespaces are redacted, but only the new HMAC domain can validate.
Both exact neutral comment formats are filtered as echoes before destination
access; this is a read/filter compatibility rule, not a delivery alias. Existing
`zabbix_ack_sync*` attributes and the source fact contract remain unchanged.

Tests cover old and relabelled proofs before target lookup, old config staying
inert, proof/attribute privacy and SQL preflights on a real PostgreSQL WASM engine.
Fresh native login/concurrency, PgBouncer, real-Zabbix and installed deployment
validation remain unexecuted where the runtime is unavailable. Historical lab
success cannot validate a renamed deployment. See ALPHA_REPORT.md and RENAME.md.

## R14 — 0.1.0a10: service execution of the renamed namespace and supplied local runners

The a9 packet was verified first: outer and nested checksum lists, the bundle
(`92bac446…` on the reviewed a8 parent `aaadf491…`), wheel, sdist and source
ZIP (byte-identical to a fresh build from the restored clone), the release
verifier and export audit, the reference suite (796/22/0 with equal digests),
quality, 8 SQL groups, 15 webhook groups, quickstart and both mutation profiles
(12/12 and 20/20) all reproduced in a fresh pinned environment. The a8-reviewed
tree, mechanically renamed, differs from a9 only in the legacy-schema guards,
legacy-proof rejection and redaction, echo recognition of both comment formats,
the build epoch and version metadata; no other behavioural change was found.

a9 recorded native PostgreSQL/PgBouncer, real Zabbix and the deployment-path
lab as NOT_RUN because Docker Compose was unavailable, and the independent a8
review's F2 noted that the R12 local runs used an editable installation and
that their bootstrap and recorder were not supplied. a10 closes both gaps:

- `tools/native_local.py` is the non-Docker form of `tools/native_ci.py`: the
  same phase table (imported, not copied), the same targets and `ACK_TEST_*`
  environment, a fresh disposable cluster per phase through
  `lab/local_runtime.py`, PgBouncer from the repository's own `lab/pgbouncer.ini`
  and `lab/pgbouncer_users.txt`, JUnit redaction through `tools/evidence_utils`.
  Its record carries `not_docker: true` and the service versions it observed.
- `tools/run_deployment_e2e_local.py` is the non-Docker form of
  `tools/run_deployment_e2e.py`: it executes the unmodified
  `lab/deployment_e2e_runtime.py` through `pytest tests/deployment_e2e` against
  operator-provided local services with the Compose hostnames resolved to
  loopback, generates the bootstrap, passwords and lab certificate itself, and
  requires a runner environment in which the built wheel is installed
  **non-editably**: the installed package bytes are compared with the wheel, the
  entry points are read from the installed distribution, and the record names
  the wheel's SHA-256. Its record is validated by `tools/deployment_evidence.py`
  in the local-process scope defined by the a8 review (`not_docker: true`,
  `image_ids: null`, component identities, runner description).
- Both runners invalidate the previous record before any service is touched,
  bind source and harness digests before and after execution, and record
  NOT_RUN with a reason when their inputs are absent. Offline regressions cover
  the phase-table identity, unavailability handling, the editable/checkout
  import refusal, wheel-byte and entry-point verification and the service
  identity shape. `docs/local-services.md` describes the operator inputs.

With these runners the renamed namespace was executed for the first time on
source-built local services: 41 native passes across the five phases (the a9
legacy-schema guard cases included), 5 real-Zabbix passes through
`tools/record_real_zabbix.py --run` with `[zabalack]` comments observed on the
disposable instance, and the deployment-path lab's 4 cases with all 13
observations true from the installed a10 wheel. No product defect was found;
the corrections in a10 are the two runners, their tests and documentation.
The Docker Compose forms remain unexecuted, as do every hosted workflow, the
signed attestation and G1–G8. Local-process records do not establish the
Compose image path; they establish the installed-wheel service path on the
named local services.

## R15 — a11 illustrated documentation, with an explicit capture limitation

Requested screenshots could not be captured because the cloud browser rejected
local-file navigation. The approved development work continued with static SVG
output illustrations generated from a fresh, asserted synthetic execution.
They are labelled as such inside each figure, in the guide and in blog captions;
no Alerta/Zabbix UI, browser session or native-service success is implied.
The complete ZIP includes PNG renders, the SVG and HTML sources, an execution
record and a manual capture recipe. Actual UI screenshots remain a follow-up.

The new documentation harness reuses the existing Alerta fixture and executes
real PostgreSQL WASM capture/worker/projection code with mocked Zabbix. It is
included in the supplemental reference phase and mapped to T1/T36. The runtime
and SQL contract is unchanged; no migration, grant or frozen-baseline amendment
is needed. Source packaging now selects SVG/HTML documentation assets.

Re-running reference checks after the version bump caught the export gate's a10
constant. Only its expected version was updated; all export assertions remain.
The second full reference run passed. The later final capture and all packaged
run records carry the unchanged final source/harness digests. a10 service
records remain in the original archive and are not rebound to this release.

## R16 — 0.1.0a12: service execution of the illustrated candidate and browser captures

The a11 packet was verified first (checksums, bundle on the a10 candidate,
byte-identical wheel rebuild, release verifier, export audit) and its offline
results reproduced exactly in a fresh pinned environment. a11 recorded native,
real-Zabbix and deployment-path suites as NOT_RUN and could not take browser
screenshots. a12 ran the R14 local runners on the a11 code: 42 native passes
(the walkthrough case is now part of the native `alerta` phase), 5 real-Zabbix
passes and the deployment lab's 4 cases from the installed a12 wheel. No product
defect was found.

Browser capture was possible here: headless Chromium rendered the three
transcript pages over `file://`, and — after the deployment-lab run — the Zabbix
7.0.10 frontend of the disposable lab instance was captured on the acknowledged
synthetic event's details page. That image is a real product UI screenshot of
lab data: it shows the reference source fact, the worker's `[zabalack]`
acknowledgement, the update fact, a human update and the recovery fact, the same
sequence the deployment record's thirteen observations describe. The capture
command uses Playwright, which is not a pinned dependency, so it is supplied in
the complete handoff's `verification/` rather than as a repository tool;
`docs/visuals/browser-captures.json` records the Chromium version and hashes.
No Alerta UI screenshot exists because the Alerta Web UI is a separate project
that the lab does not install; that remains a follow-up, as do the Compose forms
of both labs, every hosted workflow, the signed attestation and G1–G8.


## R17 — Independent a12 review and effective-role correction

The supplied a12 packet was checked first. Its wheel, normalized sdist and
source ZIP rebuilt byte-identically before any corrections. The original
reference suite and native phases were executed independently. A recent
source-built Zabbix 7.0.30 made additional real API negative tests possible.

The review found the lab role-default defect described narrowly in A4. Explicit
individual rules and effective read-back replace an insufficient default-setting
assertion; the mock now models upstream rule inheritance. No production Python,
SQL, grant, migration, source event contract or acknowledgement action set was
changed. The runtime wheel remains the original candidate; the corrected source
and sdist intentionally include the new lab/tests/docs and current evidence.

Run records keep original and corrected source/harness identities separate.
Failed first-attempt supplemental tests and the regression against original a12
are retained rather than hidden by later passing runs. Two new supplemental
assertions were corrected to accept upstream integer result IDs as the existing
client already does. Another fixture setup attempt was rejected because Zabbix
requires at least one enabled UI element; the independent fixture now uses the
shared helper's existing monitoring.problems default, with the same restricted
API and action rules. These review-helper issues did not change product code.

QEMU supplies real unprivileged OS users and native processes in this sandbox;
it does not provide production performance or HA evidence. Source-built 7.0.30
contract/API checks and the pinned 7.0.10 installed-wheel notification pipeline
are separate evidence layers. The complete handoff includes source hashes,
executed helpers, bounded records and a reproduction recipe, not private
credentials, raw logs, VM disks or OS binaries.

The screenshot metadata was stale and pointed at a nonexistent source PNG path.
It now identifies the supplied a12 captures and the complete-handoff locations.
No new browser capture is claimed. The website publishes the review findings
and practical documentation while keeping the previous screenshot labelled
7.0.10. The plugin is not pushed or uploaded; this is a reviewed edition of a12,
not a new tagged release. A2 and every deployment/publication gate stay open as
specified in INPUTS_REQUIRED.md.


R17 final-review implementation notes: the role-module split initially omitted
its new dependency from the Docker staging selection. The stager now includes
it explicitly, with an isolated staged-import regression. This review-introduced
omission was fixed before packaging; it is not an original candidate defect.
The first installed-wheel attempt also exposed a 13-second cold trigger.create
under CPU emulation, beyond the synthetic helper’s 10-second RPC bound. Only
that isolated provisioning/observation bound was raised to 30 seconds; no
production transport, delivery deadline or assertion changed. Final evidence
was rerun after these changes; the intermediate 74e… harness records and failed
setup attempt remain separately retained in the handoff.

The subsequent installed-wheel attempt produced 3 passes, 1 failure and twelve
true observations. The projection check sampled immediately after ledger
completion, although completion and projection are separate commits; the
retained database later showed projection ok. The lab now waits boundedly for
the identical full projection predicate before continuing. A deterministic
regression rejects pending projection, wrong event, wrong status and wrong
history type. This corrects an asynchronous observation race, without changing
production Python/SQL or weakening the required final state. The intermediate
022… records and this failure are retained; final evidence is rerun on 2727….

## R18 — 0.1.0a13: the independent a12 review adopted, verified and extended

The independent review of a12 was verified before adoption. Its packet
checksums, retained original a12 archive (byte-identical), review bundle and
rebuilt artifacts all checked out, and the wheel it ships is the original a12
wheel. Its central finding (A4) was reproduced independently here on a
separately built Zabbix 7.0.10 instance rather than taken on trust: the original
rule set left every User-type action enabled and a real suppression request was
accepted, while the corrected enumerated rules produced the specific upstream
permission denials. The review's corrections — `lab/zabbix_roles.py`, the
effective read-back before user/token creation, the staged-context dependency,
the bounded lab provisioning timeout and the full projection-predicate wait —
are adopted unchanged, with their ten regressions.

a13 extends the correction to the path the review did not cover: the
operator-provisioned real-Zabbix fixture. `lab/real_zabbix.md` now requires the
same enumeration and read-back and names the `role_rules` constructor for the
worker and witness shapes, two regressions pin those shapes, and the real-Zabbix
contract suite proves the boundary on the live server by requiring the specific
suppression-permission denial for the supplied token. The lab fixture used for
this candidate's own runs was re-provisioned with enumerated rules first.

Every suite was then re-executed on this tree in the local lab: reference,
quality, SQL, webhook, property, mutation, documentation capture, native
PostgreSQL 18.3 / PgBouncer 1.24.1, real Zabbix 7.0.10 through the recorder and
the deployment-path lab from the installed a13 wheel. The review's own QEMU
records, including its 7.0.30 layer, its failed attempts and its intermediate
harnesses, are retained in the handoff as received and are not rebound to this
tree. Runtime Python and SQL remain unchanged; A2, the Docker Compose forms,
hosted workflows, the signed attestation and G1–G8 all remain open.

## R19 — a14 task-specific agent guidance and retained local review runs

**Baseline:** the supplied zabalack-0.1.0a13-complete.zip, SHA-256
`bfb06eca2b70f4c7b09e43a327b1929e39e9ccce8c954f0cb78afb2bb250a620`,
Git commit `a4ccf3a2bae61f76a1621d365ff1c83405302c58`. This is a fresh
implementation from the last artifact the user actually received. No unavailable
later checkout or conversational test claim is accepted as evidence.

**Reasoning:** the old AGENTS.md imposed the full 906-line design/reference set
and broad verification on every edit. Task routes preserve its invariants while
making a small documentation change proportionate. Three repository skills expose
only the relevant workflow; substantive references remain in their existing
files. Completion examples state the desired finished result and the already
approved local work, without extending production or publication authority.

**Execution tooling:** a review wrapper serializes selected existing runners,
retains UTC-named or explicitly single-use attempts and changed evidence, and
records an interrupted process separately from confirmed service cleanup. It does
not treat an inherited report as fresh, delete an old attempt or silently proceed
after a failed phase. Direct runner concurrency remains a documented cooperative
boundary. The mutation runner also locks its latest pointer and retains separate
PR/nightly and per-attempt reports; existing strict behavioural-kill semantics
are unchanged. Newly created private leaves do not chmod shared ancestors, so
service users retain their own intended traversal rights.

**Packaging:** the source distributions include the three skills and evaluation
cases. A separate guidance manifest fingerprints 16 instruction/context files;
build and verification compare actual bytes. It is not a signed attestation,
model benchmark or evidence that a ZIP-uploading host loaded those instructions.
Pytest's project path includes tools so new test modules can use top-level
imports without per-file sys.path bootstraps or lint suppressions.

**Validation:** focused tests cover lock exclusion/release, unique attempts,
private paths, interruption, source changes during execution, fresh evidence,
profile separation and artifact tampering. The five independent agent tasks are
run after these implementations, with input identities and observable results in
the complete handoff. The review labels fresh reference checks and historical
native/real-source results separately. G1–G8 and A2 remain open.

**Scope:** no runtime semantic change, SQL amendment or new database migration.
All original source/interface pins, publication inputs and inactive production
configuration are preserved. Existing website assets and screenshots remain
labelled with the versions they actually depict.

**Review follow-through:** the independent evidence review correctly rejected
all twelve stale records in its deliberately historical input and left all 307
input files unchanged. Its findings also exposed the pending publication
register's inherited version; final inspection found the inactive publishing
template's matching stale tag. Both now name a14 without enabling publication.
The real-Zabbix README now directs readers to the current record and accurately
labels a13's six historical passes. Those harness changes required another fresh
reference/mutation/property run; earlier attempts remain separately retained.
The initial reference attempt exposed two minimal-audit-fixture failures: the
new mandatory guidance files were missing from the audit's required-file list.
The list was corrected while keeping the strict guidance check and both tests.

**Artifact-review feedback:** E5 verified actual source/archive/guidance bytes and
identified an inherited a13 attestation example and an implicit read-only branch
in the release-review skill. The example now names a14, and the skill explicitly
keeps inspection-only requests read-only. These are documentation/instruction
changes outside source/harness digests. Guidance validation and artifact builds
were repeated, and E5 received a separately hashed final set for follow-up.

## R20 — 0.1.0a15: a14 verified and executed; exported evidence mode corrected

a14 was checked before anything was changed: its checksum lists, its retained
byte-identical a13 archive, its Git bundle and lineage, its artifacts, the
release verifier and the export audit, and every offline result — reference
853/23/0, 8 SQL groups, 15 webhook groups, mutation 12/12 and 20/20, property
46, quality, quickstart and the documentation capture — reproduced exactly in a
fresh pinned environment. Its a13→a14 diff changes release tooling, tests, docs
and version metadata only; no service-path file under `lab/` or `tests/` and no
`src/` change beyond the version string. a14's agent guidance, repository
skills, guidance fingerprint, retained review runs and separated mutation
profiles are kept as delivered.

Verification exposed one packaging defect. `tools/run_mutation_checks.py` writes
its published records through `tempfile.mkstemp` and `os.replace`; the temporary
file is created 0600 and `os.replace` preserves that mode, so the exported
record is owner-only while every other `evidence/*.json` is 0644, so another
account on the producing machine cannot read it. Whether that mode reached a
consumer depended on the handoff: the delivered a10, a12 and a13 archives carry
`plugin/source/evidence/mutation-run.json` at 0600, while a14's complete ZIP,
source ZIP and sdist store the same records 0644 even though a fresh a14 run
still produced them 0600 — the difference is that this project's packaging
copies source files with `shutil.copymode` and publishes whatever mode the
writer left, while the other producer's assembly normalised to 0644. The writer
defect dates from a7, where the mutation runner was introduced; a14 enlarged it
from one record to three. Those two
profile records are validated when present and are not required to be present —
removing both from a copy of the tree produces no `evidence_binding_findings` —
so this amendment does not describe them as required.

Each writer now names the mode it intends — `0o644` for published evidence,
`0o600` for the private diagnostic copy that stays inside `build/private/mutation`
(mode 0700) — and `tools/review_runs.py`'s writer takes the same explicit
parameter while keeping its private default for retained review output. To catch
the class rather than this instance, `tools/public_audit.py` gains
`export_mode_findings`: any exported file that is not readable to group and
other is now a finding (`public-file-not-world-readable`). Two regressions
cover both halves — the mutation runner's published and private modes, and the
audit rule's positive and negative cases.

The class rule immediately earned itself: with the mutation records fixed, the
first audit of this tree reported a second instance in a different tool.
`tools/record_real_zabbix.py` writes `evidence/real-zabbix-run.json` through the
same `mkstemp` and `os.replace` pair and had published it 0600 since a5. That
writer also produces the NOT_RUN record, so a live Zabbix was never needed to
reproduce the mode: the retained a7 packet already carries a NOT_RUN
`evidence-a7/real-zabbix-run.json` at 0600. It went unreported until this tree
because nothing had yet checked exported modes as a class. That writer now sets
the same public mode. All four temporary-file writers in the repository are
explicit about the mode they intend.

The three service suites a14 recorded as NOT_RUN were then executed on this
tree: native PostgreSQL 18.3 / PgBouncer 1.24.1 (42), real Zabbix 7.0.10 through
the recorder (6, including the A4 excluded-action denial) and the deployment-path
lab from the installed a15 wheel (4, with all thirteen observations). Those runs
belong to a15. a14 changed no service-path file, which supports an inference
about its service behaviour, but a14's own records remain NOT_RUN and are not
restated as executions. The Docker Compose forms, hosted workflows, the signed
attestation, A2 and G1–G8 all remain open.


## R21 — 0.1.0a16: the independent a15 review verified and adopted

The review of a15 was checked before any of it was accepted. Its 46 checksum
entries verify, and the a15 packet it retains is byte-identical to the delivered
one (`cf397f91…`), so it reviewed the release rather than a copy. Its manifest
declares no change to the supplied release and no new version, and the bytes
agree. Its independent execution matches this project's: reference 855/23/0,
8 SQL groups, 15 webhook groups, mutation 12/12 and 20/20, property 46, quality
and quickstart, with the wheel, normalised sdist and source ZIP rebuilt to the
supplied bytes. It reports zero Gitleaks 8.24.3 findings over the source and the
history through the a15 commit; that is the review's evidence, and
`evidence/security-scan.json` here remains NOT_RUN for its own environment. It
inspected rather than re-ran the three service records and says so.

It found no runtime or SQL defect and proposed no product change. Its
corrections are to release prose, and each was reproduced here before adoption.

The R20 history was overstated. "Shipped in every handoff since a7" describes
the writer, not the handoffs. The stored mode of the published records in each
delivered archive is a3/a4/a5/a8 0644, a9 0644, a10 0600, a11 0644, a12 0600,
a13 0600, a14 0644, a15 0644, recorded in `verification/archive-modes.json`; for
packets whose own zip was consumed the unzip-extracted tree was read, after
confirming that extraction preserves modes. The mechanism is packaging: this
project copies source files with `shutil.copymode` and therefore published
whatever mode the writer left, while the other producer's assembly normalised to
0644. A fresh a14 run still wrote 0600, so the writer was wrong throughout; the
consumer-visible consequence was not.

The two profile mutation records are validated when present and optional when
absent: removing both from a disposable copy produces an empty
`evidence_binding_findings` list. Describing them as required was wrong, and the
rule itself is unchanged.

Service runs belong to the candidate that executed them. Unchanged service-path
code supports an inference about an earlier candidate's behaviour; it does not
convert that candidate's NOT_RUN records into executions. R20's wording and
a15's report and review are corrected accordingly.

`tools/verify_release.py` imports `tools/agent_guidance.py`, which imports
PyYAML, so it is not the stdlib-only verifier `START_HERE.md` claimed. The
handoff instructions now name the dependency.

Two owner-only records do remain in the handoff, inside
`reviews/0.1.0a7/plugin/evidence-a7/`, retained exactly as received; they are
the only two of the twenty-one records in that directory below 0644.
`verification/handoff-checks.json` already recorded them accurately in a15 —
only the review prose said there were none. One of them is a **NOT_RUN**
real-Zabbix record at 0600, which disposes of a15's claim that this instance was
invisible while no candidate could run the suite: the NOT_RUN branch uses the
same writer, the mode was already in a shipped artifact, and nothing had
examined exported modes as a class until R20 added that rule.

The review's ten writer regressions are adopted as
`tests/release/test_evidence_modes.py`: both public writers and both private
writers, each under a permissive (0o000) and a restrictive (0o077) umask, plus
replacement of an existing 0600 file and an explicit publish through the
private-by-default review writer. They discriminate — against an unmodified a14
checkout the four public cases fail on the mode itself (0600 observed where 0644
is required) and the other six fail because a14's writers took no `mode`
parameter — and all ten pass here. The `Still owner-only` changelog headings are
renamed `Still pending` so the phrase cannot be read as a file mode.

The review's Gitleaks run prompted one more attempt here, which failed honestly.
A Go 1.24.7 toolchain is now present and the pinned commit
(`107a41827bb6698dbbff756b2300537774a1d84c`, v8.24.3) clones from github.com,
but the build needs modules from `proxy.golang.org`, `golang.org/x`, `gopkg.in`
and `dario.cat`, all outside this environment's egress allowlist. Rewriting
`go.mod` to fetch mirrors would produce a binary that is no longer the pinned
upstream build, so it was not done. `docs/gitleaks_reference_manifest.json` now
gives that reason instead of the stale "Go toolchain unavailable", and the layer
stays NOT_RUN.

Nothing in the review's own environment is restated as this project's evidence,
and no historical artifact was patched in place: the corrections are made in this
candidate, whose checksums and artifacts are regenerated, exactly as the review
instructed.


## R22 — 0.1.0a17: explicit integrated recovery candidate identity

The integrated PR25 correction changes how ambiguous remote write errors remain
uncertain, suspends delivery while permitting read-only reconciliation, and couples
the runtime to migration 010's recovery capability (A6). Its preceding a16-labelled
Actions snapshot remains distinct from the earlier qualified PR24 a16 wheel.
The new candidate therefore receives version a17 before artifact qualification.

This version/export metadata update adds no runtime feature and changes no SQL
body or dependency pin. It deliberately changes source/harness identities; old
evidence stays historical and cannot satisfy the new builder by relabelling.
Obtain final-input required Actions and authentic producer/package evidence, then
qualify that exact wheel's affected installed recovery behavior. Preserve original
failures and skipped/unavailable layers. A reviewed implementation is not formal
GitHub approval, and local service results do not close deployment gates.

The a16 narrative and qualified artifacts are retained. Native x64 remains deferred;
installed-account permissions, current security review, owner publication inputs
and all applicable beta, amendment and deployment requirements remain open.
No tag, upload, merge, release or deployment is authorized by this amendment.

## R23 — Coherent wheel build-tool correction

The build requirement and installed development lock now both pin `wheel==0.48.0`.
PR #2 changed only the former, leaving `requirements.lock` at 0.46.2. The
[upstream release notes](https://wheel.readthedocs.io/en/stable/news.html)
identify the fix for `wheel convert` output-path traversal (GHSA-vgq5-9859-3mmw).
The repository builds its own source with setuptools and does not invoke that
conversion command; this correction does not establish a runtime exploit path.

The new PyPI wheel's actual bytes and metadata were inspected and hashed; its
dated entry is appended to `tooling_reference_manifest.json`. Its Python >=3.9
and packaging >=24 requirements are compatible with the existing Python 3.12
target and packaging 26.3 pin. Other pins and the runtime, quality and scanner
hash locks are unchanged. `requirements.lock` remains an exact-version lock
without hashes; adding one isolated hash would enable pip's hash enforcement
without supplying the other required hashes. Historical R1 and compatibility
observations retain their original versions.

This changes the harness binding, not the application or SQL bytes. Preserve the
merged-main and retained e5c9e212 wheel results at their original identities.
Before freezing a replacement candidate, obtain the required ARM64 Actions jobs
and dependency/secret checks at the new revision, retain authentic producer and
package records, and rebuild reproducibly with the corrected toolchain. Qualify
the exact retained output non-editably with the installed migrations and reseal
procedure, including affected recovery and reader cases. A Windows build smoke
check is not ARM64, installed-environment or beta acceptance evidence. No old
evidence is relabelled and no acceptance or publication gate is waived.

## R24 — 0.1.0a18: replacement candidate identity (alpha under qualification)

The owner selected a replacement candidate instead of freezing PR #26 as-is. On
base `a3317dc` it combines R23's wheel pin correction, the pin-consistency check,
forward migration 013, four Python corrections from the a17 application-security
review, the logging redaction and access-log route tests, the populated
delivery-ledger upgrade coverage and the 21 September readiness documentation.
Migrations 001–012 are byte-identical; 013 is the reviewed file. Only the
manifest's `package_version` changes with this identity; every file hash in
`docs/sql_manifest.json` is unchanged.

`0.1.0a18` is an alpha under qualification toward beta, not a beta or release
candidate. The a17 wheel `e5c9e212…` and all a17 and earlier results keep their
original identities and are not evidence for a18. Required Actions runs, the
release build and exact-artifact qualification, independent security review,
approved private retention and the owner inputs remain open. No tag, upload,
merge, release or deployment is authorized by this amendment.

## R25 — Routine Actions execution moves to a native x64 runner

The former dedicated Linux ARM64 runner went offline, leaving required runs
queued. The owner authorized a staged replacement: one repository-specific
Ubuntu Server 24.04 amd64 QEMU/KVM guest, `zabalack-omarchy-x64`, labelled
`[self-hosted, Linux, X64, zabalack]`, with Docker inside the guest. Every active
job now selects that label set. The trusted hook's platform policy accepts only
`Linux`/`X64` and refuses ARM64 and every other platform. The security job
installs the official Gitleaks 8.24.3 Linux x64 archive with its own inspected
checksum; the version, source pin, account/path contracts, lab lock,
retention-before-reset, required job names, producer/package chain and trust
gates are unchanged. CodeQL stays behind its enablement gate.

Earlier ARM64 runs remain ARM64 evidence at their original revisions. Routine
x64 CI does not by itself close the x86_64 acceptance gate or any other
qualification, security, installed-environment or publication requirement.
`main` and scheduled runs keep the old definitions until a separately
authorized merge.

## R26 — Routine native CI without Docker: lab lifecycle, retention and reviewed service identities

Numbered after the a18 candidate's R23–R25 (PR #29), which this change is
prepared to integrate onto. The routine Actions `native` job runs
`tools/native_local.py --isolated-lab` under the same shared host lab lock
instead of `tools/native_ci.py` with its Docker network-retention arguments;
the step names the installed services (`ACK_LAB_PG_BIN`,
`ACK_LAB_PGBOUNCER_BIN`). The Compose form is unchanged apart from the shared
run-start preservation and remains for local Docker hosts.

Lifecycle. `lab/local_runtime.py state` classifies the checkout's lab as
absent, stopped, running, unmarked or ambiguous without touching it, and
`stop` exits 0 only when the cluster is observed stopped. The local runner
resets only an absent or stopped lab; a running marked lab is stopped and
re-inspected first; unmarked, ambiguous or failed-to-stop state refuses the
reset and fails the phase with nothing removed. PgBouncer refuses to start over
a live PID or an occupied port. Cleanup of PgBouncer and PostgreSQL is
independent: both outcomes are recorded in `cleanup`, `shutdown_exit_code` is
0 only when both succeeded, `shutdown_reason` names the failed services, and
the phase's original `reason` is never replaced.

Retention. `tools/lab_retention.py` preserves diagnostics under
`build/private/retained-phases/<run>/<label>/` with verified digests and a
manifest: the previous run's reports before invalidation (both runners), the
lab logs before each `.lab` reset, and each phase's raw JUnit, log, exception
log and pool log at its end. A preservation failure refuses the corresponding
destructive step; at run start nothing is invalidated and the previous report
is left in place. End-of-job hook retention is in addition, not a substitute.
The trusted job hook refuses to archive or reset a workspace with a live lab
PID, an unreadable PID file or any visible process working inside it.

Identities. `lab/native_service_identities.json` fixes PostgreSQL major 18 and
PgBouncer 1.24 or newer (the former check accepted any 1.x) and lists reviewed
package or binary identities. The runner records the executable identities it
used; a packaged executable that is not reviewed refuses the run, a source
build is labelled UNREVIEWED, and `collect_ci_evidence.py` accepts a
`not_docker` native record only with both services REVIEWED. The reviewed list
ships empty: the runner host's packages must be reviewed and added before its
run can be packaged.

Regressions: `tests/release/test_native_local_lifecycle.py`,
`test_local_runtime_state.py`, `test_lab_retention.py`,
`test_self_hosted_job_live_services.py`, and additions to
`test_native_evidence_failures.py` and `test_ci_collection.py`; each fails with
its fix removed. This amendment records an implementation with author
verification only. It does not execute on the x64 runner, does not review the
runner host's packages, does not uninstall Docker or change any privilege, and
authorizes no merge, runner activation, release or deployment.

## R29 — Native deployment path: installed-wheel lab orchestration and a Docker-free deployment job (prepared)

Numbered after R26–R28. `lab/deployment_native_lab.py` supplies the two
services `tools/run_deployment_e2e_local.py` (R14) needs from artifacts
installed on one host: a dedicated PostgreSQL 18 cluster on loopback 5432
(`lab_admin`, `scram-sha-256` host logins, database `alerta`) and the
disposable Zabbix instance of R27 with its frontend on 8080. It classifies
the lab (`absent`, `stopped`, `running`, `partial`, `unmarked`, `ambiguous`)
without touching it, refuses occupied or unmarked state, verifies but never
changes the host layout the runtime fixes, prepares a runner environment from
the wheel only (refusing one that imports the checkout), is its own reset
command (fresh `alerta` database with the lab roles dropped, Zabbix reloaded
from the schema at the pinned reference), stops with exit 0 only when every
component is observed stopped, and wipes only a stopped lab. `cycle` runs
start, run, stop and wipe as one invocation from an absent lab with cleanup
after every outcome; a passing run that leaves anything behind exits 5. A
root start without a usable unprivileged identity is `BLOCKED_ENVIRONMENT`.

The local runner gains the Compose form's supplied-wheel receipt
(`--wheel-sha256`: exact bytes, project identity, payload equal to the
checkout, installed bytes equal to the wheel; `artifact_mode: supplied_wheel`
in the record); the unchanged `tools/deployment_evidence.py` already accepts
that receipt on a `not_docker` record, which a test now pins. The trusted job
hook's live-service guard covers the deployment and Zabbix lab PID files.
`tools/lab_retention.py` reports an unreadable diagnostic as a
`RetentionError` rather than an uncaught `PermissionError`, so the caller
refuses its destructive step; the unprivileged run of the suite found it.
`tools/native_local.py` records an interrupted phase (reason `interrupted`,
stage, cleanup, retention) and ends the run as FAIL with exit 130; before,
the killed pytest child was cleaned up but the record stayed at RUNNING
without the phase.

The manual `deployment-e2e.yml` job is proposed in the Docker-free form:
prepare the runner environment from the candidate wheel, then
`lab/deployment_native_lab.py cycle --isolated-lab` under the shared host
lab lock with the installed services named by the step environment. As first
delivered the job built a wheel of its own; the corrections below make it
consume the exact candidate wheel instead. The trigger, enablement variable,
runner labels, artifact name, record path and upload-on-failure rule are
unchanged (the permissions gain read-only `actions` access for the artifact
download); a missing prerequisite fails the step (NOT_RUN is exit 2), so the
gate is neither removed nor made optional. The Compose runner remains the optional Docker form. Host
prerequisites are verification items for the runner handoff
(`docs/self-hosted-runner.md`); none is provisioned by this change, and no
hosted run of the native form has been recorded.

Corrections from the author cycles: the cycle preserves both labs'
diagnostics (`tools/lab_retention.py`) before its wipe and refuses the wipe
when preservation fails; `lab/zabbix_native_lab.py` starts `zabbix_server`
and the PHP server with `PATH`, `TZ` and `LANG` only, because an inherited
proxy setting made the lab's loopback webhook deliveries fail; and a schema
loaded without its initial data is refused at load. Package identities for
the runner host are collected there with
`tools/collect_package_identities.py` (dpkg, apt origin, InRelease signature
against the installed keyrings, `dpkg -V`, version line), validated against
the pin and the policy (a missing observation is INCOMPLETE, a source build,
foreign origin, unverified signature or off-pin version DEVIATES), and
proposed for review from a CONFORMS record only; the reviewed lists stay
empty until that review. `tools/run_quality.py` adds `ruff-infrastructure`,
a bounded lint of the listed Docker-free lab and runner files.

Corrections after the non-author review of the delivered `32c8268`
(2026-09-23; eight corrective commits in seven groups and two documentation
commits layered on it, none rewriting delivered history,
each with a regression that fails with its fix removed):

- R1, R13: the deployment record is invalidated (NOT_RUN with a public reason
  code) before any service work and on every refusal, and bound to the cycle
  outcome afterwards; a failed stop, retention or wipe, a lab left behind or
  an interruption is FAIL, never an uploaded PASS; the validator requires a
  native-lab record to end with the lab absent and the run at exit 0; a
  NOT_RUN record carries no artifact-mode or supplied-wheel metadata.
- R2, R3, R6, R15, R16: the runner-account checker observes the exact
  transient unit and its cgroup processes, compares every uid, gid, group and
  capability field of every candidate, resolves socket links, classifies
  connects, and never socket-activates the daemon it is checking.
- R4, R5, R7, R18, R19: the identity collector derives INCOMPLETE from the
  record, pins each origin by full fingerprint, treats a failed `dpkg -V` as
  a deviation, accepts `Architecture: all` only where the package is, checks
  the PostgreSQL client members, and binds identities to the signed index;
  the reference records the corrected repository URL and the reviewer's index
  observation; the 7.0.30 fallback is retired now that 7.0.31 is confirmed
  in the noble index (its former rule stays as a historical note).
- R8, R9, R20: SIGTERM and SIGHUP join SIGINT in one cancellation path with
  deferral during cleanup and process-group termination of the children.
- R12, R14: the Zabbix lab cluster accepts scram logins only, from generated
  passwords kept in the marked lab directory; a PID file is signalled only
  when the live process matches the identity recorded at start, and never a
  process group.
- R17, R21: the recorder enforces the pin on the execution itself and the
  prepare script checks `apiinfo.version` before login; archive verification
  enforces the root prefix and refuses duplicate entries and members.
- R10, R11: the deployment job qualifies the exact candidate wheel named by
  its dispatch inputs (run, revision, digest) and verifies the whole
  installation inside the runner interpreter; `artifact_mode: supplied_wheel`
  now describes what the job does.

Still NOT_RUN from the preparing environment: every host observation that
needs the x64 runner (checker and collector runs on the guest, the packaged
Zabbix instance, the hosted deployment dispatch).

Integration corrections after the re-review of `19d0fc7` (Opus, 2026-09-23;
sixteen commits O1–O15 and O13b layered on it, each with a regression that
fails with its fix removed; author verification and same-session technical
review only, not independent review or approval): held
qualification packages accepted; the checker reads systemd's real `Listen`
format and never connects to an activated socket; every unexpected cycle
error is recorded and the runner's record stays pending until the cycle
binds it; the deployment job invalidates the tracked record first, runs the
lab under `exec flock -n -F` so cancellation reaches Python, and requires a
`ci.yml` candidate run that built the revision itself; the installed
environment is verified with `python -I -S -B` against the venv scheme,
`requirements.lock` pins and RECORD hashes; the identity collector pins
repositories and suites, reads gpgv per signature block, matches signatures
by repository and suite, reads index digests only from gpgv-verified
content bound to the pinned signature, finds epoch-named cached `.deb` files
and verifies `postgresql-client-18`; the group escalation keeps one deadline
and the first cause and cannot signal a reused group id; bundle paths must
be canonical; `pg_hba.conf` is read with PostgreSQL's quoting rules. The runner-host observations then ran on
`zabalack-omarchy-x64`; their review and any identity approval are recorded separately (not yet).

Corrections after the non-author review of `b57bebb` (2026-09-23; five
commits O17–O21 and test commits O22 and O19b layered on it, each correction with a
regression that fails with its fix removed; author verification only): inside
a cycle the runner writes `PENDING_BINDING` and only the cycle sets and
validates the final PASS, so a pending record never validates and a real
cycle can pass (pinned through the `cycle` command and the real runner); `pg_hba.conf`
blanks and record ends follow PostgreSQL; the only startup hook allowed is
setuptools' pinned `distutils-precedence.pth`, and the record states that
dependency files are checked for RECORD consistency only; the packaged
frontend is staged without its dangling configuration link; the runner
interpreter starts with its own `libpython` in the minimal child
environments. A manual native `cycle` of the a18 wheel on
`zabalack-omarchy-x64` then passed (4 passed, lab absent, final record
validated); it is a verification run, not a qualification record.

Corrections after Astra's review of `e8ef82b` (2026-09-24; author
verification only): the cycle publishes its final record once, and a
termination signal received before that publication makes the outcome an
interrupted FAIL with exit 130 instead of a published PASS followed by a
traceback (F1); `pg_hba.conf` is read as raw bytes with PostgreSQL's line and
blank rules (F2); the runner interpreter's library directory is named only
for its own `libpythonX.Y.so.1.0` file (F3).

## R28 — Docker requirements inventory and runner-account verification (prepared handoff)

Numbered after R26 (Docker-free routine native CI) and R27 (native Zabbix
reference). `docs/docker_requirements_inventory.md` classifies every Docker
dependency at a17 and the a18 candidate as required by the routine path,
optional retained support or historical record, and names what R26 changes:
after it, no routine Actions job needs Docker; the deployment lab's Compose
form, the Compose native form and the inactive publication template remain
optional. `tools/runner_account_check.py` is a read-only report of the
dedicated account's host capabilities against a `docker-allowed` or
`docker-free` policy for the operational handoff. Small corrections align the
local-runtime blocked-environment message, the Makefile's local deployment-lab
target and the two scope statements about local-process records with R14.

No host is changed: removing the runner account's Docker group membership,
removing Docker from the guest and rewording the "root-equivalent Docker
access" statements are separately reviewed operational steps.

## R27 — One exact native Zabbix 7.0 reference: pin, disposable instance and coordinated validators

Numbered after the a18 candidate's R23–R25 and the Docker-free routine CI
change R26. `lab/zabbix_reference.json` selects Zabbix **7.0.31** (tag commit
`fc62e967…`, released from 7.0.31rc1 unchanged) as the one patch the disposable
reference lab, the deployment-path lab and their validators target, with the
facts verified at the tag (frontend and API version 7.0.31; PostgreSQL 13.0 to
18.x supported since 7.0.20; `AllowUnsupportedDBVersions` default 0; PHP 8.0
minimum and the frontend's ini minima), the Ubuntu 24.04 package identities and
signing key still to confirm on the runner host, the verification gate, a
7.0.30 fallback rule (retired on 2026-09-23 once 7.0.31's presence in the
Zabbix noble index was independently confirmed; the rule remains as a
historical note) and the changes since 7.0.10 that touch the lab.

Producers and validators read the pin instead of carrying `7.0.10` literals:
`tools/deployment_evidence.REFERENCE_VERSIONS`, the deployment provisioner's
reference version and commit, the lab runtime's approved version and endpoint
API version, the Compose runners' image tags, the staged Docker context and
the mocked tests. `lab/zabbix_native_lab.py` provides the package-installed
disposable instance without Docker: a marked directory with its own
PostgreSQL 18 cluster and the packaged schema, a loopback-only
`zabbix_server`, and the packaged frontend served privately at the fixed lab
API; state classification, refusals of unmarked, ambiguous or running state,
a verified stop, a fresh-database reset and a status record of component
versions and package identities. `tools/record_real_zabbix.py --services-file`
records those identities; since the 2026-09-23 corrections the recorder
refuses any run whose observed version is not the pin, with or without that
file, and `lab/real_zabbix_prepare.py` refuses an instance that is not the pin.

Historical results are preserved: the 7.0.0 reference-source inspection, the
7.0.10 deployment-path and real-Zabbix records and their manifests keep their
own bindings and are not rewritten. Author verification only: no packaged
instance existed in the preparing environment (repo.zabbix.com unreachable),
so the package identities are not reviewed, the pinned version's Ubuntu
packages are not confirmed and no service run at 7.0.31 exists. Installed
artifact qualification uses the packaged instance on the runner host; a
source-built instance is an author convenience, never a substitute.

## R30 — PyJWT advisory in the development and lab-runner lock

**The finding.** A pip-audit 2.9.0 lookup of the pinned `requirements.lock` reported one advisory on 2026-09-29:
- **Package:** PyJWT 2.13.0.
- **Advisory:** [GHSA-w6j9-cwv2-h6wq](https://github.com/advisories/GHSA-w6j9-cwv2-h6wq) / CVE-2026-102274.
- **Effect:** a malformed RSA key in a JWK Set aborts parsing of the whole set. It is an availability defect only,
  CVSS `A:H`, CWE-755.
- **Affected versions:** 2.9.0 to 2.13.0; fixed in 2.14.0.
- **Timing:** the maintainer published it on 2026-09-08. The GitHub database reviewed it on 2026-09-29, after the
  previous clean scan.
- The failing scan is retained as its own attempt; the earlier clean scan keeps its original time and scope.

**Where PyJWT is used:**
- `requirements.lock` pins PyJWT only as a dependency of `alerta-server==9.1.0`, which declares `PyJWT>=2.0.0`.
- The zabalack package does not import it, and the runtime lock does not contain it.
- The lab deployment runner installs `requirements.lock`, so the pin is part of the tested lab deployment path. The
  production shadow installation uses the runtime lock and provides Alerta separately.

**The change:**
- Only the PyJWT pin changes, from 2.13.0 to 2.14.0.
- The PyPI wheel `pyjwt-2.14.0-py3-none-any.whl` was downloaded and verified against its PyPI SHA-256,
  `ad0cef71c756a56e74863c2919cf0985f72decbcfcb550ee2f422e7c62b5eedc`.
- Its metadata declares `Requires-Python: >=3.9` and needs no new dependency on Python 3.12. `cryptography` stays an
  optional extra, already pinned.

**What is and is not claimed:**
- The lock carries versions without hashes, as R23 describes.
- The change moves the harness binding, not the application or SQL bytes.
- Compatibility is established by the focused Alerta/JWT tests and by the candidate's fresh gates and scans, not
  inferred from the version number.
- Earlier records keep their original bindings.

## R31 — Retained, owned scratch for local gate runs

**The problem.** A local candidate run lost trial material in two ways:
- pytest's default base temporary directory is the shared `/tmp/pytest-of-<user>`, where each new session deletes all
  but the three newest. A later session removed an earlier session's scratch.
- The mutation runner's variant bytes existed only between its write and its restore.

An outside observer can only verify after the fact. It cannot prevent either loss, or prove whose scratch it copied.

**Owned scratch.** `tools/retained_temp.py` gives each pytest invocation of the gate runners its own `--basetemp`:
- It sits below a fresh `mkdtemp` root.
- Ownership binds a random invocation identity to that root's device and inode, recorded in `OWNER.json` with the
  label and process. A root that is foreign, or was replaced since, is `NOT_OWNED` and is never copied.
- An explicit basetemp is never rotated.
- The root is `/var/tmp/zab-XXXXXXXX/b` when `/var/tmp` is writable, otherwise under the system temporary directory.
  On the local lab host `/var/tmp` is on persistent disk rather than a size-limited tmpfs; that is a host
  observation, not a portable guarantee. The path stays shorter than pytest's own basetemp, because fixtures bind
  Unix sockets below it.

**Retained copies.** Before the child starts, a `PENDING` custody record binding the invocation to its root is written
to the run's private raw directory. However the child ends (including a timeout or interruption, which still
propagates), the scratch is copied there and the record finalised. The original is left in place.
- A record left `PENDING` never counts as retained. `retained_temp.recover` retains it later from the record alone.
- Every open is relative to an already opened directory, refuses a final link, and checks that the opened object is
  the one listed. So a link swapped in during the copy is never followed.
- Links are copied as links. FIFOs, sockets and devices are listed, never opened. Nothing is re-permissioned.
- Unreadable entries are listed. Only the suite's deliberately unreadable fixtures are accepted: regular files with
  mode 0 at the exact paths in `EXPECTED_UNREADABLE`. Any other unreadable entry, including a directory at such a
  path, makes the retention `INCOMPLETE`.
- Destinations are single use. An existing destination, `PENDING` or finished, is refused before anything is written,
  so an earlier custody record is never overwritten.
- `retention.json` inventories every file by size and SHA-256.
- A vanished or changing file, or a copy that does not match its inventory, makes the retention `INCOMPLETE`.
- **Test status is kept separately.** Each record keeps its tests' own exit status or outcome, and adds the retention
  status per invocation. An incomplete, not-owned or failed retention alone prevents `PASS`:
  - the reference record's `all_executed_phases_succeeded`;
  - the property record;
  - the mutation record's `scratch_retention`;
  - quickstart exit 3.

**Where it applies:**
- `run_reference_suite.py`: all six phases. Its public record names the basetemp as `<owned>`, not its random path.
- `run_property_nightly.py`.
- `quickstart.py`: its pytest steps.
- `run_mutation_checks.py`: baseline and each mutant. The runner also keeps `tree-baseline/` before the baseline, and
  each variant's exact executed bytes with `variants/index.json` before that child starts. Its restore and the
  working-copy removal are unchanged.
- `security_scan.py` keeps its copy of the public selection under `build/` instead of deleting it.

**What does not change:** existing record fields, mutant classification and the tested bytes. The records gain only
the retention statuses. The harness digest moves; source and SQL do not. Focused regressions
are in `tests/release/test_retained_temp.py`, `test_gate_scratch_retention.py` and `test_mutation_runner.py`.

## R32 — Reviewed x64 runner service identities; external whole-job exclusion replaces the in-job lab lock

**Reviewed identities.** `lab/native_service_identities.json` `reviewed` now holds exactly the identities approved from
the x64 runner's provisioning record:
- **Record:** `e4064e342636b6a6aaf6d58d1fe56b770f381d990277cf74fa509b9cfbdfb448`, collected read-only on the runner by
  `tools/collect_package_identities.py collect` as the runner account, 2026-10-01T01:01:45Z.
- **Validation:** `validate` reports CONFORMS.
- **Review:** codex-omarchy confirmed the record, disposition
  `e816ec5bc20f6da8c8a01f225348b22851bc3bc25c0a40963e25fed3904d28d4`.
- **The two entries,** produced by `approve` and entered by hand, unchanged:
  - `postgresql-18` `18.6-1.pgdg24.04+2` amd64, `.deb` `c0773357…fa3d`, with members initdb, pg_ctl and psql (the
    last from `postgresql-client-18`);
  - `pgbouncer` `1.25.2-1.pgdg24.04+1` amd64, `.deb` `660146d7…c47e`.
- **Origin:** both come from `https://apt.postgresql.org/pub/repos/apt` `noble-pgdg/main`, with the InRelease verified
  by `B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8`.
- **Not added:** the proposal's `postgresql_client` and `zabbix_reference.ubuntu_packages.verified` entries. No tool
  consumes them. They stay in the retained proposal.
- **Matching scope:** the matcher compares package, version and architecture, so run acceptance must compare the
  observed executable hashes with the record. A re-collection is required after any package change on the runner.

**External whole-job exclusion.** `ci.yml` (native) and `deployment-e2e.yml` no longer take
`flock -n -F /home/codex/zabalack-task-runs/shared-linux-lab.lock`. That in-job lock could not stop work in another
runner instance before its job began, and a held descriptor does not survive replacement of the file. Retiring it is a
prerequisite for the shared-ci broker.
- **The admission step:** both jobs run `tools/runner_admission.py` after evidence invalidation and before any service.
  It admits only under a proven exclusion and refuses anything else:
  - **Phase B:** the broker's read-only status must show the job's own unit and manager InvocationID as the active
    lifecycle in phase `running`, with `quarantined` exactly false, `quarantine_state` `clear`, no pause and no pause
    requested;
  - **Phase A:** the job's own unit must be the persistent runner unit gated by the shared-ci window;
  - the historical transient runner, an unreadable observation or a malformed reply refuses.
- **Not until the exclusion exists:** these workflows are not run on a runner without the exclusion. The step refuses
  there.
- **Signals:** the service invocations keep `exec`, so cancellation still reaches their cleanup.
- **Tests:** `tests/release/test_runner_admission.py`; `test_ci_collection.py` now asserts no flock or legacy path and
  admission before the service; `test_collect_package_identities.py` asserts the exact reviewed entries.
- The harness digest moves; source and SQL do not.

## R33 — Dependency advisory refresh: PyJWT 2.15.1 and urllib3 2.8.0

**The findings** (Daybreak, 2026-10-01, recorded by codex-omarchy as ZB-B1-DEPENDENCY-ADVISORY-REFRESH):
- PyJWT 2.14.0 is affected by GHSA-42vr-xj54-vc7v and GHSA-x33g-cr3x-6449, fixed in 2.15.0.
- urllib3 2.7.0 is affected by GHSA-8988-9cw3-xx77, GHSA-vxq7-64xx-v4gw and GHSA-gh4c-6fx4-qh6g, fixed in 2.8.0.

**The change:**
- `requirements.lock`: PyJWT `2.14.0` to `2.15.1` (Daybreak's proposal and the latest patch); urllib3 `2.7.0` to
  `2.8.0`.
- `requirements-scanner.lock`: urllib3 `2.8.0`, hashed by its wheel
  `0cf3cae568d36aa9576b28dfb35f11328f1cb974ca7647d9475ebb86c75ac6e3`.
- Both wheels were downloaded and verified against their PyPI SHA-256 (PyJWT `42d59d631f7768a1…e193`).
- Neither adds a required dependency on Python 3.12.

**Where they are used:**
- Neither is imported by the zabalack package, and neither is in `requirements-runtime.lock`.
- Both reach the development and lab-runner environment through `alerta-server` and `requests` (which accepts
  urllib3 `<3`). urllib3 also reaches the scanner environment through pip-audit's `requests`.

**What is and is not claimed:**
- Both locks install cleanly with `pip check` passing.
- The installed set differs from C5's only in these two packages.
- The focused Alerta HTTP and JWT tests pass.
- Compatibility for the candidate rests on its fresh gates and scans. HTTPS-proxy behaviour is not exercised by any
  current test, and request-level reachability within Alerta is not demonstrated.
