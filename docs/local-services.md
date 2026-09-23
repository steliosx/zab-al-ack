# Service suites without Docker (R14)

`tools/native_ci.py` and `tools/run_deployment_e2e.py` are the Compose forms of
the native PostgreSQL/PgBouncer phases and the deployment-path lab. Where no
local Docker daemon exists, the two local runners execute the same tests on
services the operator supplies on one host. Both are isolated-lab tools: they
must never be pointed at a production database, Zabbix server or credential.

Their records are labelled `not_docker: true`, name the local services and are
validated exactly like the Compose records. They establish the installed-wheel
service path on the named local services, not the Compose image path.

## Native phases: `tools/native_local.py`

Inputs (environment): `ACK_LAB_PG_BIN`, a directory with PostgreSQL 18
`pg_ctl`, `initdb`, `psql` and `postgres` (build with `lab/build_postgres.sh` or
install a packaged 18.x); `ACK_LAB_PGBOUNCER_BIN`, a PgBouncer **1.24 or newer**
executable. The version policy and the reviewed package identities live in
`lab/native_service_identities.json` (see *Service identities* below).
When run as root, both services start as the unprivileged `nobody` user, as
`lab/local_runtime.py` does. The runner writes only under `.lab/`, `build/` and
`evidence/` in the checkout.

```bash
ACK_LAB_PG_BIN=/path/to/pg18/bin ACK_LAB_PGBOUNCER_BIN=/path/to/pgbouncer \
  .venv/bin/python tools/native_local.py --isolated-lab --plan
ACK_LAB_PG_BIN=/path/to/pg18/bin ACK_LAB_PGBOUNCER_BIN=/path/to/pgbouncer \
  .venv/bin/python tools/native_local.py --isolated-lab
```

Each phase starts a fresh cluster on loopback port 55432 with database `alerta`,
starts PgBouncer on loopback port 56432 from `lab/pgbouncer.ini` and
`lab/pgbouncer_users.txt` (synthetic trust authentication; never copy it to
production), runs the phase's pytest targets with the same `ACK_TEST_*`
environment as `tools/native_ci.py`, redacts the JUnit into `evidence/`, stops
PgBouncer and the cluster, and records the cleanup exit code. A missing input
records NOT_RUN (exit 2); a failed phase stops the run (exit 1).

Since R26 this form is also what the routine Actions `native` job executes on
the dedicated runner's installed services; the Compose form stays available for
a local host with Docker.

### Lifecycle, refusals and retention (R26)

Before any destructive step the runner asks `lab/local_runtime.py state` (a
read-only command that needs no `--isolated-lab` flag) to classify the
checkout's `.lab` directory:

| State | Meaning | Runner action |
|---|---|---|
| `absent` | no `.lab`, nothing on port 55432 | start a fresh cluster |
| `stopped` | marked lab, `pg_ctl status` reports no server, port free | preserve its logs, remove `.lab`, start fresh |
| `running` | marked, initialised lab with a live postmaster | `pg_ctl stop`, re-inspect; only a verified `stopped` result continues |
| `unmarked` | a `.lab` directory without `ISOLATED_ACK_LAB` | refuse: the phase fails, nothing is touched |
| `ambiguous` | inaccessible data directory, an unclassifiable PID file, or a listener on port 55432 that is not the marked cluster | refuse: the phase fails, nothing is touched |

A stop that leaves anything other than `stopped` (a non-zero `pg_ctl`, a
still-running server or an ambiguous result) also refuses the reset. The
refusal is recorded as the phase's `reason` (for example
`LabStateError: lab_state_unmarked_reset_refused`) with `stage: lab-reset`.
Resolve it by hand: inspect the directory, stop whatever owns it, and only then
remove it yourself. The runner never deletes what it did not declare. PgBouncer
is guarded the same way: a live PID in `build/private/pgbouncer-local` or a
listener on port 56432 refuses the phase before any file is replaced.

Every destructive step is preceded by preservation through
`tools/lab_retention.py` into `build/private/retained-phases/<run id>/<label>/`
(mode 0700, private, never exported):

- `previous-run` — before this run invalidates an earlier run's
  `evidence/native-run.json`, `evidence/native-*.xml` and raw logs, including
  the exception logs and the lab/pool logs an interrupted run left behind;
- `pre-reset-<phase>` — the lab's `postgres.log` and `initdb.log` before `.lab`
  is removed;
- `phase-<phase>` — the raw JUnit, pytest log, exception log and the phase's
  own `pgbouncer.log` (one log per phase, no longer appended across runs).

Each copy is verified by digest and listed in a `manifest.json`; the public
record carries only the directory, counts and manifest digest. A preservation
failure is a refusal: at run start nothing is invalidated and NOT_RUN (exit 2)
is printed without rewriting the previous report; before a reset the phase
fails and the directory stays; at a phase end a passing phase becomes FAIL
(`retention.status: FAILED`) and the run stops before the next reset.
Retained copies are never deleted by the runner; on the dedicated runner the
job hook archives them with the workspace, elsewhere the operator prunes
`build/private/retained-phases` deliberately. End-of-job retention is not a
substitute for this: a file the run itself deleted between phases would be gone
before any end-of-job archive ran.

PgBouncer and PostgreSQL are cleaned up independently. The phase record's
`cleanup` names both outcomes (`STOPPED`, `NOT_STARTED`, `FAILED` with the error
type, or `REFUSED` with the observed state); `shutdown_exit_code` is 0 only when
both succeeded and `shutdown_reason` names the failed services. The phase's own
`reason` (a failed test run, a refused reset) is never replaced by a cleanup
outcome, and a failing PgBouncer stop never skips the cluster stop.

### Service identities

The record's `services` section names, for `postgres` and `pgbouncer`, the
version string, the identity of the executable actually used and its review
status:

- `{"kind": "dpkg", "package", "version", "architecture", "sha256"}` when
  `dpkg -S` attributes the executable to an installed package;
- `{"kind": "unpackaged", "sha256"}` for a source build.

`lab/native_service_identities.json` fixes the policy (PostgreSQL major 18;
PgBouncer 1.24 or newer) and lists the reviewed identities. A packaged
executable that is not listed refuses the run before any service starts
(`postgres_package_not_reviewed …`, NOT_RUN). A source build is labelled
`UNREVIEWED` unless a reviewed `binary` digest matches. CI evidence collection
accepts a `not_docker` native record only when both services are `REVIEWED`
(`Local native service identities are not reviewed` otherwise), so a runner
host's packages must be reviewed and recorded before its run can be packaged.
Add entries only from a reviewed provisioning record; never edit an entry to
match an observed host.

## Deployment-path lab: `tools/run_deployment_e2e_local.py`

`lab/deployment_e2e_runtime.py` fixes the Compose hostnames, ports and private
directory, and the local runner executes it unmodified. The operator supplies:

| Input | Requirement |
|---|---|
| PostgreSQL 18 | reachable as host `postgres`, port 5432; superuser `lab_admin` with its password in `/run/zabalack-e2e/pg-password`; host logins password-authenticated (`scram-sha-256`); database `alerta` empty before the run |
| Zabbix at the pinned patch (`lab/zabbix_reference.json`, R27) | a fresh disposable instance: `zabbix_server` running with a clean environment (libcurl honours `HTTPS_PROXY`), its frontend reachable as `http://zabbix-web:8080` with the default `Admin` account, no enabled source action and no synthetic host; the provisioner refuses any other `apiinfo.version` |
| Hostnames | `postgres`, `zabbix-web` and `runner` resolving to loopback (for example in `/etc/hosts`) |
| Checkout link | `/opt/zabalack-lab` resolving to the checkout under test |
| Private directory | `/run/zabalack-e2e`, mode 0700, containing `pg-password`; the runner generates and replaces `bootstrap.json`, the lab certificate and every other file there |
| Runner environment | a Python 3.12 environment with `requirements.lock` and the built wheel installed **non-editably** (`pip install --no-deps dist/pypi/zabalack-<version>-py3-none-any.whl`) |
| `--services-file` | JSON naming exactly `postgres`, `zabbix-db`, `zabbix-server` and `zabbix-web` (build source, version, authentication mode); the `runner` identity is generated |
| `--reset-command` | optional operator command that returns the Zabbix instance and the `alerta` database to the fresh state above before the run |

```bash
.venv/bin/python tools/build_release.py                       # or any build of the wheel
python3.12 -m venv /path/runner && /path/runner/bin/pip install -r requirements.lock \
  && /path/runner/bin/pip install --no-deps dist/pypi/zabalack-<version>-py3-none-any.whl
.venv/bin/python tools/run_deployment_e2e_local.py --isolated-lab \
  --runner-python /path/runner/bin/python \
  --wheel dist/pypi/zabalack-<version>-py3-none-any.whl \
  --services-file services.json --reset-command "bash reset-lab.sh" --plan
.venv/bin/python tools/run_deployment_e2e_local.py --isolated-lab ... # same arguments without --plan
```

Before running, the tool checks the host layout, that the runner imports
`zabalack` from outside the checkout and from a non-editable installation, that
every installed package file equals the wheel's bytes, and that the installed
distribution registers both Alerta entry points. It then invalidates the previous
record, generates the bootstrap and certificate, runs the reset command, runs
`pytest tests/deployment_e2e` from the runner environment with a minimal
environment, validates the private observations exactly as the Compose runner
does, confirms no lab listener remains on ports 58081/58443, checks that the tree
did not change, validates the final record and writes
`evidence/deployment-e2e-run.json`. Raw logs stay under `build/private/<project>/`
and `/run/zabalack-e2e/`; neither leaves the host.

The reset command is the operator's responsibility. In the a10 preparation it
stopped the disposable `zabbix_server`, reloaded its database from the 7.0.10
tarball's `schema.sql`, `images.sql` and `data.sql`, restarted the server with
`env -i`, dropped and recreated the `alerta` database and the lab roles on the
PostgreSQL 18 cluster, and removed generated files from the private directory.

### Services from the orchestrator: `lab/deployment_native_lab.py` (R29)

The orchestrator supplies the table above from installed artifacts on one
host and is the operator's reset command. Everything runs as an unprivileged
user (as `nobody` when started as root; a root start without a usable
`nobody` identity is `BLOCKED_ENVIRONMENT`). Mutation requires
`--isolated-lab`.

| Command | Effect |
|---|---|
| `plan` | read-only: fixed topology, host layout check with the exact missing prerequisites |
| `state` | read-only: `absent`, `stopped`, `running`, `partial`, `unmarked` or `ambiguous` (an occupied port 5432 without the lab cluster, for example a packaged default cluster, is ambiguous and refuses) |
| `start` | dedicated PostgreSQL 18 cluster (`.deployment-lab/pgdata`, loopback 5432, `lab_admin` from `/run/zabalack-e2e/pg-password`, `scram-sha-256` host logins, database `alerta`) and the disposable Zabbix instance on port 8080 |
| `reset` | fresh `alerta` database with the lab roles dropped; Zabbix stopped, reloaded from the schema and restarted at the pinned reference |
| `services` | the services file with each component's version and package identity |
| `prepare-runner --wheel … --venv …` | a Python environment with `requirements.lock` and the wheel installed non-editably; refuses when it imports the checkout |
| `run --runner-python … --wheel … [--wheel-sha256 …]` | services file, then `tools/run_deployment_e2e_local.py` with `reset` as the reset command; exit code passed through |
| `cycle …` | `start`, `run`, `stop`, `wipe` in one invocation from an absent lab; cleanup after every outcome; both labs' logs preserved under `build/private/retained-phases/<run>/deployment-cycle/` before the wipe, and a preservation failure leaves the stopped lab in place; exit 5 when a passing run left something behind |
| `stop` | both components; exit 0 only when every component is observed stopped |
| `wipe` | removes the lab directories; refuses while any component runs |

```bash
python -m build --no-isolation --wheel --outdir /path/dist
python lab/deployment_native_lab.py plan
python lab/deployment_native_lab.py prepare-runner --isolated-lab --wheel /path/dist/zabalack-<version>-py3-none-any.whl --venv /path/e2e-runner
export ACK_LAB_PG_BIN=/usr/lib/postgresql/18/bin   # and ZABALACK_ZABBIX_* / ZABALACK_PHP when the packages are elsewhere
python lab/deployment_native_lab.py cycle --isolated-lab --runner-python /path/e2e-runner/bin/python \
  --wheel /path/dist/zabalack-<version>-py3-none-any.whl --wheel-sha256 "$EXPECTED_WHEEL_SHA256"
```

The Zabbix packages, PHP and PostgreSQL are read from `ZABALACK_ZABBIX_SERVER`,
`ZABALACK_ZABBIX_UI`, `ZABALACK_ZABBIX_SCHEMA` (the packaged `server.sql.gz`,
or a directory holding `schema.sql`, `images.sql` and `data.sql`; a bare
`schema.sql` is refused after the load because it carries no initial data),
`ZABALACK_PHP` and `ACK_LAB_PG_BIN` (`lab/zabbix_native_lab.py`); the Zabbix
instance must observe the pinned reference version or the services file is
refused. `zabbix_server` and the PHP server start with `PATH`, `TZ` and `LANG`
only: an inherited `HTTPS_PROXY` would route the lab's loopback webhook
deliveries elsewhere. The Zabbix lab cluster (loopback 55434) accepts
`scram-sha-256` logins only, with its generated passwords kept in
`.zabbix-lab/` at mode 0600, and its `stop` signals a component PID only when
the live process matches the identity the lab recorded at start (see
`lab/real_zabbix.md`). Raw logs stay under
`.deployment-lab/`, `.zabbix-lab/` and `build/private/<project>/`.

## What the local records do not claim

They do not execute the Compose images or an installed systemd/Ansible
topology, and they do not close G1–G8. A record written by the routine Actions
`native` job through this form is Actions execution evidence bound by
`tools/collect_ci_evidence.py`; a record produced by hand on a development host
is not, whatever its counts. The same holds for the manual deployment job
(`deployment-e2e.yml`, R29): its uploaded record is hosted execution evidence;
an author's `cycle` on a development host is preparation. A2 (webhook transport
verification) and the Alerta table-owner startup identity remain as described in
`docs/deployment-e2e.md`.
