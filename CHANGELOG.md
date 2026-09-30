# Zab-al-ack changelog

## 0.1.0b1 — 2026-09-27 (integrated local beta candidate; unpublished)

- Integrates reviewed retention `9cb38de` with reviewed S2 `842e88d` once. The R8 x64 host run exercised the
  S2 lineage before this merge and the historical `0.1.0a18` wheel; it is preparation evidence, not a test of
  this combined source or a frozen `0.1.0b1` artifact.
- Sets the package and local release policy to `0.1.0b1`. Final-head checks, a built wheel, and
  exact-artifact host qualification remain separate evidence to produce for this candidate.
- The two sections below preserve their original, separate-lineage change histories. The S2 heading describes
  its pre-integration review state.
- Corrections on 29 September 2026. Each has focused regression tests. The local release checks and the
  exact-wheel lab run have been recorded on the tested source `2a4ce07`; canonical build verification is still
  to be recorded.
  - **ZB-B1-REL-01:** `MANIFEST.in` includes the lab-trust installer files and the ingestion template, so the
    sdist matches the source archive.
  - **Daybreak DB-1:** once an intent is staged, a failure while binding the signed marker refuses the
    acknowledgement under either `PLUGINS_RAISE_ON_ERROR` setting. It is never saved without its proof.
  - **Daybreak DB-3:** `response_limit_bytes` must be an integer from 65536 to 8388608, in both the
    validation and the schema.
  - **Evidence history (Daybreak DB-2/DB-4):** the a16 evidence is preserved byte for byte under
    `evidence/history/0.1.0a16/`. The current records are those of `2a4ce07`, adopted together with the
    evidence index, the compatibility manifest and the version narrative; the records they replaced, adopted for
    `34ea703`, are preserved byte for byte under `evidence/history/0.1.0b1+34ea703/`. Fresh local security scans
    (pip-audit, Gitleaks), npm audit, the SBOM and its schema validation replace their earlier records. Three legacy a16 records (wheel
    smoke, collection check, local validation) are kept only in the history, and are NOT_RUN for `0.1.0b1`.
  - **Deferred, Daybreak DB-5:** the unfenced `record_drift` operation is documented, with its release impact,
    in `docs/BETA_READINESS.md`.
  - **Hosted evidence history:** `tools/collect_ci_evidence.py` keeps `evidence/history/` byte for byte through
    producer preparation and candidate collection, failed collections included. It validates every version against
    its `MANIFEST.json` before removing anything, refuses links, special files and unlisted or altered files, and
    removes stale current reports without following links. Synthetic regressions are in
    `tests/release/test_ci_collection.py` and `test_release_provenance.py`.
  - **PyJWT advisory (R30):** `requirements.lock` moves PyJWT from 2.13.0 to 2.14.0 to fix GHSA-w6j9-cwv2-h6wq, an
    availability defect in JWK Set parsing. PyJWT comes in through `alerta-server` in the development and lab-runner
    environment; the zabalack runtime lock and application code are unchanged.
  - **Retained gate scratch (R31):** every pytest invocation of the local gate runners gets its own never-rotated
    basetemp, copied with a full inventory into the private raw directory. The mutation runner keeps the baseline
    tree and each variant's executed bytes before the child runs, and the secret scan keeps its scanned copy.

## Unreleased — S2: webhook transport candidate for A2 (Opus; separate, not integrated)

- Corrections after Astra's R7 review of `6f083b5`:
  - **B13:** the root validation driver adopts only an owner record bound during its own lab start. A stale
    checkout record is never adopted, so a failed audit or busy preflight makes no root stop.
  - **B14:** a failed lab stop or reset fails the S2 acceptance attempt (exit 1, named in the summary), and a
    failed stop is never followed by a wipe.
- Corrections after the independent R6 review of `97925db`:
  - **Evidence:** a collection is COLLECTED only when every selected, existing file was copied and read back.
    Otherwise it is INCOMPLETE and lists each failure: copy or destination errors, links anywhere on the path,
    files resolving outside their source, or an existing copy with different bytes. Such a collection
    suppresses the lab wipe and fails the run. Attempts get fresh directories, and copies are never
    overwritten. The harness records setup refusals, including `isolate()`, and stops its own gateways before
    collecting.
  - **Rollback:** `uninstall.sh` reads the unit cgroup's `cgroup.events` and `cgroup.procs` instead of a file
    size, which is always 0 on cgroupfs. It refuses a populated or unreadable group and accepts a removed one.
  - **Audit:** `check-ca.sh` verifies the CA's self-signature with `-check_ss_sig`.
  - **Ownership:** a foreign or unobservable installed unit is refused before a fresh checkout can read as
    absent, so nothing starts. The root validation driver requires the unit idle before the attempt, and
    stops only the invocations it created.

- The reference webhook constructs `HttpRequest` with `SSLVerifyPeer` and
  `SSLVerifyHost` enabled (Zabbix 7.0.28+ in the 7.0 branch; 7.0.31 qualified).
- `tools/webhook_transport_check.py`: per-node enablement precondition
  (qualified executor build, reviewed script, verified trust anchor);
  refuses otherwise.
- `deploy/ingress/alerta-ingest.nginx.conf.template`: ingestion-only route
  that refuses upstream redirects and emits none; a review candidate.
- `lab/webhook_transport_acceptance.py`: acceptance through the packaged
  zabbix_server in an isolated trust namespace; 41 of 41 cases on the x64
  lab. A2, G1 and G2 stay open. Integration needs a decision on the
  deployment lab's daemon trust (`docs/webhook-transport.md`).
- Checker: every refusal is structured (an executor that does not exit 0,
  an invalid port, a malformed export, an unreadable input).
- Lab trust (`docs/lab-trust.md`): a fixed root-owned unit runs the packaged
  zabbix_server, dropped to the lab account with no supplementary groups or
  capabilities, with only a name-constrained lab CA as its trust; the lab
  signs its certificate with that CA and refuses before any service without
  it. Prepared and validated without installing; installation is the
  owner's decision. The real-engine acceptance adds the response contract
  and the route's upstream TLS (47 of 47).
- R4-CHECKER (R5 review): 443 applies only when the URL names no port; an
  explicit port 0 (or any port outside 1–65535) is REFUSED/3 before any TLS
  context or connection.
- R4-T7 (R5 review): T7 reads at most one byte over the webhook's 1 MiB
  limit and fails a body over it, a non-object (`[]`, `null`, a string) or
  invalid JSON, instead of passing or raising. Tested on T7 itself with the
  exact 1,048,577-byte R4 body.
- R5-UNIT-OWNERSHIP (R5 review): the lab no longer stops the global
  lab-trust unit whenever it is installed. It binds its own invocation
  (InvocationID, control group, main PID and start time) when its start
  returns. It refuses a foreign or unobservable active, activating or
  deactivating unit (state `ambiguous`) without writing the configuration
  or starting, signalling or stopping anything. It stops only its own
  invocation, including descendants after the main process exits.
- Installed validation fails closed (R5 review):
  - `lab_trust_validation.py` records `verify.sh`'s exit code, stdout and stderr, and refuses before copying
    or starting anything when it fails or the snapshot is mutable;
  - prerequisites, cleanup and the host comparison are graded cases;
  - PASS needs every required case exactly once and no exception.
- The installation audit is exact:
  - `check-ca.sh` checks the CA's constraints, path length, key usage and extension set, the exact trust and
    CA entries, the hash-link target, copy equality and the key;
  - `verify.sh` runs as root;
  - `install.sh` installs only from a root-owned snapshot whose `SHA256SUMS` has the approved digest;
  - `uninstall.sh` removes nothing until the unit is inactive with an empty control group.
- Acceptance after installation (R5 review):
  - the S2 harness launches `zabbix_server` directly in its own trust namespace (`ZABALACK_LAB_ZABBIX_LAUNCH=direct`)
    and refuses while the installed unit is up;
  - the deployment cycle refuses the override;
  - installed validation requires the unit launch.
  - New engine cases (pending a root run): `[]` and `null` responses, and route-upstream missing-anchor and
    expired certificates. That makes 51 graded cases plus the 15 ungraded baselines.
- Evidence collection (R5 review): `tools/collect_attempt_evidence.py` puts every available record,
  diagnostic, retained manifest and service log of an attempt into a PRIVATE bundle (0700/0600,
  per-file digests, absent files recorded, secret-like names listed only). Both root drivers
  collect after stopping and before wiping, for every outcome, including a refusal.

## Unreleased — follow-ups to the R3 review (Opus; on S1 `cea3412`)

- F2: Astra's exact CR_strong host bytes are a permanent reader case.
- F1: the cancellation cutoff, the commit and the publication are named
  separately. A signal held during the commit is reported as "during the
  final commit, after the cancellation cutoff" (exit 130; the committed
  record stands), no longer as "after the final record was published"; a
  failed replace publishes nothing, leaves no temporary record and exits 1
  without a traceback. The documentation no longer claims that a death after
  the cutoff leaves a published record: before the replace completes it
  leaves `PENDING_BINDING`.
- S1: the runner's record is retained under its own label as soon as the run
  ends, before cleanup, on success, failed cleanup and interruption alike;
  a cycle whose runner record could not be retained never ends PASS.
- R3-RETENTION: when that copy cannot be made, the runner's record is left
  byte for byte as the canonical record, nothing is wiped, and the outcome
  goes to a separate terminal diagnostic
  (`evidence/deployment-e2e-run.retention-failure.json`, NOT_A_RESULT)
  bound to this run and to the record's digest; it is removed when the next
  run starts.
- R3-RETENTION retry (R5 review): a later command no longer invalidates an
  unretained original. It first retains the original and its diagnostic
  (verified by digest, bound as `native_lab.previous_attempt`), or refuses
  with both unchanged and no start, runner, stop or wipe. This also covers
  an original whose diagnostic write failed. OSError while retaining (a
  wrong-type retention parent) now takes the protected path (exit 5,
  original intact, lab stopped, not wiped) instead of escaping as exit 1.

## Unreleased — corrections after Astra's review of `e8ef82b` (Opus)

Layered on the reviewed `e8ef82b`, which is not rewritten; each correction
carries a regression that fails with its fix removed. Author verification
only, not independent review or approval.

- F1 (HIGH): the cycle publishes its final record once, at an explicit
  boundary. A SIGTERM, SIGHUP or SIGINT received before the last check (run
  with those signals blocked up to the atomic replace) is an interrupted
  FAIL with exit 130, even after a passing run and complete cleanup;
  previously a signal during the final validation still published a
  validating PASS and the CLI died with a traceback (exit 1). A signal
  after the publication leaves the record as published and exits 130 with
  a message; a death before it leaves the runner's non-validating
  `PENDING_BINDING`. Pinned by real signals at nine boundaries
  (`tests/release/native_lab_signal_harness.py`).
- F2: `pg_hba.conf` is read as raw bytes and split at `\n` only (a bare CR
  is a blank, as in PostgreSQL); a non-UTF-8 file and a record whose options
  are not `name=value` are refused. Ten raw-byte cases pin PostgreSQL 18.6's
  verdicts.
- F3: `LD_LIBRARY_PATH` names the interpreter's lib directory only for its
  own `libpythonX.Y.so.1.0` file; another version, the stable-ABI library,
  a non-file, a dangling link and a path with `:`, `;` or `$` give nothing.
  Both copies are tested, and the four callers import `_ctypes` on the x64
  host's setup-python interpreter.

## Unreleased — corrections after the non-author review of `b57bebb` (Opus)

Five corrective commits (O17–O21) and two test commits (O22 with its lint
follow-up, O19b) layered on the reviewed `b57bebb`, which is not rewritten; each
correction carries a regression that fails with its fix removed. Author
verification only, not independent review or approval.

- O17 (HIGH): inside a cycle the runner validates its execution evidence and
  writes `PENDING_BINDING` (`provisional_status: PASS`, `native_lab.pending`),
  never PASS; only the cycle, after an exit-0 run and a complete cleanup that
  left the lab absent, sets PASS and validates the final bound record
  (`final_evidence_validation_failed` and a non-zero exit otherwise). A
  pending record, or a plain PASS from a bound runner, never validates.
- O22: the lifecycle is pinned through the cycle command itself: `cycle`
  runs the real runner `main` with the binding and its real final
  validation, with only host effects replaced.
- O18: `pg_hba.conf` blanks are PostgreSQL's (space, TAB, CR) and records end
  at `\n` only, matching PostgreSQL 18's `pg_hba_file_rules`.
- O19: the only startup hook the runner check accepts is setuptools'
  `distutils-precedence.pth` with the exact bytes of the pinned setuptools
  83.0.0 wheel; a `.pth` a pinned distribution's consistent RECORD owns is
  otherwise refused (O19b keeps the reviewer's appended-RECORD reproduction). The record states what is anchored
  (`runner_environment`, `dependency_verification: record_consistency_only`).
- O20: the Zabbix lab stages the packaged frontend without the package's
  dangling `conf/zabbix.conf.php` link, which failed the first real cycle.
- O21: the runner interpreter starts with its own `libpython` in the
  deployment path's minimal environments (`LD_LIBRARY_PATH` derived from the
  interpreter's prefix); the setup-python interpreter otherwise loaded the
  system libpython and `_ctypes` failed to import.

## Unreleased — integration corrections after the re-review of `19d0fc7` (Opus)

Sixteen corrective commits on Fable's corrected delivery `19d0fc7` (O1–O15
and O13b), each with a regression that fails with its fix removed; no
delivered commit is rewritten. Author verification and same-session
technical review only, not independent review or approval.

- Held packages: `tools/native_local.py` and the identity collector accept
  `hold ok installed` (the qualification host holds its exact versions) and
  refuse a package selected for removal.
- Runner-account checker: systemd's real `Listen=PATH (Type)` format is
  read, so an active `docker.socket` is recognised and never connected to;
  an unreadable `Listen` value or an unlocated runner `.env` is
  NOT_OBSERVED; a non-root run cannot conform (sudo enumeration).
- Deployment evidence: any unexpected error during or after the cycle is
  recorded (FAIL, or NOT_RUN before anything started); the inner runner's
  record stays `native_lab.pending` until the cycle binds its cleanup
  outcome (as first delivered, the runner then self-validated that pending
  record and every real cycle ended FAIL; corrected by O17: the runner writes
  `PENDING_BINDING` and the cycle sets and validates the final PASS); PostgreSQL is stopped even when the Zabbix lab's stop fails, and
  a failed or unreadable Zabbix stop is never a clean stop.
- Workflows: the deployment job replaces the tracked historical record with
  NOT_RUN before any step that can fail; the lab steps run as
  `exec flock -n -F <lock> python …` so a job cancellation reaches Python;
  the candidate run must be a completed, successful `ci.yml` run of the
  revision by `workflow_dispatch`, `push` or `schedule` (explicit checks, not
  `assert`).
- Installed-environment verification: the runner probe runs as
  `python -I -S -B` and finds the venv's site-packages with the `venv`
  scheme (Debian's default scheme named a non-existent `local/…/dist-packages`
  under `-S`); only `requirements.lock` distributions (plus pip) with RECORD
  hashes matching their files; no site customisation; a `.pth` only when a
  verified RECORD owns it; no unowned top-level entry; no bytecode in the
  package (installed with `--no-compile`; the lab never writes it).
- Identity collector: unattributable `apt-cache policy` lines are
  NOT_OBSERVED; each origin host has pinned repositories and suites; gpgv
  signatures are read per signature block with the primary fingerprint and
  matched to origins by repository and suite (never by URI alone; duplicate
  observations are missing); index digests come only from the content
  `gpgv --output` verified, whose digest must match the pinned signature of
  the same repository and suite, and an InRelease with content outside its
  signed message is refused; cached `.deb` files are found under apt's
  epoch-encoded names; `approve` refuses to write over a policy file;
  `postgresql-client-18` is verified as a component of its own.
- Cancellation: the process-group escalation keeps one fixed deadline,
  completes despite further signals, keeps the first interrupt as the cause,
  kills members that outlive the leader, and never reaps the leader before
  the final SIGKILL (its exit is observed with `waitid(WNOWAIT)`), so the
  group id cannot be reused; an already-reaped leader's group is never
  signalled.
- Evidence bundles accept only the canonical spelling of a path; the Zabbix
  lab reads `pg_hba.conf` with PostgreSQL's quoting rules and requires one
  unquoted method keyword per record.

## Unreleased — corrections from the non-author review of the Docker-removal delivery (R29 corrections)

Eight corrective commits in seven correction groups, and two documentation
commits, on the delivered `32c8268`, each correction with a regression that
fails with its fix removed; no delivered commit is rewritten. (First
recorded as "seven corrective commits"; corrected during integration.)

- Deployment evidence (R1, R13): `lab/deployment_native_lab.py` writes a
  fresh NOT_RUN record before any service work, rewrites it on every refusal
  with a public reason code, binds it to the cycle outcome afterwards (a
  failed stop, retention or wipe, or a lab left behind, turns a PASS into
  FAIL whose reason names the cleanup step: `lab_retention_failed`,
  `lab_wipe_failed`, `lab_cleanup_refused` or `lab_cleanup_incomplete: final
  state <state>`; an interruption is FAIL), and
  `tools/deployment_evidence.py` accepts a native-lab record only when the
  lab ended absent with the run at exit 0. `tools/run_deployment_e2e_local.py`
  sets `artifact_mode` and the supplied-wheel receipt only after the runner
  check; a NOT_RUN record carries neither. The tracked
  `evidence/deployment-e2e-run.json` can no longer be uploaded as a stale
  PASS.
- Runner-account checker (R2, R3, R6, R15, R16):
  `tools/runner_account_check.py` observes the exact transient unit named by
  `--unit` (no glob) through `systemctl show`, takes its processes from the
  unit's cgroup, compares every uid and gid field, groups, capabilities and
  `NoNewPrivs` of every candidate (ambiguity is INCOMPLETE), resolves socket
  symlinks, classifies a connect as `ok`, `errno:N` or `unknown`, and skips
  the demonstrated connect on a socket-activated unit instead of starting the
  daemon; docker-free rules for an inferred write path. Record schema 3.
- Package identity collector and validator (R4, R5, R7, R18, R19):
  `tools/collect_package_identities.py` derives its missing-observation list
  from the record, pins every origin (Zabbix, PGDG, Ubuntu archive) by full
  key fingerprint, treats a failed or erroring `dpkg -V` as a payload
  deviation, accepts `Architecture: all` only for the frontend and SQL
  scripts, verifies the PostgreSQL client binaries as members of their
  packages, and binds each package to the signed index (InRelease digest of
  the Packages index, stanza SHA256 and filename, cached `.deb` digest);
  `lab/zabbix_reference.json` carries the corrected repository URL, the
  Zabbix and PGDG fingerprints, the reviewer's index observation of
  2026-09-23 and retires the 7.0.30 fallback (historical note kept) now that
  7.0.31 is confirmed in the noble index.
- Cancellation (R8, R9, R20): `tools/cancellation.py` routes SIGTERM, SIGHUP
  and SIGINT into one `Cancelled` interrupt raised at a safe point, defers a
  further signal during cleanup and the record write, and stops a child's
  whole process group on cancellation or timeout; `tools/native_local.py`,
  `lab/deployment_native_lab.py` and `tools/run_deployment_e2e_local.py` use
  it (whole-run guard, per-phase cleanup, PgBouncer's group, exit 130).
- Zabbix lab (R12, R14): `lab/zabbix_native_lab.py` initialises its cluster
  with `scram-sha-256` logins only from generated passwords kept in the
  marked lab directory at mode 0600, refuses a cluster whose `pg_hba.conf`
  allows trust, and signals a component PID only when the live process
  matches the identity recorded at start (`/proc` comm, start time, uid and
  the lab's own arguments); a reused or unrecorded PID is `foreign_pid`,
  reported and never signalled; no process group is signalled; the PHP
  server starts directly as the service identity.
- Recorder and bundle (R17, R21): `tools/record_real_zabbix.py` requires the
  JUnit version property and exactly the pinned version with or without a
  services record; `lab/real_zabbix_prepare.py` reads `apiinfo.version`
  before logging in and refuses a non-pin instance;
  `tools/evidence_bundle.py verify` requires every archive member under the
  one root holding the manifest, parses the archive manifest with the on-disk
  rules (no duplicates), refuses duplicate members and symlinked directories.
- Exact candidate wheel (R10, R11): `deployment-e2e.yml` consumes the
  candidate run's already-produced wheel, bound by dispatch inputs (run ID,
  revision, SHA-256), confirmed through the Actions API and verified with
  `sha256sum -c` before installation; nothing is built in the job; gate,
  lock, upload and artifact naming unchanged. `tools/supplied_wheel.py`
  `check_installed` also checks the dist-info inventory, other entries named
  after the project and the ownership of every `.pth` or site customisation
  hook, and `tools/run_deployment_e2e_local.py` runs it inside the runner
  interpreter with the supplied digest.
- Housekeeping: an unused import left by the cancellation change in
  `lab/deployment_native_lab.py` and `tools/run_deployment_e2e_local.py` is
  removed so `tools/run_quality.py ruff-infrastructure` passes.

## Unreleased — native deployment path and Docker-free deployment job (R29)

- `lab/deployment_native_lab.py`: disposable PostgreSQL 18 cluster (loopback
  5432, scram host logins, `alerta`) and the R27 Zabbix instance on 8080 for
  `tools/run_deployment_e2e_local.py`; state classification and refusals,
  verified host layout, runner environment from the wheel only, reset,
  verified stop, wipe, and `cycle` (start, run, stop, wipe with cleanup after
  every outcome).
- `tools/run_deployment_e2e_local.py --wheel-sha256`: the supplied-wheel
  receipt in the local form; the unchanged `tools/deployment_evidence.py`
  already accepts it on a `not_docker` record, now pinned by a test.
- `tools/lab_retention.py`: an unreadable diagnostic is a `RetentionError`
  (the caller refuses its destructive step) instead of an uncaught
  `PermissionError`; found by running the suite as an unprivileged account.
- `tools/native_local.py`: an interrupted phase (SIGINT during pytest) is
  recorded with reason `interrupted`, cleaned up and retained like any
  failure, and the run record ends as FAIL (exit 130) instead of staying at
  RUNNING with the phase missing.
- `deployment-e2e.yml` (proposed, unchanged trigger, gate and artifact):
  prepare the runner environment from the wheel, `cycle` under the shared
  lab lock on installed services; no Docker. (Originally the job built the
  wheel itself; the corrections above make it consume the exact candidate
  wheel instead.)
- `tools/self_hosted_job.py`: the live-service guard covers the deployment and
  Zabbix lab PID files. `Makefile` gains `deployment-e2e-native`.
- `lab/zabbix_native_lab.py`: `zabbix_server` and the PHP server start with a
  minimal environment (no inherited proxy settings); a schema without its
  initial data is refused at load with the correct inputs named.
- `tools/collect_package_identities.py`: read-only collection of the native
  labs' package identities on a host (dpkg, apt origin, InRelease signature,
  dpkg -V, version line), validation against the pin and the policy
  (CONFORMS / DEVIATES / INCOMPLETE) and a reviewed-identity proposal written
  only from a CONFORMS record; the policy files stay unchanged and their
  reviewed lists empty. `lab/zabbix_reference.json` records the official
  Ubuntu archive observation for php8.3 and the sources still unreachable.
- `tools/run_quality.py`: `ruff-infrastructure`, a bounded lint of the listed
  Docker-free lab and runner files.
- Author verification on a synthetic host only (one passing native deployment
  cycle: 4/4 cases, supplied-wheel receipt, cleanup and retention complete);
  no hosted run of the native deployment form is recorded.

## Unreleased — one exact native Zabbix 7.0 reference (R27)

- `lab/zabbix_reference.json` pins Zabbix 7.0.31 with verified source facts,
  the Ubuntu 24.04 package verification gate and a fallback rule (retired in
  the corrections above once 7.0.31 was confirmed in the noble index); every
  producer, validator and mocked test reads the pin instead of a `7.0.10` literal.
- `lab/zabbix_native_lab.py`: disposable package-installed Zabbix instance
  without Docker (own PostgreSQL 18 cluster, loopback-only server, packaged
  frontend on the fixed lab API) with state classification, refusals, verified
  stop, fresh-database reset and a component identity record;
  `tools/record_real_zabbix.py --services-file` records it;
  `lab/real_zabbix_prepare.py` provisions the synthetic contract fixture.
- Historical 7.0.0/7.0.10 records are untouched. Author verification only; no
  packaged run at the pinned version.

## Unreleased — Docker requirements inventory and runner-account verification (R28)

- `docs/docker_requirements_inventory.md`: required / optional / historical
  classification of every Docker dependency and what R26 changes.
- `tools/runner_account_check.py`: read-only host capability report for the
  runner account and its running process (configured versus running identity,
  capabilities, sudo, socket mode/ACL with inferred and demonstrated access,
  effective unit configuration including drop-ins and environment files, the
  account's own Docker configuration read as files, Docker-requiring
  workflows) with `docker-allowed` and `docker-free` policies; a missing
  required observation is INCOMPLETE, never CONFORMS.
- Corrections: the local-runtime blocked-environment message names the local
  form; the Makefile gains the `deployment-e2e-local` rule; the deployment
  validator and release-provenance scope statements agree with R14.

## Unreleased — routine native CI without Docker (R26)

- The routine Actions `native` job runs `tools/native_local.py` on the runner's
  installed PostgreSQL 18 / PgBouncer under the same shared lab lock; the Compose
  form and its network retention are no longer part of routine CI.
- `lab/local_runtime.py state` classifies the lab; the local runner refuses to
  reset unmarked, ambiguous or failed-to-stop labs, stops and re-inspects a
  running one, and cleans up PgBouncer and PostgreSQL independently with both
  outcomes recorded next to the phase's original failure.
- `tools/lab_retention.py` preserves phase diagnostics before every destructive
  reset in both runners; a preservation failure refuses the reset. The job hook
  refuses to archive or reset live or ambiguously owned lab data.
- PgBouncer must be 1.24 or newer; `lab/native_service_identities.json` records
  reviewed service identities, which evidence collection now requires for a
  `not_docker` native record. Author verification only; no x64 execution.

## 0.1.0a18 — 2026-09-22 (alpha under qualification toward beta, unpublished)

- Routine Actions execution moves from the offline Linux ARM64 runner to a
  dedicated native x64 runner (R25); the hook accepts only Linux/X64 and the
  security job verifies the official Gitleaks 8.24.3 x64 archive. Earlier ARM64
  results remain ARM64 evidence.

- Replacement candidate identity for the owner-selected combination on `a3317dc`:
  the wheel build-tool pin correction (PR #26), the pin-consistency check,
  forward migration 013, four corrections from the a17 application-security
  review (`health.py`, `config.py`, `plugins.py`, `query_guard.py`), the logging
  redaction and access-log route tests, and the populated delivery-ledger upgrade
  coverage (PR #28). The a17 wheel `e5c9e212…` and every historical run keep their
  original identities; none of them is evidence for this version.

- Access-log routes documented and tested: Alerta's own after-request line
  formats the request path and never a query string, the development server's
  request line is held at WARNING, and a WSGI-server or reverse-proxy access log
  needs a path-only format at that originating logger; no log message is rewritten.
- The mandatory adapter's log redactor blanks Alerta's `ContextFilter` extras
  `data` and `reqargs` and drops the query string from `url` on every record, so
  `LOG_FORMAT=json` emits neither the operator's note nor a reader search's staged
  intent value; the request path is kept. Found by the a17 application-security
  review (body and arguments) and its cross-review (query string).
- Forward migration 013 closes two NULL gaps in `worker_call` found by an
  independent SQL review of a17: `record_reconciliation`, `record_host_check`
  and `record_host_inventory` now require `worker_id`, an instance holding no
  background lease refuses them instead of comparing a NULL owner to a NULL
  caller, and a reconciliation `ok` with no `completion_evidence` key is refused
  like one with the wrong evidence. 012's body is otherwise unchanged; the
  routine digest is re-recorded and explicit resealing remains required.
- The reference job fails when a `pyproject.toml` pin is not repeated in the
  locks or a workflow or release-template action commit is not the inspected
  one; `pip check` never saw that drift.

## 0.1.0a17 — 2026-09-18 (qualification candidate, unpublished)

- Give the integrated recovery correction an explicit new artifact identity;
  preserve the previously qualified a16 wheel and every historical run unchanged.
- Retain uncertain writes after ambiguous Zabbix API errors, suspend delivery,
  allow read-only reconciliation and require forward migration 010's capability.
  Earlier migrations, grants, explicit resealing and dependency pins remain fixed.
- Include the integrated reconciliation, diagnostics, private evidence retention,
  isolated network lifecycle, Actions provenance and supplied-wheel qualification
  changes documented in their reviewed PRs. No new product feature is added here.
- Capture private WASM socket diagnostics and execute the missing forward-migration
  WASM regression. The earlier intermittent connection-refusal cause is unresolved.
- Required checks and affected installed-wheel/cold-source qualification must bind
  to a17 inputs. The successful PR25 a16-labelled CI snapshot is historical; its
  reports are not rewritten into a17 passes. See amendment R22 and the beta checklist.

## 0.1.0a16 — 2026-09-13

Sixteenth alpha candidate, prepared locally and not published. Base: a15
(`14b8c72f…`). It reproduces and adopts the independent review of a15, which
found no runtime or SQL defect and proposed no product change. Runtime Python
differs from a15 only in `__version__`; SQL, the media artifact and dependency
pins are unchanged, and there is no new migration.

### Verified about the review

- Its 46 checksum entries verify and the a15 packet it retains is byte-identical
  to the delivered one (`cf397f91…`), so it reviewed the release itself.
- Its independent execution matches this project's: reference 855/23/0, 8 SQL
  groups, 15 webhook groups, mutation 12/12 and 20/20, property 46, quality and
  quickstart, with the wheel, normalised sdist and source ZIP rebuilt to the
  supplied bytes. It inspected rather than re-ran the three service records.
- Its Gitleaks 8.24.3 result (zero findings over the source and the history
  through the a15 commit) is retained as the review's evidence;
  `evidence/security-scan.json` stays NOT_RUN for its own environment.

### Added

- `tests/release/test_evidence_modes.py`, the review's ten writer regressions:
  both public writers (`run_mutation_checks._atomic_json`,
  `record_real_zabbix.write_record`) and both private writers (the same one and
  `review_runs.atomic_json`) under a permissive (0o000) and a restrictive
  (0o077) umask, plus replacement of an existing 0600 file and an explicit
  publish through the private-by-default review writer. Against an unmodified
  a14 checkout the four public cases fail on the mode itself and the other six
  fail because a14's writers took no `mode` parameter; all ten pass here.
- Amendment R21 and `verification/archive-modes.json`, the measured stored mode
  of the published records in every delivered handoff.
- An accurate NOT_RUN reason in `docs/gitleaks_reference_manifest.json`. The
  build was attempted here: the pinned commit clones and a Go 1.24.7 toolchain
  is present, but three module hosts are outside this environment's egress
  allowlist and substituting mirrors would stop the binary being the pinned
  upstream build. The old reason ("Go toolchain unavailable") no longer held.

### Corrected

- **R20's history.** "Shipped in every handoff since a7" described the writer,
  not the handoffs. Measured from the archives: a3/a4/a5/a8 0644, a9 0644, a10
  **0600**, a11 0644, a12 **0600**, a13 **0600**, a14 0644, a15 0644. The writer
  was wrong throughout — a fresh a14 run still produced 0600 — but only packets
  assembled with mode-preserving copies carried it to a consumer.
- **The profile mutation records are optional when absent and validated when
  present**, not required. Removing both from a disposable copy produces an
  empty `evidence_binding_findings` list. The rule is unchanged; the description
  of it was wrong.
- **Service runs belong to the candidate that executed them.** Unchanged
  service-path code supports an inference about an earlier candidate; it does
  not convert its NOT_RUN records into executions.
- **The release verifier is not stdlib-only**: `tools/verify_release.py` reaches
  PyYAML through `tools/agent_guidance.py`. The handoff instructions say so.
- **Two owner-only records do remain in the handoff**, in
  `reviews/0.1.0a7/plugin/evidence-a7/`, retained as received and the only two
  of that directory's twenty-one records below 0644. One is a **NOT_RUN**
  real-Zabbix record, which disposes of a15's claim that the second writer
  instance was invisible until a candidate could run the suite: the NOT_RUN
  branch uses the same writer and the mode was already in a shipped artifact.
  `verification/handoff-checks.json` had recorded both accurately in a15; only
  the prose said there were none.
- The `Still owner-only` changelog headings are renamed `Still pending`
  throughout, so the phrase cannot be read as a file mode.

### Executed for this candidate (all bound to this tree)

- Reference 865 passed, 23 skipped, 0 failed; 8 SQL groups; 15 webhook groups;
  quality PASS; mutation PR 12/12 and nightly 20/20; property nightly 46;
  quickstart; documentation capture.
- Native PostgreSQL 18.3 + PgBouncer 1.24.1 **42 passed**; real Zabbix 7.0.10
  through the recorder **6 passed**; deployment-path lab from the installed a16
  wheel **4 passed, 13/13 observations**.
- pip-audit, npm audit, export audit, wheel smoke, SBOM schema, release
  verifier; artifacts reproduced byte-identically across two builds.

### Still pending

- A2 transport remediation; the Docker Compose forms of both labs; hosted
  CI/CodeQL/Gitleaks; attestation issuance; an Alerta Web UI capture; G5's
  effective-role evidence on the approved installed account; licence review;
  external security review; publication inputs; G1–G8.


## 0.1.0a15 — 2026-09-13

Fifteenth alpha candidate, prepared locally and not published. Base: a14
(`c2b7fb58…`). Runtime Python differs only in `__version__`; SQL, the media
artifact and dependency pins are unchanged, and there is no new migration.

### Verified about a14

- Checksums, the retained byte-identical a13 archive, the bundle lineage, the
  artifacts, the release verifier and the export audit all check out, and every
  offline result reproduced exactly: reference 853/23/0, 8 SQL groups, 15
  webhook groups, mutation 12/12 and 20/20, property 46, quality, quickstart
  and the documentation capture.
- The a13→a14 diff is release tooling, tests, docs and version metadata; no
  `src/` change beyond the version string, no SQL, and no service-path change.

### Fixed

- **Freshly written public evidence was owner-only** (R20). `tempfile.mkstemp`
  creates 0600 and `os.replace` keeps it, so a newly generated mutation record
  was published 0600 while every other `evidence/*.json` is 0644, and another
  account could not read it. The writer defect dates from a7; a14 enlarged it
  from one record to three. Whether it reached a consumer depended on the
  handoff: the a10, a12 and a13 archives retained 0600, while a14's delivered
  complete ZIP, source ZIP and sdist stored the same records 0644 even though a
  fresh a14 run still produced them 0600.
  Each writer now states its mode (`0o644` published, `0o600` for the private
  diagnostic inside `build/private/mutation`), `tools/review_runs.py` takes the
  same explicit parameter, and `tools/public_audit.py` gains
  `export_mode_findings` so any exported file that is not group/other readable
  is a finding. Two regressions cover the writer modes and the audit rule.

### Executed for this candidate (all bound to this tree)

- Reference 855 passed, 23 skipped, 0 failed; 8 SQL groups; 15 webhook groups;
  quality PASS; mutation PR 12/12 and nightly 20/20; property nightly 46;
  quickstart; documentation capture.
- **The three suites a14 recorded as NOT_RUN**: native PostgreSQL 18.3 +
  PgBouncer 1.24.1 **42 passed**; real Zabbix 7.0.10 through the recorder
  **6 passed**; deployment-path lab from the installed a15 wheel **4 passed,
  13/13 observations**. These runs belong to a15. a14 changed no service-path
  file, which supports an inference about its behaviour; its own records stay
  NOT_RUN.
- pip-audit, npm audit, export audit, wheel smoke, SBOM schema, release
  verifier; artifacts reproduced byte-identically across two builds.

### Still pending

- A2 transport remediation; the Docker Compose forms of both labs; hosted
  CI/CodeQL/Gitleaks; attestation issuance; an Alerta Web UI capture; G5's
  effective-role evidence on the approved installed account; licence review;
  external security review; publication inputs; G1–G8.


## 0.1.0a14 — 2026-09-13

Implemented the requested agent-workflow updates in order 1–4, 6, then 5.

- **1 — Task-specific AGENTS.md.** Route design, migration, lab and release
  references by task while preserving all release-one safety boundaries.
- **2 — Focused skills.** Add release-review, isolated-lab and migration-review
  skills with narrow descriptions and links to maintained project references.
- **3 — Completion and autonomy.** Add practical requests that carry authorised
  local implementation through repair and verification; production/publication
  authority remains separate.
- **4 — Retained runs.** Add a serial review runner with unique private run
  directories, nonblocking locks, phase records and snapshots of fresh evidence.
  Preserve interruption/cleanup uncertainty. Mutation PR/nightly records and
  per-attempt reports no longer replace each other.
- **6 — Packaging and provenance.** Include skills and evaluation cases in
  source ZIP/sdist, hash instruction files separately, and reject missing or
  changed guidance during build/verification. Correct stale release-tag examples
  and the inactive publication register/template to the current candidate;
  refresh the attestation example and SQL manifest label.
- **5 — Behavioural evaluation.** Supply five representative isolated agent
  tasks and retain their actual input/output review in the complete handoff.
  Metadata tests and qualitative agent observations are separate evidence.
  Artifact-review feedback adds an explicit read-only branch to the release skill.

Runtime Python is unchanged from a13 except the version string; SQL migrations
and production defaults are unchanged. No acknowledgement feature or production
cohort is enabled. See release amendment R19 and the handoff review for
justification, executed results, historical evidence and unavailable lab layers.

## 0.1.0a13 — 2026-09-12

Thirteenth alpha candidate, prepared locally and not published. It adopts the
independent review of a12 (review commit `81a41f73…` on `af035902…`) after
verifying it, and extends its finding. Runtime Python differs from a12 only in
`__version__`; SQL, the media artifact and dependency pins are unchanged, and
there is no new migration.

### Verified about the review

- Packet checksums, the retained byte-identical a12 archive, the review bundle
  and the rebuilt artifacts all check out; the wheel it ships is the a12 wheel.
- **A4 reproduced independently** on a separately built Zabbix 7.0.10: a role
  created with the original a12 rules read back with all eleven User-type
  actions enabled and **accepted a real suppression request**; close and
  severity were refused only by host rights (-32500), not by the role. With the
  corrected enumerated rules all three were refused by the role permission
  (-32602). A role enabling `acknowledge_problems` also accepted action 16,
  confirming the shared acknowledge/unacknowledge capability.
- The stale `documentation_visuals` block in the a12 compatibility manifest (F2)
  was real: it still said browser capture was NOT_RUN after a12 took the
  captures. The review's correction is adopted.

### Added

- The review's corrections, unchanged: `lab/zabbix_roles.py` (enumerated
  User-type action/UI rules plus `verify_role`), the effective read-back before
  user/token creation, the staged Docker context dependency, the bounded lab
  provisioning timeout, the full projection-predicate wait and ten regressions.
- A4 extended to the operator-provisioned real-Zabbix fixture: `lab/real_zabbix.md`
  requires enumeration and read-back and names the `role_rules` constructor for
  the worker and witness shapes; two regressions pin those shapes; and
  `tests/real_zabbix/test_contract.py::test_A4_restricted_token_excluded_actions_denied_by_role_permission`
  requires the live server's specific suppression-permission denial for the
  supplied token and refuses a host-rights rejection as evidence.
- Amendment R18 and the A4 addendum recording the independent reproduction.

### Executed for this candidate (all bound to this tree)

- Reference 820 passed, 23 skipped, 0 failed; 8 SQL groups; 15 webhook groups;
  quality PASS; mutation PR 12/12 and nightly 20/20; property nightly 46;
  quickstart; documentation capture.
- Native PostgreSQL 18.3 + PgBouncer 1.24.1: **42 passed** across five phases.
- Real Zabbix 7.0.10 through the recorder: **6 passed**, including the A4 case.
- Deployment-path lab from the installed a13 wheel: **4 passed, 13/13
  observations**, with the corrected role read-back and projection wait.
- pip-audit, npm audit, export audit, wheel smoke, SBOM schema, release
  verifier; artifacts reproduced byte-identically across two builds.

### Still pending

- A2 transport remediation; the Docker Compose forms of both labs; hosted
  CI/CodeQL/Gitleaks; attestation issuance; an Alerta Web UI capture; G5's
  effective-role read-back and permission tests on the approved installed
  account; licence review; external security review; publication inputs;
  G1–G8 (`docs/BETA_READINESS.md`).


## Independent review of 0.1.0a12 — 2026-09-12

Reviewed edition; no new package version, tag or public plugin upload. Full
prior release history and the engineering upgrades 1–8 remain below.

- Corrected the synthetic Zabbix role provisioner: enumerate explicit action/UI
  permissions and verify effective `role.get` output before issuing users or
  tokens (A4, R17). The prior default-only assertion did not prove that omitted
  rules were disabled. Eight new regressions and a realistic omitted-rule mock
  protect this boundary. Runtime Python and all SQL are unchanged.
- Corrected an asynchronous projection-observation race in the installed-wheel
  lab: wait for the original full predicate, with a regression rejecting partial
  or incorrect state. Retained the 3-pass/1-fail attempt and its twelve successful
  observations; production code and SQL are unchanged.
- Included the shared role helper in the Docker build context and added a real
  staged-import regression. Increased only the synthetic lab RPC bound to 30
  seconds after a 13-second cold request timed out under CPU emulation.
- Built and exercised a recent Zabbix 7.0 LTS in an isolated VM, with separate
  original-contract and supplemental API evidence. Preserved failed attempts,
  their harness snapshots and the regression failing on the original source.
- Repeated reference and native checks on the corrected harness. The reference
  result is 818 passed / 22 skipped / 0 failed; native PostgreSQL/PgBouncer is
  42 passed / 0 failed / 0 skipped with successful cleanup. Final service and
  package details are in `docs/INDEPENDENT_REVIEW.md` and the complete handoff.
- Fixed stale screenshot metadata/paths; retained supplied images with clear
  source/version labels. Updated the public site, technical guide and blog with
  current review evidence and the effective-permission finding.
- Added the executed VM helpers, source/package input hashes and reproduction
  recipe to the handoff. Kept G1–G8, A2, hosted workflows, Compose execution,
  attestation and external security review pending.


## 0.1.0a12 — 2026-09-12

Twelfth alpha candidate, prepared locally and not yet published. Base: a11
candidate `79cf4fe7…`. Runtime Python differs from a11 only in `__version__`;
SQL, the media artifact and dependency pins are unchanged. a11 was a
documentation release with every service suite NOT_RUN; a12 executes them on
that code and completes the screenshots a11 could not take.

### Added

- Browser captures (R16): full-page headless-Chromium renders of the three
  transcript pages and a real Zabbix 7.0.10 frontend screenshot of the
  deployment lab's acknowledged event (`04-zabbix-event-details.png`) with the
  source fact, the `[zabalack]` acknowledgement, the update fact, a human update
  and the recovery fact. The PNG files live in the complete handoff's
  `visual-previews/` and on the website (the source tree stores no binaries);
  `docs/visuals/browser-captures.json` records tool and hashes. Lab data only;
  no Alerta UI screenshot (the Alerta Web UI is not part of the lab).
- `tests/alerta/test_docs_walkthrough.py` added to the native `alerta` phase of
  `tools/native_ci.py`, so the documented walkthrough also runs on a real
  PostgreSQL 18.3 cluster (native `alerta` phase: 7 cases).
- Visual guide section 4 (the Zabbix view) and a browser-capture section; the
  website's guide and the a12 blog post carry the Zabbix screenshot.

### Original a12 preparation evidence (bound to the original tree)

- Reference suite 808 passed, 22 skipped, 0 failed (830 collected, all
  selected); 8 SQL groups; 15 webhook groups; quality PASS; mutation PR 12/12
  and nightly 20/20; property nightly 46; quickstart; documentation capture.
- Native PostgreSQL 18.3 + PgBouncer 1.24.1 (`tools/native_local.py`):
  **42 passed, 0 failed, 0 skipped** (`postgres` 24, `delivery_faults` 5,
  `worker` 4, `alerta` 7, `performance` 2).
- Real Zabbix 7.0.10 (`tools/record_real_zabbix.py --run`): **5 passed**.
- Deployment-path lab (`tools/run_deployment_e2e_local.py`) from the installed
  a12 wheel: **4 passed, 13/13 observations**.
- pip-audit and npm audit, export audit, wheel smoke, SBOM schema, release
  verifier PASS; artifacts reproduced byte-identically across two builds.

### Verified about a11

- Packet checksums, bundle lineage, wheel identity (rebuilt byte-identically),
  release verifier and export audit; reference 808/22/0, quality, SQL, webhook,
  quickstart, mutation and capture results reproduced. The a10→a11 diff is
  documentation, the capture tool, the walkthrough test and version metadata;
  no runtime or SQL change.

### Still pending

- Docker Compose forms of both labs, hosted CI/CodeQL/Gitleaks, attestation
  issuance, an Alerta Web UI capture, A2 transport remediation, production-like
  Zabbix behaviour, licence review, external security review, publication
  inputs, G1–G8 (`docs/BETA_READINESS.md`).

## 0.1.0a11 — 2026-09-12

Eleventh alpha candidate, based on a10 commit
`e84ce39f58f7418f29e4a75dc331ea1ecd206646`. Prepared locally; package and
repository publication remain pending. The informational website is updated
separately. This is a documentation release; runtime Python differs from a10
only in `__version__`, and every SQL file, media artifact and dependency pin is
unchanged.

### Added, with reasons

- Three **output illustrations** for inert CLI configuration, a synthetic
  single-alert Alerta action and the worker/ledger projection. These make the
  practical guide easier to follow. They are labelled observed output rather
  than product UI screenshots: browser policy blocked local capture pages.
- `docs/visual-guide.md`, SVG images, browser-ready HTML transcript pages and a
  version/source/harness-bound JSON record with content hashes. Text remains
  copyable, images have descriptive alternatives and full-size links. PNG
  companions in the complete ZIP are renders of the SVG, not screen captures.
- `tools/capture_docs.py --isolated-lab`: accepts no endpoint, DSN, credential or
  target argument; clears inherited fixture settings, creates a disposable WASM
  lab, executes the demonstration and exports selected non-secret fields. A
  failed demonstration produces no final capture record. Raw logs stay local.
- `tests/alerta/test_docs_walkthrough.py` (T1/T36), selected in the reference
  runner: real Alerta action dispatch, actual PostgreSQL capture, real worker
  verification/delivery/projection and repeat-Ack rejection. Zabbix is mocked;
  the fixture's synthetic JWT, injected probe, test client and SET ROLE are
  disclosed. This is supplemental evidence, not installed-service evidence.
- The technical website guide and a11 blog post contain all three views. The
  a10 article gains a clearly dated a11 visual companion without changing its
  service results. Articles a1–a9 are byte-identical to the supplied website.

### Updated

- Version metadata, release template (still inactive), SBOM and the export
  gate's expected version now agree on a11. Source distributions include the
  SVG/HTML documentation assets.
- Current compatibility and evidence records identify a11 execution separately
  from a10. The original a10 complete ZIP is preserved byte-for-byte, including
  its source, service reports, Git history, reviews and earlier release history.
- Release checks initially found the export gate still expecting a10; its
  version constant was corrected and the entire reference suite rerun. No test
  assertion or export rule was weakened.

### Executed on the final source and harness

- Reference suite **808 passed / 22 skipped / 0 failed**: 779 unit, mock,
  property and offline cases, plus 29 PostgreSQL WASM passes. All 830 collected
  cases are selected. Four WASM skips require native semantics; eighteen cases
  require optional services.
- Eight SQL groups; fifteen synthetic webhook groups; quickstart; Ruff, mypy and
  pip check; PR mutation 12/12 and nightly mutation 20/20 detected; additional
  fixed-seed nightly property profile 46/0/0. Repeated profiles overlap and are
  not added to unique case totals.
- The documentation recorder completed on the final tree. Static validation
  checks 14 website pages, 330 local references and seven uses of the three
  figures; nine historical articles are unchanged.
- Fresh dependency queries: 58 Python packages, zero reported advisories;
  npm audit zero vulnerabilities. CycloneDX 1.6 schema validation passed for
  61 components using the schema snapshots in the complete ZIP.
- Reproducible build and fresh-wheel checks are recorded with the final artifact
  hashes in `dist/BUILD_PROVENANCE.json` and the complete ZIP's `verification/`.

### Not executed / still open

- Actual browser or product UI screenshots: local-file navigation was blocked
  by browser security policy. No workaround was attempted; the static SVG
  figures are a labelled fallback. See the capture recipe in the visual guide.
- Native PostgreSQL/PgBouncer, real Zabbix and the installed-service deployment
  lab were not rerun for a11. Local services were not configured and Docker is
  unavailable. a10's 41 native, 5 real-Zabbix and 4 deployment cases are historical
  evidence, not a11 passes. Hosted Actions, CodeQL, Gitleaks and attestations were
  not run. G1–G8 and A2 remain open; no production access was attempted.

## 0.1.0a10 — 2026-09-12

Tenth public alpha candidate, prepared locally and not yet published. Base:
a9 candidate `92bac446…`. Runtime Python, SQL (schema 3, migrations 001–007 in
the `zabalack` namespace), the media artifact and every dependency pin are
unchanged apart from the version string. This candidate executes the renamed
namespace on real services for the first time and supplies the non-Docker
runners that the independent a8 review's F2 asked for (R14).

### Added

- `tools/native_local.py`: non-Docker form of `tools/native_ci.py` — same
  phase table, targets and `ACK_TEST_*` environment; fresh cluster per phase
  through `lab/local_runtime.py`; PgBouncer from `lab/pgbouncer.ini`; record
  labelled `not_docker: true` with the observed service versions.
- `tools/run_deployment_e2e_local.py`: non-Docker form of
  `tools/run_deployment_e2e.py` — executes the unmodified
  `lab/deployment_e2e_runtime.py` on operator-provided local services, generates
  the bootstrap and lab certificate, and requires a runner environment with the
  built wheel installed non-editably (installed bytes compared with the wheel,
  entry points read from the installed distribution, wheel SHA-256 recorded).
- `docs/local-services.md` (operator inputs) and eleven offline regressions in
  `tests/release/test_local_service_runners.py`.

### Executed for this candidate (all bound to this tree)

- Reference suite 807 passed, 22 skipped, 0 failed; 8 SQL groups; 15
  webhook groups; quality (ruff, mypy, pip check) PASS; mutation PR 12/12 and
  nightly 20/20 killed; quickstart; property nightly profile.
- Native PostgreSQL 18.3 + PgBouncer 1.24.1 through `tools/native_local.py`,
  five fresh-cluster phases: **41 passed, 0 failed, 0 skipped** (`postgres`
  24 including the a9 legacy-schema guards, `delivery_faults` 5, `worker` 4,
  `alerta` 6, `performance` 2).
- Real Zabbix 7.0.10 through `tools/record_real_zabbix.py --run`: **5 passed**;
  `[zabalack]` acknowledgement comments observed on the disposable instance.
- Deployment-path lab through `tools/run_deployment_e2e_local.py` from the
  installed a10 wheel: **4 passed, 13/13 observations true**.
- pip-audit and npm audit, export audit, wheel smoke, SBOM schema, release
  verifier PASS; artifacts reproduced byte-identically across two builds.

### Verified about a9

- Packet checksums, bundle lineage, artifact identity and reference/quality/
  mutation/SQL/webhook results all reproduced; the mechanically renamed
  a8-reviewed tree differs from a9 only in the documented guards, legacy-proof
  handling, echo recognition and metadata.

### Still pending

- Docker Compose forms of both labs, hosted CI/CodeQL/Gitleaks, attestation
  issuance, A2 transport remediation, media/notification behaviour on a
  production-like Zabbix, query plans at scale, licence review, external
  security review, publication inputs, G1–G8 (`docs/BETA_READINESS.md`).

## 0.1.0a9 — 2026-09-12

**Breaking rename from Ackline.** The public name is Zab-al-ack and the package,
Python module, CLI, Alerta entry points, environment/config keys, database
namespace and roles, trigger, service files, collectors, monitoring labels,
media artifact, CI/release tooling and operational examples use `zabalack`.
The website, technical examples and release blog use the new brand.

The rename deliberately changes the HMAC domain and proof/comment prefixes.
Historical proofs are not executable in the new namespace, even after visible
prefix substitution. Legacy proof strings still receive diagnostic redaction;
legacy reserved attributes are stripped; both exact neutral comment formats
are suppressed as echoes before endpoint or secret lookup. No old proof is
accepted as authority for a new delivery.

Installation and trigger enablement refuse a database containing `ackline`.
SQL 001–007 are a fresh `zabalack` baseline, schema version 3, **not an edit to
an installed Ackline migration**. Existing installations keep their old
ledger and release; a same-database in-place transition is not supplied.
[Transition guidance](docs/RENAME.md) explains fencing, retention, requalification
and non-destructive rollback. R13 records the rationale and narrow amendment;
`docs/rename_manifest.json` records both namespaces' SQL hashes.

Added regression coverage for legacy-domain rejection before target access,
proof privacy, inert legacy configuration and real PostgreSQL-engine installation
guards. Updated all existing tests to exercise the new namespace. Fresh reference,
SQL, quality, mutation and packaging results are recorded in `ALPHA_REPORT.md`;
prior Ackline reports remain untouched in the historical handoff. Native and
real-service coverage is labelled separately; G1–G8 remain open.

The a8 review's strict deployment-evidence validator and all eight quality
upgrades are retained. Dependency versions and delivery policy are unchanged.
No plugin repository/package was pushed or published and no production system
was accessed. The public informational website is updated separately.

---

## Historical Ackline changelog (preserved below)

# Changelog

## Independent a8 review correction — not a new release

- Reject incomplete or contradictory deployment-lab reports before source
  export/build, in unsigned archive verification, and before the service runner
  records PASS. Check all four cases, thirteen observations, cleanup, versions,
  runtime identities, timestamps and unchanged-tree binding. Reject duplicate
  JSON fields and NOT_RUN reports that still claim execution.
- Preserve R12's local-process evidence as a distinct reported scope. It does
  not establish the Docker or installed-wheel service path. The local bootstrap
  and recorder used during a8 preparation are not supplied for reproduction.
- Runtime Python, SQL, dependency pins and webhook behavior are unchanged.
  The correction changes tooling, tests and documentation only; package version
  remains 0.1.0a8 and original a8 artifacts/evidence are retained separately.
  See docs/INDEPENDENT_A8_REVIEW.md for rationale and validation boundaries.

## 0.1.0a8 — 2026-09-11

Eighth public alpha candidate, prepared locally and not yet published. The
a6/a7 upgrades were executed on real services for the first time — native
PostgreSQL 18.3 / PgBouncer including the worker crash and race tests and
migration 007, real Zabbix 7.0.10, and the deployment-path lab — and the
defects that execution exposed are corrected (R12). Base: a7 candidate
`67f84543…`. Python runtime unchanged apart from the version string; SQL
unchanged (schema 3, migrations 001–007).

### Fixed

- **Reference media webhook** (`zabbix/v1/webhook.js`, artifact rebuilt): a
  real Zabbix 7.0.x expands `{EVENT.CAUSE.ID}` to `*UNKNOWN*` for an event
  without a cause; the script rejected it and every cause-less problem,
  update and recovery notification failed before any HTTP request. Now
  treated as "no cause" (`null`); arbitrary text still rejected; the synthetic
  Node contract had asserted the wrong behaviour and is corrected (14 groups).
  Amendment A3. Present since 0.1.0a1; deployments that imported the earlier
  artifact must re-import it.
- Native `postgres` phase: T37 left its verification-lease flood live, so the
  a6 settled-expiry tests failed when run after it; T37 releases its flood.
- Deployment-path lab: accept CRLF storage of the imported media script while
  still rejecting a changed script; wait a bounded configuration-cache grace
  after creating the source action before raising the problem, otherwise the
  server never escalates it. Offline regressions for both.

### Executed for this candidate (all bound to this tree)

- Reference suite 725 passed, 22 skipped, 0 failed (702 unit/mock/property/offline + 23 WASM); 8 SQL groups; 14 webhook groups; quality
  (ruff, mypy, pip check) PASS; mutation PR 12/12 and nightly 20/20 killed on a
  107-test baseline; quickstart.
- Native PostgreSQL 18.3 + PgBouncer 1.24.1, five fresh-cluster phases:
  **36 passed, 0 failed, 0 skipped** (`postgres` 19,
  `delivery_faults` 5, `worker` 4, `alerta` 6, `performance` 2).
- Real Zabbix 7.0.10 through `tools/record_real_zabbix.py --run`:
  **5 passed** (contract with witness-proved negative case, end-to-end).
- Deployment-path lab on equivalent local processes: **4 passed, 13/13
  required observations** — installed Alerta and worker processes, real
  logins, distinct password-authenticated SQL roles, media import, macro
  ingestion, echo filtering, human update, recovery.
- pip-audit 58/0, npm audit 0, export audit 0 findings, clean-venv wheel
  smoke, SBOM schema valid, release verifier PASS.

### Still pending

- Hosted CI/CodeQL/Gitleaks, attestation issuance, the Docker Compose forms of
  both labs, media/notification behaviour on a production-like Zabbix, A2
  transport remediation, query plans at scale, licence review, external
  security review, publication inputs, G1–G8 (`docs/BETA_READINESS.md`).

## 0.1.0a7 — 2026-09-11

- Add a reviewed mutation catalogue with bounded PR and expanded nightly profiles.
  Unchanged baselines must pass; only behavioral assertion failures count as
  kills. Invalid mutants, survivors, missing tests, errors and timeouts fail.
  Mutation testing exposed and closed missing wrong-event/trigger regressions.
- Require fresh mutation evidence alongside quality/reference/native CI before
  packaging. Extend source-binding coverage to deployment configuration, service
  definitions, release templates and all GitHub policy files.
- Add an isolated normal-process deployment lab: installed application and worker,
  ordinary authentication, separate native SQL logins, real Zabbix and media flow.
  Runtime execution remains NOT_RUN here; offline harness results are separate.
- Add opt-in commit-pinned CodeQL analysis with narrow result-upload permissions.
  Supply an inactive attestation workflow and strict release/attestation verifiers;
  local unsigned provenance is never relabeled as a signed hosted attestation.
- Record source-backed amendment A2: Zabbix 7.0.10 webhook HttpRequest disables
  TLS peer/host verification and follows redirects. Deployment requires reviewed
  transport remediation and negative tests; the worker TLS policy is unchanged.
- Correct README compatibility rows that did not clearly label historical native
  and real-Zabbix execution. Preserve G1–G8 and all publication-owner inputs.
- Runtime code is unchanged apart from version metadata. Migrations 001–007 and
  schema version 3 are unchanged; this candidate adds no SQL migration.

## 0.1.0a6 — 2026-09-11

- Adopt the a5 independent evidence corrections: ignore generated egg-info in
  source digests, require bound acceptance results, and make the real-Zabbix
  recorder own fresh execution and invalidate prior success before errors.
- Join fresh quality/reference/native CI artifacts from one repository, commit,
  run and attempt; reject missing, stale, mismatched or failed evidence before
  regenerating acceptance and building. Package upload remains private CI output.
- Add hash-pinned Ruff, strict gradual mypy and Hypothesis; check validated
  source/actor/intent boundaries. Runtime edits preserve wire format and SQL.
- Add generated trust-boundary tests and restricted-function ledger state
  machines, with deterministic PR and bounded nightly profiles.
- Add native worker process races, dropped responses, SIGKILL after remote commit,
  and stale-owner completion fencing against an isolated HTTPS source stub.
- Fix a ledger defect found by generated testing: explicitly settled no-effect
  work now closes at its original expiry so a later human action can get a new
  generation. Forward migration 007 requires schema 3; no automatic retry.
- Bind SQL and quality records to executable inputs; include CI/config/quality
  pins in harness digests. Migrations 001–006 remain byte-identical.
- Native, PgBouncer, real-Zabbix and hosted Actions execution remain NOT_RUN for
  this candidate workspace. See ALPHA_REPORT.md for actual final-tree results.

## 0.1.0a5 — 2026-09-10

Fifth public alpha candidate, prepared locally and not yet published. Adopts
the independent a4 review's seven-file tooling patch unchanged (R9), closes
the review items this lab can close — run records bound to source/harness
digests, positive proof for the outside-scope Zabbix case — and executes every
suite, including the real-service cases the review could not run, on this
exact code. Base: a4 candidate `36f8df8b…` + review patch `ff1f2854…`.
Production Python and SQL are unchanged apart from the version string; no
migration is added and existing schema-2 installations need no database work.

### Fixed (from the independent a4 review, R9)

- Test-harness safety: the real-Zabbix fixtures (`test_contract.py`,
  `test_end_to_end.py`) posted the `apiinfo.version` probe to the fixture URL
  before checking it was the declared loopback lab API, and the older contract
  fixture accepted URL userinfo, query and fragment. Both now call one
  validator (`lab/real_zabbix_safety.py`) before any HTTP client exists or a
  token file is opened, accept exactly `http://127.0.0.1:58080/api_jsonrpc.php`,
  and require a correlated JSON-RPC 2.0 `7.0.x` version envelope; 46 mocked
  regressions (`tests/security/test_real_zabbix_lab_boundary.py`). Reproduced
  here with the review's HTTP-spy reproducer on unmodified a4 (client
  constructed and probe sent for a remote URL) and confirmed fixed (no client).
- Evidence validation: `tools/update_acceptance.py` accepted a real-Zabbix run
  recorded as `FAIL` while selecting an old all-passing XML, ignored count/XML
  disagreement, and treated a missing native cleanup result as success. It now
  requires explicit integer cleanup exit 0, a `PASS` status, non-empty
  all-passing XML and exact counts; `NOT_RUN` selects no XML and carries no
  counts. 24 regressions (`tests/release/test_real_zabbix_evidence.py`,
  `test_native_evidence_failures.py`); 16 of them fail on the unmodified a4 mapper.
- The end-to-end read-back uses the canonical `selectAcknowledges` parameter
  instead of the deprecated `select_acknowledges` alias (review note).
- Found while verifying this candidate: `tests/postgres/assertions_boundaries.sql`
  failed intermittently on the WASM engine (`T14 bounded candidate count`, about
  1 run in 13) because three consecutive acknowledgement updates carried the
  identical marker text and differed only in `clock_timestamp()`, which PGlite
  resolves to milliseconds; the capture trigger's WHEN predicate correctly
  ignores a history head whose (time, type, text) is unchanged. The test now
  gives each entry distinct operator text before the marker; 50 consecutive
  WASM runs pass. Harness-only; the trigger and migrations are unchanged.

### Added

- **Evidence bound to the tree it ran on** (a3 and a4 review item): every
  run record carries `code_digests` — `source` over `src/` and `sql/`,
  `harness` over `tests/`, `tools/`, `lab/`, `zabbix/`, `collectors/`,
  `pyproject.toml` and the lock files (`tools/evidence_utils.py`). The
  reference and native runners bind before anything executes;
  `tools/update_acceptance.py` refuses any record whose digests differ from
  the tree it is invoked on; `tools/public_audit.py` (and therefore
  `tools/build_release.py`) refuse an export whose records are missing,
  not final or not bound; `evidence/acceptance-results.json` records the
  digests it was built from. 34 regressions (`tests/release/test_evidence_binding.py`).
- `tools/record_real_zabbix.py`: records an executed or explicitly not-run
  real-Zabbix suite from its raw JUnit — redaction, counts and status from the
  executed cases only, digest binding, and refusal of any report that names
  the fixture, a token file or an operator-forbidden string.
- Outside-scope negative case with positive proof (a3 and a4 review item):
  `test_T11a` now requires a separate read-only witness token that can see
  both host groups, proves the outside event exists, belongs to the outside
  group and not to the cohort group, then requires the restricted token's read
  of that event to be a valid empty result rather than a generic rejection.
  Without the witness the case reports NOT_RUN. Fixture key
  `outside_scope_source.witness_token_file`; lab procedure in `lab/real_zabbix.md`.
- `[tool.pytest.ini_options] pythonpath = ["."]`: the review's
  `from lab.real_zabbix_safety import …` only resolved under `python -m pytest`;
  bare `pytest` failed to collect `tests/real_zabbix` and the new boundary test.
  Regression test spawns pytest from another working directory.

### Executed for this candidate

- Reference suite: 354 passed, 13 skipped, 0 failed (340 unit/mocked + 14
  WASM); 8 SQL groups; 12 webhook groups; isolated quickstart.
- Native PostgreSQL 18.3 + PgBouncer 1.24.1, database `alerta`, fresh cluster per
  phase: **22 passed, 0 failed, 0 skipped**, cleanup exit 0 per phase, record
  bound to this tree.
- Real Zabbix 7.0.10 with the patched fixtures: **5 passed, 0 failed, 0 skipped**
  — T13, T33, T11a (witness-proved), T1/T25 and the end-to-end case; recorded
  with `tools/record_real_zabbix.py`.
- pip-audit (hash-verified install) 50/0, npm audit 0, export audit 0 findings
  (evidence binding included), clean wheel install with the `[alerta]` extra,
  SBOM schema valid.

### Still pending (see `docs/BETA_READINESS.md`)

- The ten publication inputs, hosted CI first run (Docker-form native job,
  `security.yml`), Zabbix media-type/macro/update-notification behaviour on a
  production-like instance, external security review, retained-ledger query
  plans at scale, licence-label review, deployment gates G1–G8.

## 0.1.0a4 — 2026-09-10

Fourth public alpha candidate, prepared locally and not yet published. Adopts
the independent a3 review's corrections unchanged (R7), adds a full end-to-end
case on real components, and executes every suite — including migration 006,
its upgrade scenarios and the native PostgreSQL 18 / PgBouncer phases — on this
exact code. Base: a3 candidate `afdfb858…` + review commit `9a22f934…`.

### Fixed (from the independent a3 review, R7)

- Delivery coordination: a Zabbix response arriving after the delivery lease
  expired recorded `late_response` but left the action eligible for another
  send; replacement-worker shutdown could also clear uncertainty inherited from
  the previous worker. Forward migration `sql/migrations/006_late_response_fencing.sql`
  adds `ackline.attempt_unsettled()`, replaces `worker_call`/`expire_due_effects`,
  repairs affected existing work and raises the database schema version to 2
  (`ackline_schema_version`). Migrations 001–005 are byte-identical. The seven-scenario
  SQL group `tests/postgres/assertions_late_response.sql` fails on unmodified a3
  and passes after 006; two 005→006 upgrade scenarios (`test_late_response.py`)
  prove repair, denied reclaim and the explicit reseal requirement.
- Release evidence: `tools/native_ci.py` invalidates its previous reports and
  records `RUNNING` before touching Docker, persists startup/cleanup failures;
  `tools/update_acceptance.py` rejects failed, interrupted, empty or
  inconsistent native inputs; 34 new evidence/manifest regressions.
- `docs/sql_manifest.json` digests refreshed for the renamed files (all 11 were
  still the pre-rename bytes); `tools/public_audit.py` and `tools/build_release.py`
  now refuse stale, incomplete, duplicate or unsafe manifests.
- `tests/real_zabbix/README.md` historical statement corrected.

### Added

- `tests/real_zabbix/test_end_to_end.py` — real Alerta HTTP `PUT /alert/<id>/action`
  → capture trigger on the native PostgreSQL 18 lab → production `Worker` with the
  production `EndpointRegistry` and `ZabbixClient` → real `event.acknowledge` as the
  restricted lab user → ledger `ok`/`write_response` with exactly one attempt →
  projection `zabbix_ack_sync=ok`, source identity preserved → Zabbix read-back
  shows one `[ackline]` acknowledgement carrying the public reference → a second
  delivery pass writes nothing. Selected by the reference runner's optional phase
  (skips without fixtures); the fixture gains an `end_to_end` block with its own
  trigger/problem (`lab/real_zabbix_fixture.example.json`, `lab/real_zabbix.md`).

### Executed for this candidate

- Reference suite: 250 passed, 13 skipped, 0 failed (236 unit/mocked + 14 WASM;
  the extra skip is the new end-to-end case without fixtures); 8 SQL groups;
  12 webhook groups; isolated quickstart.
- Native PostgreSQL 18.3 + PgBouncer 1.24.1, database `alerta`, fresh cluster per
  phase: **22 passed, 0 failed, 0 skipped** — `postgres` 10 (incl. the late-response
  group and the 005→006 upgrade scenario with reseal), `worker` 4, `alerta` 6,
  `performance` 2. Cleanup outcome recorded per phase.
- Real Zabbix 7.0.10: **5 passed, 0 failed, 0 skipped** — the four API contract
  cases and the end-to-end case above.
- pip-audit (hash-verified install) 50/0, npm audit 0, export audit 0 findings,
  clean wheel install with the `[alerta]` extra, SBOM schema valid.

### Changed

- Handoff checksum lists now cover nested checksum files (only the list itself
  is excluded), as the a3 review noted.

### Removed

- `REVIEW_STATUS.md` (a3 review checkout note; superseded by this entry and R7/R8).

### Still pending (see `docs/BETA_READINESS.md`)

- The ten publication inputs, hosted CI first run (Docker-form native job,
  `security.yml`), Zabbix media-type/macro/update-notification behaviour on a
  production-like instance, external security review, retained-ledger query
  plans at scale, licence-label review, deployment gates G1–G8.

## 0.1.0a3 — 2026-09-10

Third public alpha candidate, prepared locally and not yet published. Adopts the
Ackline identity end to end, folds in the a2 review corrections (R4), and
re-executes every suite — including the native PostgreSQL 18 / PgBouncer phases
and the a2 review's new native SQL assertions — on the renamed code. The
rename rationale is in `docs/RELEASE_AMENDMENTS.md` R5; the remaining path to a
beta is in `docs/BETA_READINESS.md`.

### Changed — project identity (breaking for any a1/a2 lab installation)

| Was | Now |
|---|---|
| PyPI/project `msp-alerta-zabbix-ack`, module `msp_alerta_ack`, CLI `msp-ack` | `ackline` / `ackline` / `ackline` |
| Alerta plugin entry points `msp_zabbix_normalise`, `msp_zabbix_ack` | `ackline_zabbix_normalise`, `ackline_zabbix_ack` |
| Database schema `msp` | `ackline` (all functions, tables and grants schema-qualified as `ackline.*`) |
| Roles `msp_owner`, `msp_worker`, `msp_operator`, `msp_installer`, `alerta_capture_probe` | `ackline_owner`, `ackline_worker`, `ackline_operator`, `ackline_installer`, `ackline_probe` |
| Capture trigger `msp_ack_capture` | `ackline_capture` |
| Signed marker prefix `[msp-ack:v1 …]` | `[ackline:v1 …]` (same length and grammar; v1 stays v1 because no signed marker exists outside disposable labs) |
| Health field `msp_schema_version`; metrics `msp_ack_*`; error codes `msp_ack_*`; Flask `g.msp_ack_context` | `ackline_schema_version`; `ackline_*`; `ackline_*`; `g.ackline_context` |
| Environment `MSP_MIGRATION_DSN`, `MSP_ACK_CONFIG_FILE`, `MSP_LAB_BEARER_TOKEN` | `ACKLINE_MIGRATION_DSN`, `ACKLINE_CONFIG_FILE`, `ACKLINE_LAB_BEARER_TOKEN` |
| Paths `/opt/msp-alerta-ack`, `/etc/msp-alerta-ack`, `/var/lib/msp-alerta-ack`; units `msp-alerta-ack-worker.service`, `msp-alerta-ack-reconcile.service/.timer`; playbook `02_msp_migrations.yml`; Ansible variables `msp_*` | `/opt/ackline`, `/etc/ackline`, `/var/lib/ackline`; `ackline-worker.service`, `ackline-reconcile.service/.timer`; `02_ackline_migrations.yml`; `ackline_*` |
| Zabbix media type "MSP Alerta source contract v1 REFERENCE DISABLED" | "Ackline source contract v1 REFERENCE DISABLED" (`zabbix/v1/media_type.reference.json` regenerated by `build_export.py`) |
| Archive/bundle names `alerta-zabbix-ack-*`; NOTICE/author "Alerta Zabbix Ack contributors" | `ackline-*`; "Ackline contributors" |
| Zabbix acknowledgement comment prefix `[alerta-ack] Acknowledged by …` (also the echo pattern in `zabbix/v1/webhook.js`); Compose project names `alerta-ack-*`; SBOM property namespace `alerta-ack:`; example service user `svc-alerta-ack` | `[ackline] Acknowledged by …`; `ackline-*`; `ackline:`; `svc-ackline` |

Unchanged on purpose: Alerta alert attributes (`zabbix_ack_sync*`,
`zabbix_ack_sync_intent`, `source_system`, `zabbix_*` source keys), the
upstream roles `alerta_owner`/`alerta_app` used by the lab, the `ACK_TEST_*` and
`ACK_LAB_*` test variables, and the `ack_test.*` SQL settings — none of them is
a product identifier. Any disposable a1/a2 lab database must be recreated: the
schema and roles are new objects, and `sql/migrate.py` records migrations under
the new schema. No tracked operational installation exists (see R5).

### Included from the a2 independent review (R4)

- Gitleaks built with the upstream version linker symbol; version assertion kept.
- Acceptance aggregation by exact test identity and layer; missing/failed native
  reports stop mapping; runners delete their previous JUnit before launching.
- Handoff metadata excluded from source exports.
- Lab cluster starts with `unix_socket_directories=''` (loopback TCP only).
- T14/T38 native SQL assertions for the a2 marker and grant changes.
- 21 release-tooling regression tests (`tests/release/test_a2_regressions.py`).

### Executed for this candidate (all on the renamed code)

- Unit/mocked/security/release/rpc/alerta suites: 202 passed, 0 failed, 6 native-only skips.
- Reference suite with PostgreSQL 18.3 WASM: 214 passed, 12 skipped, 0 failed; 7 SQL assertion groups; 12 webhook contract groups; isolated quickstart.
- Native PostgreSQL 18.3 + PgBouncer 1.24.1, database `alerta`, fresh cluster per phase: 20 passed, 0 failed, 0 skipped (`postgres` 8, `worker` 4, `alerta` 6, `performance` 2) — now including the R4 T14/T38 native assertions.
- pip-audit (hash-verified install), npm audit, public export audit, clean wheel installation with the `[alerta]` extra.
- **Real Zabbix contract suite: 4 passed, 0 failed, 0 skipped** against a disposable Zabbix 7.0.10 (server built from the upstream source tarball, frontend on PHP 8.3, PostgreSQL 16): exact original-event read (T13), approved host inventory (T33), restricted-token negative visibility (T11a) and one explicit synthetic acknowledgement with public-reference read-back (T1/T25), as a dedicated lab user limited to `event.get`/`event.acknowledge`/`host.get` with no media types or actions enabled. `evidence/real-zabbix.xml`, `evidence/real-zabbix-run.json`. The fixture now accepts any 7.0.x patch release and records the observed version; the reference pin stays 7.0.0 and the production endpoint registry still approves only configured versions.

### Fixed (found while regenerating evidence)

- `tools/public_audit.py` and `.gitignore` now exclude `.lab/`, the data
  directory `lab/local_runtime.py` creates. On a host that had run the local
  lab, the export audit and `tools/build_release.py` would otherwise have
  selected the PostgreSQL data files into the public source ZIP (1,400+ binary
  files flagged). Regression added to `tests/release/test_public_release.py`.
- `lab/local_runtime.py` retries the cluster start for up to a minute when the
  lab port is held in `TIME_WAIT` by a lab client (the lab ports sit inside the
  default ephemeral range); `lab/README.md` documents reserving them.

### Removed

- `REVIEW_STATUS.md` (a2 review checkout note; superseded by this candidate's changelog and R4/R5).

### Still pending (see `docs/BETA_READINESS.md`)

- The ten publication inputs, hosted CI first run (Docker-form native job,
  `security.yml`), real-Zabbix media-type/notification behaviour on a
  production-like instance, external security review, deployment gates G1–G8.

## Unreleased review corrections on 0.1.0a2

Gitleaks build-version injection, accurate native duplicate-result aggregation,
stale-JUnit rejection, handoff metadata exclusion from source ZIPs, and local-lab
TCP-only startup. Add focused release regressions and strengthen T14/T38 SQL
security assertions. Correct a1's local candidate/lab-installation wording.
No application or migration changes; see `docs/RELEASE_AMENDMENTS.md` R4.

## 0.1.0a2 — 2026-09-10

Second public alpha candidate, prepared locally and not yet published. Corrects
the findings of the first independent review of 0.1.0a1, which executed the
native PostgreSQL 18 / PgBouncer phases for the first time. Reasoning for each
change is in `docs/RELEASE_AMENDMENTS.md` R3.

### Fixed

- Native test phases could not pass against the lab database that
  `tools/native_ci.py` and `lab/compose.postgres.yml` create (`alerta`):
  `tests/postgres/test_worker_path.py` and
  `tests/performance/test_reference_batches.py` hard-coded the worker's expected
  `database_name` as `postgres`, so `Worker.check_health()` returned false and
  the `worker` and `performance` phases failed. The `pg_lab` fixture now yields
  the observed `current_database()` and both tests use it. Verified on a native
  PostgreSQL 18.3 cluster with database `alerta`: 20 passed, 0 failed, 0 skipped.
- `lab/local_runtime.py` could not start a cluster from a checkout path longer
  than about 90 characters ("Unix-domain socket path is too long"); the socket
  now lives in a short per-checkout directory under the system temp directory.

### Changed (SQL, corrected in place — no tracked operational a1 installation reported)

- `sql/migrations/004_controls.sql`: `msp_worker` no longer receives direct
  `EXECUTE` on `msp.capture_health()`. The worker reads health through
  `msp.worker_call('doctor')` inside the `SECURITY DEFINER` boundary, so the grant
  was unused and wider than design baseline §12 ("claim, completion and
  projection functions only"). `alerta_capture_probe` and `msp_operator` keep
  their grants. `docs/sql_manifest.json` hashes updated.
- `sql/migrations/002_capture.sql`: when the reserved `[msp-ack:` prefix is
  present but the marker does not parse, `msp.captures.marker` is stored as
  `NULL` instead of up to 256 characters of the following text, which could
  include the operator's free-text note excluded by the §5.3/§12 capture
  allowlist. `diagnostic_reason = 'marker_malformed'` and all other columns are
  unchanged; T14 assertions are unaffected.

### Changed (evidence hygiene)

- `tools/evidence_utils.redact_junit` renders JUnit `timestamp` attributes in UTC;
  `tools/run_reference_suite.py` and `tools/native_ci.py` run pytest with
  `TZ=UTC`. Batch evidence records `platform.system()-platform.machine()`
  (`Linux-x86_64`) instead of the kernel/glibc build string. All committed
  evidence regenerated; the public Git bundle commit is dated in UTC.
- `tools/build_acceptance.py` accepts `--native-xml` (repeatable) and
  `tools/update_acceptance.py` passes every executed native phase recorded in
  `evidence/native-run.json`, so `docs/acceptance_matrix.csv` and
  `evidence/acceptance-results.*` show native PASS where a native check ran
  instead of a blanket `BLOCKED_ENVIRONMENT`.
- `evidence/native-*.xml` and `evidence/native-run.json` now contain executed
  results (PostgreSQL 18.3, PgBouncer 1.24.1, four phases, 20 cases).

### Changed (CI and supply chain)

- `.github/workflows/security.yml` installs pip-audit 2.9.0 and its 26
  transitive dependencies from the new `requirements-scanner.lock` with
  `--require-hashes` (hashes for the hosted `ubuntu-24.04` / CPython 3.12 runner;
  the pip-audit wheel hash equals `docs/scanner_reference_manifest.json`), builds
  Gitleaks from the commit recorded in `docs/gitleaks_reference_manifest.json`
  rather than a movable tag, and asserts `gitleaks version` is 8.24.3.
- `.github/workflows/ci.yml` and `release/publish.yml.example` run
  `python -m pip check` after installation to surface `pyproject.toml` /
  `requirements.lock` drift, since Dependabot tracks only the pyproject pins
  (`.github/dependabot.yml` documents this).
- `README.md` and `lab/README.md` use `npm ci … --ignore-scripts`, matching CI.
- `lab/build_postgres.sh` accepts PostgreSQL 18.3 in addition to the 18.0 pin,
  each verified against its published SHA-256, and derives the version from the
  archive name.

### Documentation

- `docs/DESIGN.md` §12 lists the `msp_operator` role and its rights.
- `docs/deployment.md` (G1) records that `005_readiness.sql` grants
  `capture_ack()`/`seal_installation()` to the observed `alerts` owner, so an
  application that connects as that owner can re-seal the readiness manifest;
  a dedicated installation principal is preferred.
- `docs/runbook.md` documents the fail-closed exceptions of `msp.capture_ack()`
  (missing `msp.installation` row or failed capture insert → acknowledgement
  rolled back, HTTP 500) and the repair path.
- `CONTRIBUTING.md` scopes the "never edit migrations" rule to tagged or applied
  migrations, and documents manual lock refresh and evidence regeneration.
- `lab/README.md` replaces the sandbox-specific paragraph with the actual
  runtime requirements (second OS identity, socket path, accepted 18.x archives).
- `docs/RELEASE_AMENDMENTS.md` R3 records every change above with its reason.

### Unchanged by decision (owner items, not defects)

- The ten publication inputs in `config/publication.pending.json`, `SECURITY.md`
  and `CODE_OF_CONDUCT.md` contacts, `[project.urls]`.
- `msp` schema, `msp_*` role and `[msp-ack:v1` marker names.
- Per-file licence headers; committed evidence remains in the repository.
- `requirements.lock` / `requirements-runtime.lock` stay version-pinned without
  `--hash` lines so they remain installable on developer platforms other than
  the CI runner.

### Not executed in this release preparation

- The Docker form of the native job (`tools/native_ci.py`) and the hosted
  `security.yml` (Gitleaks) — both need the private repository's first push.
- Real Zabbix fixture tests, Ansible/Compose execution, external security
  review, deployment gates G1–G8.

## 0.1.0a1 — 2026-09-09

First public alpha candidate, prepared locally and not yet published.

- Package the release-one normalizer, acknowledgement/signing plugins, mandatory
  Alerta privacy adapter, PostgreSQL capture/ledger/lease/health/projection
  functions, worker, reconciler and operational CLI.
- Include versioned SQL, read-only collectors, disabled Zabbix media examples,
  Ansible/systemd deployment, non-destructive rollback and isolated lab tooling.
- Create public documentation with site-neutral examples, Apache-2.0 licensing,
  upstream notices, contributor/governance/security policies and release checks.
- Add native and supplemental CI definitions, dependency inventory, reproducible
  release assembly and a publication-disabled Trusted Publishing template.
- Update build/test pins to fix three reported dependency advisories; record
  the source-backed tooling amendment and final scan separately.
- Preserve design revision 1.2, final-review interpretations and amendment A1.
  Limit advertised Python compatibility to the tested 3.12 series.

See ALPHA_REPORT.md for actual run results and remaining limitations. Production
gates G1–G8 and the independent external security review remain pending.
