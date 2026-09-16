# Dedicated Linux Actions runner

The reviewed workflow policy selects one repository-specific Linux ARM64 runner
for `steliosx/zab-al-ack`. It has no automatic GitHub-hosted fallback. Windows
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
against hostile repository code. Keep this repository's runner separate from
other repositories, personal credentials, controller files and direct-agent
editable environments. Do not copy personal GitHub or Codex credentials into
the VM. Use the approved runner enrollment mechanism and retain only the
runner's own protected registration/authentication files.

The root-owned installed hook is a reviewed copy of `tools/self_hosted_job.py`,
not a script executed from whichever PR checkout happens to be present. A hook
update needs a reviewed source revision and installed-file hash recorded in
the host readiness evidence. The runner account can write its private evidence;
these records are cooperative operational retention, not tamper-proof signed
attestations or a sandbox for untrusted code.

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
the dedicated account, exact repository/workspace/temp paths, Linux ARM64 and
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

Path ancestors, retained entries and state files must not be symlinks. The
archive reader refuses special files and hard links and never follows links.
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

Both native workflow entrypoints hold the shared Linux lab lock for their
complete service/test/normal-cleanup invocation. Direct agent labs must use the
same lock. A stopped hook/process does not prove containers stopped; retain the
attempt and inspect exact owned resources before another service lab starts.
Never reset resources belonging to another agent or job.

## Validation and policy limits

Run the hook filesystem regressions locally with injected temporary roots;
those fixtures are explicitly synthetic and do not set Actions variables to
manufacture CI evidence. Validate clean dependency installation and source
bindings separately, using `tools/run_review.py` for local work. Then inspect
real Actions allocation, the unchanged producer requirements and package's
same-run artifact download/collection on the reviewed commit.

Actions artifacts retain their existing allowlists with three-day retention.
Check actual storage against the free allowance and keep required private
copies outside disposable workspaces; retention duration alone is not a
storage-budget guarantee. ARM64 is distinct from x64. CodeQL enablement and
ingestion, the separate daily scan, independent review, installed-environment
acceptance and G1–G8/A5/T40/T41/T42 retain their existing requirements. The
inactive signed-attestation template and its hosted-signer verifier remain a
separate owner policy decision in `release-provenance.md`.
