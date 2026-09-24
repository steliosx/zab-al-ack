# Deployment-path service lab

The Docker form of this opt-in lab is designed to exercise the installed wheel
through ordinary application and worker processes. The historical Ackline
0.1.0a8 report recorded all four service cases on local processes without
Docker from an editable source installation (`docs/RELEASE_AMENDMENTS.md` R12);
that result did not establish the packaged-wheel service path. 0.1.0a10 supplies
the local runner (`tools/run_deployment_e2e_local.py`, `docs/local-services.md`,
R14) and records the four cases from a runner environment in which the built
wheel is installed non-editably, with the installed bytes verified against the
wheel; that historical run did not execute the Docker Compose form below. Offline
orchestration tests are separate and establish no PostgreSQL authentication,
Zabbix permissions, notification or macro-expansion result.

Before export, `tools/deployment_evidence.py` checks the reported case counts,
observations, cleanup, versions, runtime identities and source binding. The same
check runs during archive verification and before the Docker runner records
PASS. This is evidence consistency, not proof of authorship. See
`docs/INDEPENDENT_A8_REVIEW.md` for the reproduced failure and remaining scope.

Two behaviours of a real Zabbix 7.0.10 that the first execution exposed are now
built into the lab: an imported media-type script is stored with CRLF line
terminators (the shipped script is compared with normalized terminators), and
the server evaluates actions from a configuration cache refreshed every
`CacheUpdateFrequency` seconds, so the lab waits a bounded grace after creating
the source action before raising the problem. Run the server with a clean
environment: libcurl inside `zabbix_server` honours `HTTPS_PROXY`.

On a development host with a local Unix-socket Docker daemon and Compose:

```bash
python tools/run_deployment_e2e.py --isolated-lab --plan
python tools/run_deployment_e2e.py --isolated-lab
```

To qualify a previously produced wheel without rebuilding it, supply both its
path and independently obtained SHA-256:

```bash
python tools/run_deployment_e2e.py --isolated-lab \
  --wheel /private/artifacts/zabalack-0.1.0a18-py3-none-any.whl \
  --wheel-sha256 "$EXPECTED_WHEEL_SHA256"
```

This mode checks the archive hash, layout, project/version and complete package
payload against this checkout before contacting Docker. The image contains no
`src` directory and installs only that wheel, without dependency resolution or
a fallback build. It verifies installed package files, metadata, entry-point
targets, version and import origin both at image build and immediately before
service tests. A PASS requires the matching installed-wheel receipt. The public
record adds `artifact_mode: supplied_wheel` and the wheel filename, version,
SHA-256 and installed-byte verification result; raw receipts stay private.
Bind the artifact's producer revision/provenance separately from this runner's
source/harness digests. A changed harness does not transfer old qualification
results to its new revision. Legacy source-build/local records remain scoped
to their original execution and do not imply this exact-artifact mode.

The runner builds a Python 3.12.10 image from `requirements.lock` and a wheel from
the current source by default. Services use PostgreSQL 18.3, PostgreSQL 16.4 for the source
server, and the Zabbix server/web images at the pinned patch (`lab/zabbix_reference.json`). Version tags are explicit; a successful
run records the actual Docker image IDs. Image pulls/build dependency downloads
need ordinary registry access. The runtime network is a new internal Compose
network with **no published ports**, host networking, Docker-socket mount or
external endpoint input. A random project name and fresh named volumes avoid
reusing an existing service. An inherited remote Docker context is refused.

The application runs the installed `zabalack.plugins:create_app()` factory through
the Flask CLI HTTPS server. This is a real HTTP process and upstream factory,
with the actual registered plugin entry points and file-based configuration;
it is a lab server, not a claim about a production Gunicorn/systemd deployment.
No test client, injected JWT/actor, injected health probe, patched persistence or
runtime `SET ROLE` facade is used. The worker runs the installed `zabalack` CLI
in another process. Its source endpoint is a fixed HTTPS loopback proxy to only
this project's Zabbix API; the production HTTP client verifies a generated lab
certificate. No source URL or token is accepted from a caller fixture.

The four service cases require these observations:

| Case | Required observations |
| --- | --- |
| Installed authenticated path | Real signup/login; human `/action`; one committed capture; one source acknowledge/comment; successful projection; repeated action returns HTTP 409. |
| Customer and actor scope | A separately logged-in outside customer cannot read/acknowledge the alert; a customer-bound ingestion key can create/local-acknowledge a separate alert but produces no capture. |
| Native privileges | Distinct password-authenticated app, worker, probe and operator sessions; direct worker table update and probe table read fail under their real logins. |
| Media and updates | Import the shipped disabled media artifact unchanged; configure only private lab parameters; trigger a real problem via `history.push`; require macro-expanded ingestion; observe a completed update webhook and no duplicate ingestion for the neutral write-back comment; require a human source comment to ingest; recover the problem and preserve its original problem ID alongside the distinct recovery ID. |

The Zabbix worker user has a single synthetic host group and API method allowlist.
The action matches exactly one generated trigger and one private webhook
recipient. It contains no email or command operation. A completed source alert
record is required before treating a suppressed update as evidence: silence or
a failed delivery does not pass. Credentials are generated privately; the
Zabbix default account is used only to provision its new disposable instance.
Existing enabled source actions or an existing synthetic host cause refusal.

`evidence/deployment-e2e-run.json` is invalidated before daemon discovery, bound
to source/harness digests before and after execution, and can become PASS only
when all four exact cases and every required observation pass, image identities
are recorded (or, for the local runner, `not_docker: true` with component and
runner identities), the tree is unchanged, and cleanup succeeds. Exit 2 means NOT_RUN
because the local runtime is unavailable/refused; exit 1 means execution,
validation or cleanup failed. Public output contains fixed check names, booleans,
counts and reference versions only. Service logs, generated credentials and raw
JUnit remain under the private generated directory in `build/private/`; they
are excluded from source/release exports and must not be uploaded as CI artifacts.

Cleanup stops the exact generated runner container after checking its project
label, service label and full name, including after a timeout. It then stops only
that generated Compose project. It retains volumes and private logs for local
inspection; it never resets acknowledged events or deletes database volumes.
The generated `compose.json` identifies those resources for an engineer's
separately chosen local cleanup. Do not share that file or its private directory.
The source DB password is mounted as one read-only file readable by the pinned
Zabbix image's UID 1997; its host parent is mode 0700. Application keys and other
DSNs are not mounted into Zabbix containers.
The runner container uses the numeric UID and GID of its generated private
directory. This lets the installed application read owner-only bootstrap and
secret files and leaves generated mode-0600 results readable by the host owner.
Protected-file ownership checks and modes remain unchanged; the other service
containers retain their pinned images' identities.
The generated nonsecret build context recursively normalizes copied directories
to mode 0755 and files to mode 0644, including when source permissions or the
host umask are restrictive. The runner can then traverse and read the copied
source after Docker assigns it root ownership. Source permissions remain
unchanged, and the enclosing private directory stays mode 0700.

Two reference limitations are explicit:

- Pinned Alerta 9.1.0 runs schema ALTER statements on every factory startup. The
  lab application therefore logs in as the actual Alerta table owner, a trusted
  installation identity. Zab-al-ack's non-login migration owner and restricted
  worker/probe/operator remain separate. This does not prove a non-owner Alerta
  deployment; `docs/deployment.md` describes the existing ownership gate.
- The pinned Zabbix webhook `HttpRequest` disables peer/host certificate
  verification and follows redirects. HTTPS ingestion in this isolated lab
  therefore **does not prove authenticated TLS**. See amendment A2 in
  `docs/SPEC_AMENDMENTS.md`. Worker-side HTTPS uses its independent verified
  client. Production transport and redirect containment remain open inputs.

This suite does not establish installed customer/cohort permissions, production
notification behavior, deployment topology, PgBouncer, multi-process Gunicorn,
Ansible/systemd installation, UI behavior, HA or G1–G8. The separate native lab
covers its own PostgreSQL/PgBouncer cases. A successful service lab run supplies
reference evidence only; the installed-environment gates stay open.

Inspected interfaces: Alerta 9.1.0 `auth/basic.py`, `views/keys.py`,
`database/base.py`, `database/backends/postgres/base.py` and `sql/schema.sql`;
Zabbix 7.0.10 commit `237358b56b2584b94b1f8358f479ade669693ba2`
`CUser`, `CUserGroup`, `CRole`, `CToken`, `CAction`, `CMediatype`,
`CConfiguration`, `CHistory`, `CAlert` and `CEvent`; official Zabbix Docker
7.0.10 commit `8593930481428e518ac239a785629fb574b88a46`
server/web Dockerfiles and entrypoints. No interface was inferred from a mock's
successful return alone.

## Native form on one host (R29)

`lab/deployment_native_lab.py` supplies the two services the local runner
needs from artifacts installed on the host, without Docker, and keeps them
disposable: a dedicated PostgreSQL 18 cluster under `.deployment-lab/pgdata`
on loopback port 5432 (`lab_admin`, `scram-sha-256` host logins, database
`alerta`) and the disposable Zabbix instance of `lab/zabbix_native_lab.py`
(R27) with its frontend on port 8080. The host layout the runtime fixes
(`postgres`, `zabbix-web` and `runner` resolving to loopback,
`/opt/zabalack-lab` resolving to the checkout, `/run/zabalack-e2e` mode 0700
owned by the running user) is verified, never changed; `plan` prints what is
missing. `docs/local-services.md` lists the commands.

```bash
python -m build --no-isolation --wheel --outdir /path/dist
python lab/deployment_native_lab.py prepare-runner --isolated-lab \
  --wheel /path/dist/zabalack-<version>-py3-none-any.whl --venv /path/e2e-runner
ACK_LAB_PG_BIN=/usr/lib/postgresql/18/bin python lab/deployment_native_lab.py cycle --isolated-lab \
  --runner-python /path/e2e-runner/bin/python --wheel /path/dist/zabalack-<version>-py3-none-any.whl \
  --wheel-sha256 "$EXPECTED_WHEEL_SHA256"
```

`cycle` refuses unless the lab is absent, then starts both services, writes
the services file, runs `tools/run_deployment_e2e_local.py` with the
orchestrator's own `reset` as the reset command and the supplied-wheel
identity, and stops and wipes both services after every outcome, including a
refusal or an interruption. The run's exit code is reported unchanged (2 is
NOT_RUN, 1 a failed execution or validation); a run that passed but left a
component running or a lab directory behind exits 5. Inside a cycle the runner
never writes PASS: after validating its execution evidence it writes
`PENDING_BINDING` (`provisional_status: PASS`, `native_lab.pending`). Only the
cycle, once the run exited 0 and cleanup, retention and the wipe left the lab
absent, binds its outcome, sets PASS and validates that final record with
`tools/deployment_evidence.py`; a failure there is FAIL
`final_evidence_validation_failed` and a non-zero exit. A cycle cut short
before binding leaves `PENDING_BINDING`, which never validates. The final record
is committed once. The cancellation cutoff is the last check for SIGTERM,
SIGHUP or SIGINT, made with those signals blocked; the commit runs from the
cutoff, through the atomic replace that publishes the record, until the signals
are unblocked. A signal received before the cutoff makes the outcome an
interrupted FAIL with exit 130, even after a passing run and a complete cleanup.
A signal received during the commit is held; the committed record stands and
the command exits 130, saying on stderr that it came during the final commit. A
signal after the commit likewise exits 130 with its own message. A death before
the replace completes, including one after the cutoff, leaves the runner's
`PENDING_BINDING`, which never validates; a death after it leaves the published
record. A failed replace publishes nothing, removes the unpublished temporary
file and exits 1. As soon as the run has
ended, before any cleanup, the cycle retains the public record exactly as the
runner left it (its original `PENDING_BINDING`, or its FAIL), privately under
`build/private/retained-phases/<run>/runner-record/`, whatever cleanup then
does: a failed stop or an interruption keeps it too, still ending FAIL with the
lab left unwiped. If that copy cannot be made, the runner's record is
left exactly as it is as the canonical record (a `PENDING_BINDING` never
validates), nothing is wiped, and the outcome is written instead to
`evidence/deployment-e2e-run.retention-failure.json`: a terminal diagnostic
(`result: NOT_A_RESULT`) naming the canonical record's digest and this run's
identity. A later command first retains that unresolved original and its
diagnostic, byte for byte and verified by digest, under
`build/private/retained-phases/<previous run>/unresolved-previous-attempt/`
(bound into its own record as `native_lab.previous_attempt`); only then does it
replace them. If that copy cannot be made, the command refuses (`REFUSED`,
exit 2) with both files unchanged and nothing started, stopped or wiped. A
`PENDING_BINDING` record left without a diagnostic (the diagnostic write itself
failed) is treated the same way, and filesystem errors while retaining (for
example a retention parent that is not a directory) take the same protected
path as a refused copy. Both labs' service logs are retained (`…/deployment-cycle/`) only after
every service has stopped, before the wipe; a lab that did not stop is not
snapshotted. Both retention manifests' digests are bound into the final record. With `--wheel-sha256`
the local runner performs the same supplied-wheel preflight as the Compose
form (exact bytes, project identity, payload equal to the checkout) and the
record carries `artifact_mode: supplied_wheel` with the installed-bytes
verification result.

For a hosted isolated run, the supplied `deployment-e2e.yml` workflow is manual
and requires `ZABALACK_DEPLOYMENT_E2E_ENABLED=true` after repository policy review.
It uses no custom secrets, has read-only repository and Actions permissions,
and uploads only the fixed public result JSON even on failure; its first step
after the checkout replaces the checkout's tracked historical record with a
NOT_RUN record, so a failure before the cycle can never upload a stale PASS. Since R29 it runs
the native form on the exact candidate wheel: the dispatcher supplies the
candidate's CI run ID, its full revision and the wheel's SHA-256; the job is
dispatched on that revision (`git rev-parse HEAD` must equal it), confirms
through the Actions API that the run is a completed, successful `ci.yml` run of
that revision triggered by `workflow_dispatch`, `push` or `schedule` (a
`pull_request` run builds a merge commit, not the named revision),
downloads that run's `zabalack-candidate-*` artifact, verifies the wheel's
digest with `sha256sum -c` before anything installs it, prepares the runner
environment from that wheel only, and runs `lab/deployment_native_lab.py cycle
--isolated-lab --wheel-sha256 "$WHEEL_SHA256"` under the shared host lab lock
with the installed services named by the step environment; nothing is built in
the job and no Docker daemon is involved. Inside the runner interpreter
`tools/supplied_wheel.check_installed`, run as `python -I -S` so no installed
`.pth` runs first, compares the whole installation with the wheel (every
member's bytes, the package and dist-info inventories, no compiled bytecode in
the package, which `prepare-runner` installs with `--no-compile`, and no other
entry named after the project) and the rest of the environment (only the
distributions `requirements.lock` pins, plus pip; every hashed RECORD entry
equal to its file; no top-level entry that no RECORD owns; no
`sitecustomize`/`usercustomize` in any form; a `.pth` only when a verified
RECORD entry owns it). What is anchored: the supplied wheel's own files are
compared byte for byte with the wheel whose digest the dispatcher supplied
(`installed_bytes_verified`), and the only startup hook allowed is
setuptools' `distutils-precedence.pth` with the exact bytes of the setuptools
83.0.0 wheel that `requirements.lock` pins. What is not anchored: dependency
files are checked only against their installed RECORD, which pip writes into
the same site-packages; `requirements.lock` carries no hashes, so a dependency
file changed together with its RECORD is not detected. The record states this
(`runner_environment`). The job creates that environment fresh in its own
temporary directory. A missing prerequisite (services,
layout, packages at the pinned patch) fails the step: NOT_RUN is exit 2, never
a passing job. It is separate from the required
quality/reference/native/mutation packaging workflow; a service result is not
silently imported into that workflow's acceptance evidence. Hosted execution and
repository enablement are NOT_RUN here; the Compose form remains available on a
disposable local Docker host.
