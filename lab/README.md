# Isolated reference lab

Production access is not part of this lab. All accounts and records are synthetic.
The native reference stack is Alerta 9.1.0, PostgreSQL 18.0 and optional PgBouncer
1.24.1 transaction pooling. Exact source commits and hashes are in
`docs/reference_manifest.json`. G1–G8 remain open even after lab success.

## Native PostgreSQL

Routine Actions CI runs the native phases without Docker through
`tools/native_local.py` (`docs/local-services.md`, R26). On an authorised local
Linux development host with Docker Compose and direct host routing to its
bridge containers, the Compose form is:

```bash
python tools/native_ci.py --isolated-lab --phase postgres
# Run all five phases, each against a fresh synthetic database:
python tools/native_ci.py --isolated-lab
```

The runner reserves two kernel-assigned loopback ports before Compose startup,
validates the generated project's exact containers and internal bridge, and
relays TCP only to their owned PostgreSQL/PgBouncer addresses. Compose declares
no published ports: recent Docker versions skip publications on internal
networks. The relays close before the runner stops its containers; volumes and
failed-attempt evidence are preserved. Port allocation failures, unreachable bridge
addresses and incomplete relay cleanup fail the phase. Docker Desktop and
rootless networking are not assumed to provide this host route; use the local
service runner in `docs/local-services.md` when the bridge is inaccessible.
The database is real PostgreSQL 18 with native composite types, transactions and roles.
PostgreSQL 16 in the separate Zabbix definition is the Zabbix database only;
it is never a substitute for the Alerta/PostgreSQL 18 integration tests.

Alternatively build the pinned source archive into a workspace-local prefix:

```bash
bash lab/build_postgres.sh --isolated-lab /path/to/postgresql-18.0.tar.bz2
python lab/local_runtime.py start --isolated-lab
export ACK_TEST_ADMIN_DSN='postgresql://lab_admin@127.0.0.1:55432/postgres'
python lab/local_runtime.py state   # read-only: absent, stopped, running, unmarked or ambiguous
```

The source builder verifies SHA-256 and needs gcc, make, bison, flex, perl, zlib
and OpenSSL development headers. It does not install dependencies globally.
The local server uses loopback-only trust authentication as a synthetic lab
convenience, and must not be copied to production. `local_runtime.py stop
--isolated-lab` stops only the marked repository cluster and preserves its data;
it exits 0 only when the cluster is observed stopped afterwards (5 otherwise).
`local_runtime.py state` classifies the directory without touching it; a failed
`initdb` leaves its output in `.lab/initdb.log`.

`local_runtime.py` runs the server as the `nobody` user when started as root and
refuses to run PostgreSQL as root. Some hosted coding sandboxes provide no second
OS identity or block `runuser`; there the native cluster cannot start and the
native, PgBouncer and HA cases must stay NOT_RUN rather than be relabelled. The
Unix socket is disabled (`unix_socket_directories=''`); all lab clients use
loopback TCP on port 55432. This avoids Unix socket path limits in deep
checkouts or long temporary-directory paths, and requires no shared temporary
directory ownership changes. A startup override also handles a stopped,
previously initialised disposable cluster without removing its data.
When the pinned 18.0 archive is unavailable, a
later 18.x upstream tarball verified against its published SHA-256 is acceptable
for the lab (record the version in the evidence); the supplemental WASM engine
below permits SQL and actual Alerta persistence checks in a clearly separate
evidence layer.

Fixed local lab ports such as 55432 PostgreSQL, 55433 WASM socket and 56432
PgBouncer can overlap the host's ephemeral port range, so a lab client can
briefly occupy one in `TIME_WAIT`. `local_runtime.py` retries its fixed-port
start. The Compose native runner instead binds port zero on loopback and holds
both assigned sockets exclusively through service startup and relay activation.
It supplies their actual ports in the test DSNs and declares the pool port
separately as `ACK_TEST_PGBOUNCER_EXPECTED_PORT`; the probe test still requires
the declared port, loopback host, `alerta` database and dedicated probe user.
Local service/manual callers retain the default expected pool port 56432.
No port is released and rebound during startup, and no occupied port is retried
or taken from another process. Global port reservations and network settings
are unchanged.

## SQL fixture and test roles

The `tests/conftest.py` session fixture creates the unmodified vendored upstream
Alerta `history[]` schema and restricted roles in a fresh disposable database,
after `ACK_TEST_DESTRUCTIVE_LAB=YES_ISOLATED_LAB` is set. It refuses an existing
Zab-al-ack schema and does not delete data. The Compose runner supplies the
fixture environment and executes its selected phase automatically. When using
the separately started local PostgreSQL above, set the isolated-lab sentinel and run:

```bash
export ACK_TEST_DESTRUCTIVE_LAB=YES_ISOLATED_LAB
python -m pytest tests/postgres tests/alerta -ra
```

Provisioning may use the isolated lab administrator; acceptance assertions use
the intended application, owner, worker, probe, operator and installer roles.
No privilege grants are broadened to make a test pass. Do not point these tests
at an existing production or shared staging database.

## Supplemental PostgreSQL WASM engine

The pinned official PGlite 0.5.8 package contains PostgreSQL 18.3. It executes the
actual PostgreSQL composite, trigger, PL/pgSQL and role semantics. Its official
socket adapter 0.2.11 allows ordinary Psycopg clients and the installed Alerta
Flask/persistence code to connect. It uses a single backend with a multiplexer;
it cannot establish native multi-session concurrency, role login authentication,
PgBouncer behaviour, TLS, statement cancellation or failover. `SET ROLE` SQL
assertions verify current-user privileges only. No engine safeguards are patched.

```bash
npm ci --prefix lab --legacy-peer-deps --ignore-scripts
node lab/pglite_checks.mjs --isolated-lab
python lab/pglite_exec.py --isolated-lab -- python -m pytest tests/postgres tests/alerta -ra
```

The wrapper starts a fresh bootstrapped in-memory database and the child command
in the same process environment, sets `ACK_TEST_WASM=1` and the explicit synthetic
fixture opt-in, and stops the socket afterward. Logs disable wire inspection and
contain no proofs. `evidence/runtime_wasm_sql.json` records engine version,
assertion-file hashes and executed results. Never relabel a WASM check as native.

## Optional native PgBouncer transaction pool

```bash
python tools/native_ci.py --isolated-lab --phase alerta
```

The PgBouncer source release and SHA-256 are fixed in `Dockerfile.pgbouncer`.
Container build dependencies still come from the base distribution repository;
record resolved image digests/package versions when reproducing. The lab uses
trust authentication inside the isolated network so distinct roles can be tested
without real secrets; this is not a production authentication template. The
runner supplies the PgBouncer loopback DSN through the owned relay. Tests of
500-ms timeouts and clean pool reuse require an executed native route; TCP
readiness alone does not establish protocol, authentication or test success.

## Optional real Zabbix 7.0

```bash
docker compose -f lab/compose.zabbix.yml --profile real-zabbix up -d
```

The synthetic UI/API is `http://127.0.0.1:58080`; the isolated Docker network has
no notification egress. Deliberately no media type/action/customer recipient is
installed by startup. Engineer fixture provisioning and explicit source-write
opt-in are required. Version tags are fixed but image digests are not claimed
as retrieved or tested; capture `docker image inspect` digests on the lab host.
Real permission/macro/update-operation tests remain NOT_RUN until executed.
See `lab/real_zabbix.md` and `tests/real_zabbix/test_contract.py` for the executable
fixture-driven API tests and separate explicit synthetic acknowledgement opt-in.

For non-destructive shutdown, use Compose `stop`. Avoid `down -v` unless
explicitly discarding this synthetic lab; rollback tooling never deletes queues.
