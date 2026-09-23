# Dedicated Linux Actions runner

The workflow policy selects one repository-specific native Linux x64 runner
for `steliosx/zab-al-ack` (`[self-hosted, Linux, X64, zabalack]`). Since
2026-09-22 this is `zabalack-omarchy-x64`, an Ubuntu Server 24.04 amd64 QEMU/KVM
guest on the owner's Intel laptop. The former Linux ARM64 VM's runs remain
historical ARM64 evidence. It has no automatic GitHub-hosted fallback. Windows
remains the Codex controller. Configuration and unit tests are preparation;
completion requires an authentic allocated Actions producer-to-package run on
the reviewed candidate, with the run/attempt, PR head/base/tested merge SHA,
architecture, artifact results and Opus technical review retained together.

## Fixed host boundary

| Resource | Required location/identity |
|---|---|
| Service account | `zabalack-runner`, home `/var/lib/zabalack-runner` |
| Runner application | `/opt/zabalack-actions` |
| Repository workspace | `/opt/zabalack-actions/_work/zab-al-ack/zab-al-ack` |
| Job temporary directory | `/opt/zabalack-actions/_work/_temp` |
| Durable private attempts | `/var/lib/zabalack-evidence`, service-owned, mode 0700 |
| Trusted hooks | `/etc/zabalack-actions`, root-owned and not service-writable |
| Shared service-lab lock | `/home/codex/zabalack-task-runs/shared-linux-lab.lock` |

Install only the workflow dependencies and verified runner distribution.
The service account's authorized Docker group membership permits synthetic
local Docker labs; it is a host-privileged capability, not an isolation boundary
against hostile repository code.

## Which revisions may execute here

Because that capability is not an isolation boundary, the control has to be on
*which revisions reach the runner at all*, and it has to sit somewhere a pull
request cannot edit.

**The boundary is repository access.** This is a private repository; a fork of
one requires read access, and a pull request can only come from a principal who
already has it. Verified 2026-09-18: the repository had exactly one
collaborator, `steliosx`, with the `admin` role. Re-verified 2026-09-22: it is
private, workflows from fork pull requests are disabled, the default workflow
token is read-only, and there are two collaborators: `steliosx` (`admin`) and
`Limni` (`write`, added 2026-09-22). The owner has recorded `Limni` as trusted
on the same terms as the owner for this runner. A write collaborator can push
a branch to this repository, and push-triggered workflows run it here with the
runner account's root-equivalent Docker access; the head-repository refusal
below does not cover same-repository pushes. No other principal can open a pull
request or push, so no revision from outside those two can currently be proposed.
Re-verify this list before each runner enablement; any further collaborator is
a new owner decision.

**Before that changes**, and before granting anyone repository access, set
Settings → Actions → General → *Fork pull request workflows from outside
collaborators* to **Require approval for all outside collaborators**, and
restrict the runner group so this runner is offered only to this repository's
reviewed workflows. Both are repository settings; neither is in a file a pull
request can rewrite. Treat adding a collaborator without them as granting that
person the runner account's root-equivalent Docker access, its retained private
attempts and its installed hooks.

`.github/workflows/ci.yml` also refuses, as the first step of every job and
before any checkout, a `pull_request` event whose head repository is not this
one. That is **defence in depth and not the boundary**: the workflow file is
part of the pull request, so a hostile revision could delete the step. It is
worth keeping because it stops an unmodified outside pull request immediately
and makes the requirement visible at the point of use, and
`tests/release/test_ci_collection.py` fails if it is removed or reordered behind
a checkout.

There is still no GitHub-hosted fallback. Untrusted revisions are refused, not
diverted to a paid hosted runner. Keep this repository's runner separate from
other repositories, personal credentials, controller files and direct-agent
editable environments. Do not copy personal GitHub or Codex credentials into
the VM. Use the approved runner enrollment mechanism and retain only the
runner's own protected registration/authentication files.

The root-owned installed hook is a reviewed copy of `tools/self_hosted_job.py`,
not a script executed from whichever PR checkout happens to be present. A hook
update needs a reviewed source revision and installed-file hash recorded in
the host readiness evidence. The runner account can write its private evidence;
these records are cooperative operational retention, not tamper-proof signed
attestations or a sandbox for untrusted code. Hooks and job steps share the same
UID: job code can read, rewrite or delete all retained attempts, including other
jobs' archives and partial files. Mode 0700 restricts other unprivileged users;
it does not isolate the archives from the job. Docker membership also gives job
code root-equivalent control of this VM, including its lab, retention storage
and installed hooks. Root ownership protects against ordinary accidental writes,
not against a hostile job using Docker. Hashes detect inconsistent bytes; they
do not make this same-host diagnostic store tamper-evident. Preserve reviewed
copies outside that host and compare authentic Actions results separately.

## Hook installation contract

Install the reviewed Python script as
`/etc/zabalack-actions/self_hosted_job.py`, root-owned mode 0644. Install two
root-owned executable Bash wrappers outside the runner application directory.
The start wrapper is:

```bash
#!/bin/bash
set -eu
exec /usr/bin/timeout --signal=TERM --kill-after=10s 120s \
  /usr/bin/python3 /etc/zabalack-actions/self_hosted_job.py start
```

The completion wrapper is identical except for its final `complete` argument.
Set these runner environment entries, using the actual installed wrapper names:

```text
ACTIONS_RUNNER_HOOK_JOB_STARTED=/etc/zabalack-actions/job-start.sh
ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/etc/zabalack-actions/job-complete.sh
```

Hooks execute synchronously as `zabalack-runner`. The explicit outer timeout
is required because the runner has no native hook timeout. A nonzero start
hook prevents the job from proceeding. Keep the runner stopped while installing
or replacing its hook configuration; restart it only after path, ownership,
dependency, disk and retention checks pass. The
[official hook contract](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/run-scripts)
provides default Actions variables and the event-file path to both scripts.

The CLI accepts only `start` or `complete`; it has no path overrides. It requires
the dedicated account, exact repository/workspace/temp paths, Linux X64 (`RUNNER_OS=Linux`,
`RUNNER_ARCH=X64`; ARM64 and every other platform are refused) and
real Actions context. Only an allowlist of repository, run, attempt, job, SHA,
event, ref and platform fields is retained. A PR event adds its head, base and
available merge SHA. The raw event, environment, tokens and credential files
are never recorded. Completion separately reads the actual Git checkout SHA;
it does not rewrite Actions metadata when the two differ. Hook completion is
not job success: obtain the result from Actions.

## Retention before cleanup

Each start creates a unique private attempt directory and durable start record.
Completion writes an uncompressed tar and a JSON manifest with its SHA-256,
per-file hashes, allowlisted context and actual checkout SHA. The archive
includes `build/private`, generated evidence, candidates and untracked logs.
It excludes `.git`, dependency trees, virtual environments and known caches.
No archive is uploaded by these hooks.

The next start retains another snapshot before resetting the fixed workspace,
so post-hook changes cannot disappear. If completion was missing, this snapshot
is explicitly `INTERRUPTED`; it never replaces an earlier successful record.
A workspace without a known start is retained as an orphan snapshot. Incomplete
`.tar.partial` files are kept. Private attempts are never automatically deleted.

Workspace ancestors, state files and concrete archive targets must not be
symlinks. Valid internal aliases, including pytest's per-test `*current`
directory links, are retained as symlink metadata without following them.
Their existing target must remain inside the retained workspace, outside
excluded dependency paths, and have no linked ancestors or alias chain.
Absolute internal link text is converted to a canonical relative tar link;
the actual target directories and diagnostic files are retained separately.
Link and target identities are rechecked while archiving. Escaping or dangling
links, special files and hard links fail closed before workspace reset.
Dependency directories are excluded before traversal. The archive is bounded
to 50,000 files and 4 GiB, and free-space checks reserve at least 5 GiB after
the projected archive on the evidence filesystem and require 5 GiB on the
runner filesystem. Bounds, disk exhaustion, path errors or failed archive
writes stop the hook without authorizing workspace deletion. Resolve storage
or unsafe-path failures explicitly; do not disable the guard or erase a failed
attempt to resume.

Only after a verified durable archive does start reset the exact repository
workspace and the job's `venv`/`gitleaks` temporary directories. Workflow steps
then create a fresh environment; no shared editable environment is reused.
The hooks serialize with a separate process-owned private hook lock and keep
its inode. They perform no Docker stop, remove, prune or volume reset.

Neither hook archives or resets a workspace whose lab service data is live or
ambiguously owned (R26). Before anything else, the hook reads the lab PID files
the local native runner writes inside the workspace
(`.lab/pgdata/postmaster.pid`, `build/private/pgbouncer-local/pgbouncer.pid`)
and scans `/proc` for any visible process whose working directory or open file
lies under the workspace, excluding itself and its runner ancestors. A live PID
(`live_lab_service`), an unreadable PID file (`unreadable_lab_pid_file`), a
symlinked PID file, or any such process (`workspace_in_use`) stops the hook
with nothing archived and nothing deleted; the operator stops the process and
the next start retains the state, including a stale PID file, before resetting.
A crashed lab whose process is gone is retained and reset normally.

The runner's own temporary-directory manager cleans the rest of `_work/_temp`
at job initialization and completion; the service unit adds no blanket reset.
In runner v2.337.0, this is best-effort cleanup: deletion errors are logged and
can leave residue. See the pinned
[TempDirectoryManager](https://github.com/actions/runner/blob/v2.337.0/src/Runner.Worker/TempDirectoryManager.cs)
and [JobRunner lifecycle](https://github.com/actions/runner/blob/v2.337.0/src/Runner.Worker/JobRunner.cs).
The hook separately fails closed when resetting its known `venv`/`gitleaks`
paths. It must not delete the whole temp directory during the start hook, when
the current job already needs its event and command files. Do not treat temp
cleanup as hostile-job isolation or reuse leftover temp files as evidence.
Keep required diagnostics under the retained workspace, not only in temp;
investigate a reported cleanup error before assuming a clean environment.

Both native workflow entrypoints hold the shared Linux lab lock for their
complete service/test/normal-cleanup invocation. Direct agent labs must use the
same lock. The lock is taken explicitly in the workflow command, not by the
service unit or hook: `ci.yml` wraps `tools/native_local.py --isolated-lab`
(R26; previously `tools/native_ci.py` with its network-retention arguments) and
`deployment-e2e.yml` wraps `lab/deployment_native_lab.py cycle --isolated-lab`
(R29; previously `tools/run_deployment_e2e.py`) with
`flock -n /home/codex/zabalack-task-runs/shared-linux-lab.lock`.
A stopped hook/process does not prove services stopped; retain the
attempt and inspect exact owned resources before another service lab starts.
Never reset resources belonging to another agent or job.

### Routine native phases without Docker (R26)

The routine `native` job needs no Docker daemon, context or network retention.
It runs `tools/native_local.py` on services installed on the runner host and
named by the workflow step's environment:

| Requirement | Value |
|---|---|
| PostgreSQL 18 | `ACK_LAB_PG_BIN=/usr/lib/postgresql/18/bin` (`pg_ctl`, `initdb`, `psql`, `postgres`) |
| PgBouncer 1.24 or newer | `ACK_LAB_PGBOUNCER_BIN=/usr/sbin/pgbouncer` |
| Reviewed identities | the installed packages (name, exact version, architecture) listed in `lab/native_service_identities.json` |
| Ports | 55432 (cluster) and 56432 (pool) on loopback, free of other services while a job runs |
| Account | `zabalack-runner` runs both services itself; no Docker group membership is needed for this job |

The job creates `.lab/` and `build/private/` inside the workspace and the
hooks archive them; a live service there refuses the next job (above). The
runner records the service identities it used; a packaged executable that is
not reviewed refuses the run, and evidence collection refuses a record whose
services are not reviewed. Fill the reviewed list from the runner host's
provisioning record before expecting a packaged run. Per-phase diagnostics are
preserved under `build/private/retained-phases` before every reset inside the
run (`docs/local-services.md`); the end-of-job archive is in addition to that,
not instead of it. The Compose form and its network retirement below remain
available for the deployment lab and for local Docker hosts.

### Deployment path without Docker (R29, prepared)

The manual `deployment-e2e.yml` job runs `lab/deployment_native_lab.py cycle`
on services installed on the runner host. Nothing below is provisioned by the
repository; each row is a verification item for the runner host's handoff,
checked with `python lab/deployment_native_lab.py plan` and `state` before
the job is enabled:

| Requirement | Value |
|---|---|
| PostgreSQL 18 | `ACK_LAB_PG_BIN=/usr/lib/postgresql/18/bin`; **no other cluster on loopback port 5432** (a packaged default cluster makes the lab state ambiguous and the job refuses) |
| Zabbix at the pinned patch | `zabbix-server-pgsql`, `zabbix-frontend-php` and `zabbix-sql-scripts` at the version in `lab/zabbix_reference.json` (`/usr/sbin/zabbix_server`, `/usr/share/zabbix`, `/usr/share/zabbix-sql-scripts/postgresql/server.sql.gz`); identities recorded, not assumed |
| PHP | `ZABALACK_PHP=/usr/bin/php8.3` with the frontend's extensions; the frontend is served by PHP's built-in server on loopback 8080 |
| Hostnames | `postgres`, `zabbix-web` and `runner` resolving to loopback in `/etc/hosts` |
| Checkout link | `/opt/zabalack-lab` resolving to the job workspace checkout (a fixed link to the runner's `_work/<repo>/<repo>` directory) |
| Private directory | `/run/zabalack-e2e`, mode 0700, owned by the runner account, holding `pg-password` (`/run` is volatile: a `tmpfiles.d` rule or the service's pre-start recreates it) |
| Ports | 5432, 8080, 20051 and 55434 (lab services) and 58081/58443 (application and TLS proxy) free on loopback while the job runs |
| Account | the runner account runs every service itself; no Docker group membership is needed for this job |
| Build tooling | `build`, `setuptools` and `wheel` from `requirements.lock` build the job's wheel in `$RUNNER_TEMP/dist` |

The job creates `.deployment-lab/`, `.zabbix-lab/` and `build/private/` inside
the workspace; the hooks archive them and a live service there refuses the next
job (above). The Compose form remains available on a disposable local Docker
host and is not run by any workflow.

Package identities are collected on the host, never assumed. As the runner
account, read-only:

```bash
python tools/collect_package_identities.py collect --output /path/private/package-identities.json --label x64-runner
python tools/collect_package_identities.py validate /path/private/package-identities.json
```

`validate` reports CONFORMS, DEVIATES (a source build, an origin other than
`repo.zabbix.com`, `apt.postgresql.org` or the Ubuntu archive, an InRelease
signature that does not match the expected key, changed package files, or a
version outside the pin and the policy) or INCOMPLETE (a missing observation;
never a pass). After review, `approve --reviewer … --amendment … --output …`
writes the reviewed entries as a separate proposal; the reviewer copies them
into `lab/native_service_identities.json` (`reviewed`) and
`lab/zabbix_reference.json` and records the review in
`docs/RELEASE_AMENDMENTS.md`. The tool edits neither file.

### Successful native phase network retirement (Compose form only)

`native_ci.py` retains networks by default. Repeated fresh projects can exhaust
Docker's address pools even when every container is stopped. The explicit
`--retire-empty-networks --network-evidence-root /absolute/private/durable/path`
option preserves each successful phase before retiring that phase's network.
Keep the shared host lock for the entire invocation. The evidence directory
must belong to the executing account, have mode 0700, and be outside the
checkout, `_work` and runner temp. Its ancestors must be owned by root or that
account and not writable by other users (root-owned sticky ancestors excepted).
Use persistent storage; this directory is separate from hook snapshots and
must be included in private evidence backups. No records are uploaded.
The native `ci.yml` job explicitly selects this option with
`/var/lib/zabalack-evidence/native-networks`; the existing dedicated runner
account creates it beneath its private evidence root. Deployment labs do not
select it. No runner service or trusted hook installation changes are needed.

The runner captures full container/network IDs and volume metadata while the
two owned services are running. Retirement requires complete passing JUnit,
unchanged source/harness, successful relay shutdown and successful Compose
stop. It rejects active endpoints, replaced resources, additional mounts or
owned resources, and foreign containers referencing the network, including
stopped containers. Failed or interrupted phases retain their network.

Before the sole `docker network rm <full-id>` operation, a unique mode-0700
attempt preserves mode-0600 raw JUnit/pytest logs, both container log streams,
full Docker inventories, the source/harness binding and exact removal intent.
Writes and directories are fsynced and hashes rechecked. Individual diagnostics
and inspection responses are limited to 64 MiB; oversized or incomplete
retention fails the phase without authorizing removal. The complete inventory
and code binding are checked again before mutation. A final hashed manifest
binds the removal output and after-inventory; the public phase report contains
only its digest, status and preservation booleans.

Containers, volumes and other networks must remain unchanged. Stopped
containers remain forensic records but cannot simply restart against the
retired network; use a fresh project for subsequent execution. These checks
assume cooperative users honor the shared lock, not hostile Docker-group
members. A removal timeout, interrupted command or unexpected post-state is
an incomplete attempt with an unknown outcome, never an automatic retry or
an inferred pass. Preserve its partial records and inspect it explicitly.

This option does not discover or remove older projects and cannot start a
first phase when the address pool is already exhausted. Any initial capacity
recovery requires a separate exact-resource evidence and ownership check.
Runner hooks, deployment workflows and failed attempts remain unchanged.

## Runner account verification (prepared, not applied)

`tools/runner_account_check.py` is a read-only report of the dedicated runner's
host capabilities for the Docker-removal handoff (R28,
`docs/docker_requirements_inventory.md`). It distinguishes the *configured*
account (uid, gid, configured groups) from the *running* runner processes: every
process in the exact unit's cgroup (or `--runner-pid`, or the `Runner.Listener`
name only when no unit cgroup can be read), whose four uid and four gid fields,
supplementary groups, capability sets and `no_new_privs` flag it reads from
`/proc`. Candidates that differ in any of those fields are an ambiguous identity
and fail closed; the account matches only when every uid field equals its uid
and every gid field its gid. For each container socket (a symlink is resolved
and its target evaluated; a dangling or non-socket target is a failed
observation) it records the mode, owner, group and POSIX ACL, the permission
*inferred* for the runner identity, whether a systemd `.socket` unit owns the
path, and the access *demonstrated* by connecting under that identity in a
child process (uid, gid and groups switched; nothing is sent). While
`docker.socket` or `podman.socket` is active for the path no connect is
attempted, because a permitted connect would start the daemon; the inferred
permission decides then. A connect that ends neither in success, a permission
denial nor "no listener" (for example EAGAIN on a full accept queue) is
NOT_OBSERVED, never "no access". Under `docker-free`, inferred write permission,
a successful connect and a permission that exists without a daemon are each a
deviation. The service configuration is the effective one from `systemctl show`
of the exact unit named by `--unit` (transient `systemd-run` units under
`/run/systemd/transient` included; globs are refused): fragment, drop-ins,
`User`/`Group`/`SupplementaryGroups`, `Environment=`, `EnvironmentFiles=`, the
control group, plus the runner's `.env` (from `--runner-root` or the unit's
`WorkingDirectory`) and the running process's environment, keeping only
`DOCKER_*` keys. The intended account's Docker configuration (`DOCKER_CONFIG`
from the effective service environment, else its `~/.docker`) is read as files:
`currentContext`, the number of stored auths and the context store's endpoints;
malformed metadata is a failed observation; no Docker CLI runs and no daemon or
remote endpoint is contacted. Sudo rules are listed as root.

A required observation that cannot be made (no root for sudo or identity
switching, an unreadable file, an absent or ambiguous runner process, an
unclassifiable connect, an unknown socket-activation state) is reported as
NOT_OBSERVED and makes the verdict INCOMPLETE; it never becomes "absent" and
CONFORMS. Any deviation makes it DEVIATES, with the incomplete observations
listed alongside. `--policy docker-allowed` describes the host as provisioned
today; `--policy docker-free` is the target after routine CI runs the local
native form. Exit 0 conforms, 1 deviates, 2 incomplete. The report contains no
secrets: no environment dump, no auth values, no tokens.

```bash
sudo python3 tools/runner_account_check.py --unit actions.runner.zabalack-attended --policy docker-allowed   # before the change; runner running
sudo python3 tools/runner_account_check.py --unit actions.runner.zabalack-attended --policy docker-free      # after removing the docker group membership
```

Run it as root while the runner service is running; a stopped runner yields
INCOMPLETE because the running identity cannot be verified. The operational
change itself (removing `zabalack-runner` from the `docker` group or removing
Docker from the guest, then rewording the "root-equivalent Docker access"
statements) is a separately reviewed host step. Nothing in the repository
performs it.

## Validation and policy limits

Run the hook filesystem regressions locally with injected temporary roots;
those fixtures are explicitly synthetic and do not set Actions variables to
manufacture CI evidence. Validate clean dependency installation and source
bindings separately, using `tools/run_review.py` for local work. Then inspect
real Actions allocation, the unchanged producer requirements and package's
same-run artifact download/collection on the reviewed commit.

Actions artifacts retain their existing allowlists. All active artifact uploads
now request three days: Alpha (including candidate packages) and deployment-lab
uploads previously requested 14, while security used the repository default.
This deliberately limits hosted artifact storage and is separate from the VM's
disk-retention policy. Reviewers must retain required candidate bytes before
that expiry.
Check actual storage against the free allowance and keep required private
copies outside disposable workspaces; retention duration alone is not a
storage-budget guarantee. Since the 2026-09-22 migration, all jobs, including
package, produce native x64 execution evidence; earlier jobs produced ARM64
evidence and remain labelled as such. Routine x64 CI is not by itself the target
x86_64 acceptance gate, which needs the frozen candidate's exact-artifact and
installed-environment results in their actual scope. CodeQL enablement and
ingestion, the separate daily scan, independent review, installed-environment
acceptance and G1–G8/A5/T40/T41/T42 retain their existing requirements. The
inactive signed-attestation template and its hosted-signer verifier remain a
separate owner policy decision in `release-provenance.md`.
