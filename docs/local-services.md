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
install a packaged 18.x); `ACK_LAB_PGBOUNCER_BIN`, a PgBouncer 1.x executable.
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

## Deployment-path lab: `tools/run_deployment_e2e_local.py`

`lab/deployment_e2e_runtime.py` fixes the Compose hostnames, ports and private
directory, and the local runner executes it unmodified. The operator supplies:

| Input | Requirement |
|---|---|
| PostgreSQL 18 | reachable as host `postgres`, port 5432; superuser `lab_admin` with its password in `/run/zabalack-e2e/pg-password`; host logins password-authenticated (`scram-sha-256`); database `alerta` empty before the run |
| Zabbix 7.0.10 | a fresh disposable instance: `zabbix_server` running with a clean environment (libcurl honours `HTTPS_PROXY`), its frontend reachable as `http://zabbix-web:8080` with the default `Admin` account, no enabled source action and no synthetic host |
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

## What the local records do not claim

They do not execute the Compose images, the hosted workflows or an installed
systemd/Ansible topology, and they do not close G1–G8. A2 (webhook transport
verification) and the Alerta table-owner startup identity remain as described in
`docs/deployment-e2e.md`.
