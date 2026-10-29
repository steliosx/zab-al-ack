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

## R23 — 0.1.0b1: beta candidate identity on the hosted lane

The integrated main after PRs #33, #34, #35, #36 and #37 receives version
0.1.0b1 before artifact qualification (11 October 2026). Active workflows run on the
GitHub-hosted `ubuntu-24.04` runner while the ARM64 runner is held; main passed
every active job and scan on that lane at `347bca378711ad788e49484072a15287ea9a7c10`
(ci.yml run 38137813413; run-specific CI evidence only). This version/export metadata update adds no runtime feature and
changes no SQL body. It deliberately changes source/harness identities; a17 and
earlier evidence stays historical and cannot satisfy the new builder by relabelling.

Minimal publication inputs are recorded (repository, package owner, primary
maintainer, package URLs). Backup maintainer, private conduct contact, the three
review approvals, PyPI namespace confirmation, publication authorization and the
independent external security review remain unset. Native x64 qualification,
installed-account permissions (A4/G5), transport amendment A2 and deployment
gates G1–G8 remain open and are disclosed as such. No tag, upload, release or
deployment is authorized by this amendment.

## R24 — 0.1.0b1 release-policy decisions

Owner decisions recorded 11 October 2026 (see `docs/release/POLICY_DECISIONS_v0.1.0b1.md`):

- CodeQL: **enablement attempted on 11 October 2026; gate set back off until the
  repository is public (owner decision, scheduled enablement, not a waiver).** The repository variable
  `ZABALACK_CODEQL_ENABLED` was set to `true` on 11 October 2026 and one run was
  dispatched on `main` (run 38144863693). Both jobs (python, javascript-typescript)
  FAILED at the step "Analyze and wait for Code Scanning ingestion" with
  `Resource not accessible by integration`; the code-scanning API reports that
  code scanning is not enabled for this repository. The cause is not established.
  Earlier runs remain SKIPPED in history. A failed or skipped CodeQL job is not a
  clean or triaged scan. Owner decision (14:11Z, 11 October 2026): the variable is
  set back to `false`; the job skips until the repository is public, is then
  re-enabled, and one completed run is triaged before the tag. Scheduled
  enablement, not a waiver.
- Signed attestation and second-platform reproduction: **deferred to the first
  stable release**. The beta wheel carries local provenance only
  (`dist/BUILD_PROVENANCE.json`, two-build hash comparison). The inactive
  attestation template and verifier are unchanged.
- Supported environments: the beta states today's tested environment exactly
  (Python 3.12; Alerta 9.1.0; PostgreSQL 18.3, pin 18.0; PgBouncer 1.24.1;
  Zabbix 7.0.10 lab target; Linux x86_64 reference in a Docker lab on a hosted
  runner; other platforms unvalidated). Native x64 qualification on an authorised
  host remains deferred and is disclosed in the release notes.

No requirement is waived silently; the deferral is explicit. No tag, upload,
release or deployment is authorized by this amendment.

## R25 — PostgreSQL reference pin moves to the latest 18.x minor (18.6)

Owner decision 11 October 2026: Zabalack and Voalack use the latest PostgreSQL 18.x minor
release. The reference pin moves from 18.0 (18.3 in practice) to 18.6: source
archive SHA-256 `555610c24d53e4316da5b7d3fc25c279d96856d5e0e23ee308c328c5fa881d9f`,
Docker image `postgres:18.6-bookworm` for the native and deployment labs,
`docs/compatibility_manifest.json` reference. 18.0 and 18.3 archives remain
accepted by `lab/build_postgres.sh` so historical evidence stays reproducible.
Minor releases keep the on-disk format and wire protocol; PgBouncer 1.24.1 and
Patroni bind to the major. This is a lab/pin change, not a runtime change, but it
changes the harness identity: the native and deployment results recorded on 18.3
stay bound to their revisions and are not rebound. New native and deployment runs
on 18.6 are required before the beta tag; the manifest's "observed" fields are
updated only from those runs. No tag, release or deployment is authorized here.
## R26 — Coexistence contract replaces the sole/last-plugin requirement

Owner decision 11 October 2026: Zabalack and Voalack must work alone and coexist inside one
Alerta. The signer previously refused to act unless it was the sole and final plugin
in the effective routed chain. It now requires that it appears exactly once and that
every plugin after it declares, as class attributes, `ACK_FOREIGN_SOURCE_PASSTHROUGH
= True` and `ACK_OWNED_SOURCE_SYSTEMS` (a non-empty frozenset of owned `source_system`
values) that does not contain this alert's source: the plugin then provably treats the
alert as foreign, returns it unchanged and raises nothing. The flag alone is not enough,
because a same-source owner could legitimately alter the signed text later in Alerta's
loop (independent review finding F1P1). A trailing plugin without both declarations,
or one owning the same source, still blocks signing
(`skipped_routing_order` / `zabalack_final_routing_invalid`); an instance-level
attribute is not trusted. Zabalack declares the same contract for itself (it already
returns early for alerts whose `source_system` is not `zabbix`). The attribute name is
intended to be standardised in alerta-ack-core; the shared install of the global
Alerta patches, the shared-core pin and the coexistence lab profile follow the beta
tag. Runtime change: a new candidate revision and run are required.

## R27 — Publication register: owner inputs recorded 11 October 2026

The owner recorded, in session: backup maintainer `steliosx`; private conduct
contact `stelios@steliosx.com`; `publication_authorized = true` (publication of the
beta tag and GitHub prerelease; distinct from deployment). The owner also made the
repository public and enabled GitHub private vulnerability reporting the same day.
Still unset or false: `private_security_reporting_verified` (owner test report
pending), `copyright_and_third_party_review_approved`,
`public_content_review_approved`, `pypi_namespace_confirmed`. The publication audit
therefore still returns 2 ("inputs required"); no tag may be created until all are
true. No tag, upload or deployment is authorized by this amendment.
## R28 — CodeQL first completed run and triage (11 October 2026)

After the repository became public, the CodeQL variable was set back to `true` and
run 38157736793 on `main` 1a498f51 completed both analyses with ingestion accepted.
Eight alerts, all in harness paths, are triaged in
`docs/release/CODEQL_TRIAGE_20261011.md`: seven accepted-by-design or false positive
(public evidence file modes required by R20; lab-only synthetic credential files;
non-sensitive report printing) and one low-severity item fixed by PR #46 (explicit
TLS 1.2 floor on the lab HTTPS server; the platform default was already TLS 1.2, so the
alert concerned reliance on that default, not accepted TLS 1.0/1.1 traffic). Alerts are dismissed in GitHub only after the owner and the
independent reviewer agree with the dispositions. This supersedes R24's "scheduled
enablement" wording: CodeQL is enabled and has one completed run; the beta tag waits
for the dispositions to be agreed. No tag, release or deployment is authorized here.

## R29 — 0.1.0b2: candidate identity and setuptools 84.1.0 build backend (28 October 2026)

The integrated main after PRs #45 to #60 (`d074665e482f1b3a85f114f1ed930142320990e2`)
receives version 0.1.0b2 on the owner's decision of 28 October 2026. The reason is
a toolchain change: the declared build backend moves from setuptools 83.0.0 to
84.1.0 to clear the last pip-audit advisory on the exact lock. Because
`tools/build_release.py` builds with `--no-isolation` and `tools/verify_release.py`
requires the exact `Generator` line, a backend change alters the wheel bytes, and
this repository treats such a change as a new artifact identity rather than a
rebuild under the published one.

| Package | Previous pin | Candidate pin | Reason |
|---|---|---|---|
| setuptools | 83.0.0 | 84.1.0 | Windows path escape via `namespace_packages.txt` during legacy wheel installation, GHSA-xmj3-9gf7-h52w (low; this project targets Linux) |
| brotli | 1.2.0 | 1.2.1 | Out-of-bounds write in the Brotli decoder on attacker-supplied input, GHSA-9v8h-qqgm-4m35 / CVE-2026-111497 (critical); landed in PR #60 |
| certifi | 2026.7.22 | 2026.10.19 | GHSA-6q48-6c7w-9gw2 (critical): the bundle carried emSign root CAs whose intermediate CA keys were compromised; landed in PR #60 |

The setuptools 84.1.0 wheel was downloaded from PyPI and verified against the
release digest (`5231689c6ac8156cd155cfb98c3fd6fb252c3e7e9bd3d78b64d3c55f132624c9`);
its metadata and URL replace the 83.0.0 entry in `tooling_reference_manifest.json`,
and it still contains no `pkg_resources`. Two throwaway builds of main
`d074665e` with a fixed `SOURCE_DATE_EPOCH`, one per backend, differ only in the
`WHEEL` `Generator` line and its `RECORD` hash (wheel SHA-256
`8433506fae7513da377e0091f21c3a248e903fda1e251193da252adbcd4af8ae` with 83.0.0,
`241ef9480e3127bc4fbe87de35d72ea93335b7447c73dcde09ded328c5a24051` with 84.1.0).

This version and toolchain update adds no runtime feature and changes no SQL
body. It deliberately changes source/harness identities; 0.1.0b1 and earlier
evidence stays historical and cannot satisfy the new builder by relabelling. The
GitHub prerelease `v0.1.0b1` on candidate #4 (`f85212e2`) stands unchanged. The
R24 policy decisions and the R28 CodeQL dispositions carry forward. Native x64
qualification, the 7.0.25 lab qualifications, installed-account permissions
(A4/G5), transport amendment A2 and deployment gates G1–G8 remain open. No tag,
upload, release or deployment is authorized by this amendment.

## R30 — 0.1.0b3: live-lease lifetime expiry guard (migration 013) and the nightly run (28 October 2026)

The scheduled run of `ci.yml` (`ACK_HYPOTHESIS_PROFILE=nightly`: unseeded, 75
sequences of 60 steps for the stateful property) had never passed: 27 failures
and 18 cancellations in the 60 recorded runs, a failure every night since 12
October 2026, always in
`tests/postgres/test_stateful_ledger.py::test_generated_delivery_ledger` (the
native PostgreSQL job on every sampled night, two of four focus parameters, the
counterexample retained only on the runner; the reference pglite phase failing
without a test name in its log). The "property-nightly 46/0/0" cited by the
readiness record is `tools/run_property_nightly.py`, which runs the nightly
profile with a fixed seed, and so did not cover this. Push runs use the seeded
`pr` profile (8 sequences of 20 steps) and pass.

Reproduced locally on 28 October 2026 on main `c49eecf9` (pglite, node 22.23.2,
Python 3.12.14, CI's six-file phase order, nightly unseeded): one deterministic
counterexample for the `completed_generation` focus, replaying from the saved
example database in about one second. Minimized: `explicit_retry` on the
claimed generation-2 action (`delivery_claimed` → `pending`, lease still live)
→ `begin_send` (`start_attempt` records the write; state stays `pending`,
attempts 1) → `execution_deadline_passes` → `reconcile_due` records
`expired`/`execution_lifetime_expired`, `uncertain=false` → `worker_stops`
changes nothing (the row is no longer in a state `worker_stopped` touches) →
`operator_settlement` expects `uncertain=true` and finds `false`;
`settle_unknown` would raise "action has no unknown outcome".

Cause: the lifetime-expiry statement of `reconcile_due` selected
`state IN ('authorised','pending','held') AND NOT uncertain` with no guard for a
live delivery lease or an unsettled write attempt. 009's own comment foresaw
that statement and relied on the abandoned-attempt fence running first, but the
fence reads attempts on an expired lease only. `operator_call('resume')`
carried the same predicate for held rows (T43 case 4). `expire_due_effects`
(007) already had both guards. Migration
`013_live_lease_expiry_guard.sql` is 012's `worker_call` body and 004's
`operator_call` body with those two guards added to the two statements, the
routine digest re-recorded, schema still 4, no row relabelled (an action already
recorded `expired` with an unsettled attempt on an earlier tree stays as it is;
see `docs/rollback.md`). Direct SQL regression T43
(`tests/postgres/assertions_live_lease_expiry.sql`, four cases: the sequence,
two ordinary-expiry controls, the pause/resume variant) fails on 012 at case 1
and passes on 013; `test_T43_forward_live_lease_expiry_migration_and_guards`
exercises 013's preconditions, resealing and grants from a 012 tree.

What is and is not claimed: the reproduced counterexample is fixed and the
saved example replays green on 013; local unseeded nightly runs of the pglite
phase on 013 are recorded in the PR; native PostgreSQL runs only in CI, and the
native job's two failing focuses were never identified, so the scheduled run
after this lands is the test of whether the nightly failure had one cause.

Identity: because 013 changes SQL bodies, the candidate takes version 0.1.0b3
(owner decision, 28 October 2026, after the choice to fix first rather than tag
0.1.0b2 with the defect documented). 0.1.0b2 was never tagged, uploaded or
released; its evidence stays bound to `c49eecf9`/`7d170231`. PR #63 (release
verifier accepting beta versions) is included in this identity. The GitHub
prerelease `v0.1.0b1` stands unchanged. R24, R28 and R29 carry forward. No tag,
upload, release or deployment is authorized by this amendment.

## R31 — 0.1.0b4: claim-path expiry fence (migration 014) and the first nightly on 0.1.0b3 (29 October 2026)

The first scheduled run of `ci.yml` on 0.1.0b3 (main `c80681a1`, run
40855680678, 29 October 2026) failed again, but narrower: the native
PostgreSQL postgres phase failed 1 of 208 (every earlier sampled night failed
2), again `test_generated_delivery_ledger`, focus not exposed by the native
summary; the reference pglite phase also exited 1. That 013 removed one of the
two native failures is an inference from the counts only: the native focuses
and counterexamples were never exposed, before or after 013.

Local nightly-profile passes on main's exact tree (pglite, unseeded; run one at
a time under the owner's thermal limit) found a failing sequence on the
tenth pass, for the `settled_retry` and `expired_generation` focuses, same
assertion as R30 (settlement expects `uncertain=true`, finds `false`): the send
is recorded under a live lease → the execution deadline passes (013 correctly
leaves the action unexpired) → the lease expires → another worker's claim
arrives → the operator cannot settle. That pass ran four times slower than
usual under CPU throttling and one replay passed, so the classification rests
on a direct SQL probe instead: new action → claim → `start_attempt` → deadline
and lease set one second past → a second claim records
`expired`/`execution_lifetime_expired`/`uncertain=false`, deterministically.

Cause: `claim_delivery` ran its lifetime-expiry branch
(`execute_before<=now_at … AND NOT a.uncertain` → `expired`, return) before
its own abandoned-attempt fence. A plausible reading, not established, is that
before 013 `reconcile_due` usually expired such a row first, so 013 moved the
case onto the claim. Whether this local sequence is the failure the scheduled
native run hit is unknown; the next scheduled run is the test. Migration `014_claim_expiry_fence.sql` is 013's `worker_call` body with
the lease lookup, live-lease return and abandoned-attempt fence moved, unchanged,
to just before the expiry branch, followed by one new line: a claim whose action
is past its deadline returns without a lease once the fence has made it
uncertain, so an expired action is still never handed a new lease (the
generated model asserts exactly that; the first draft of 014 without this line
failed it). The maintenance-hold and cohort-policy branches keep their place
(T42 case 3 still records `held` first).
`operator_call` is not touched. Routine digest re-recorded, schema still 4, no
row relabelled. Direct SQL regression T44
(`tests/postgres/assertions_claim_expiry_fence.sql`: the probe sequence, the
same after an operator retry, and an ordinary-expiry control) fails on 013 at
case 1 and passes on 014; `test_T44_forward_claim_expiry_fence_migration_and_guards`
exercises 014's preconditions, resealing, grants and the new order from a 013
tree.

What is and is not claimed: the reproduced sequence is fixed; local results
are recorded in the PR; native PostgreSQL runs only in CI, so the scheduled run
after this lands is again the test. Not changed here, noted for review: the
cohort-policy branch of `claim_delivery` (`failed`/`cohort_policy_changed`)
also returns before the fence; it records a failure rather than an expiry and
was not reached by any generated sequence.

Identity: because 014 changes a SQL body, the candidate takes version 0.1.0b4
(owner decision, 29 October 2026). 0.1.0b3 was never tagged, uploaded or
released; its evidence stays bound to `c80681a1`. The GitHub prerelease
`v0.1.0b1` stands unchanged. R24, R28, R29 and R30 carry forward. No tag,
upload, release or deployment is authorized by this amendment.
