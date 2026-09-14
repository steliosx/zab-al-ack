# Isolated reference lab

Production access is not part of this lab. All accounts and records are synthetic.
The native reference stack is Alerta 9.1.0, PostgreSQL 18.0 and optional PgBouncer
1.24.1 transaction pooling. Exact source commits and hashes are in
`docs/reference_manifest.json`. G1–G8 remain open even after lab success.

## Native PostgreSQL

On an authorised development host with Docker Compose:

```bash
docker compose -f lab/compose.postgres.yml up -d
export ACK_TEST_ADMIN_DSN='postgresql://lab_admin:synthetic-postgres-lab-only@127.0.0.1:55432/alerta'
export ACK_TEST_DESTRUCTIVE_LAB=YES_ISOLATED_LAB
```

The Compose server exposes loopback only and uses an internal network. It is a
real PostgreSQL 18 server with native composite types, transactions and roles.
PostgreSQL 16 in the separate Zabbix definition is the Zabbix database only;
it is never a substitute for the Alerta/PostgreSQL 18 integration tests.

Alternatively build the pinned source archive into a workspace-local prefix:

```bash
bash lab/build_postgres.sh --isolated-lab /path/to/postgresql-18.0.tar.bz2
python lab/local_runtime.py start --isolated-lab
export ACK_TEST_ADMIN_DSN='postgresql://lab_admin@127.0.0.1:55432/postgres'
```

The source builder verifies SHA-256 and needs gcc, make, bison, flex, perl, zlib
and OpenSSL development headers. It does not install dependencies globally.
The local server uses loopback-only trust authentication as a synthetic lab
convenience, and must not be copied to production. `local_runtime.py stop
--isolated-lab` stops only the marked repository cluster and preserves its data.

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

The lab ports (55432 PostgreSQL, 55433 WASM socket, 56432 PgBouncer, 58080
Zabbix API, 10051 Zabbix trapper) are inside Linux's default ephemeral port
range, so a lab client can briefly occupy one of them in `TIME_WAIT`.
`local_runtime.py` retries the start; on a dedicated lab host, reserving them
(`sysctl -w net.ipv4.ip_local_reserved_ports=10051,55432,55433,56432,58080`)
removes the race entirely.

## SQL fixture and test roles

The `tests/conftest.py` session fixture creates the unmodified vendored upstream
Alerta `history[]` schema and restricted roles in a fresh disposable database,
after `ACK_TEST_DESTRUCTIVE_LAB=YES_ISOLATED_LAB` is set. It refuses an existing
Zab-al-ack schema and does not delete data. Then run:

```bash
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
docker compose -f lab/compose.postgres.yml --profile pooling up -d --build
export ACK_TEST_PGBOUNCER_DSN='postgresql://zabalack_probe@127.0.0.1:56432/alerta'
```

The PgBouncer source release and SHA-256 are fixed in `Dockerfile.pgbouncer`.
Container build dependencies still come from the base distribution repository;
record resolved image digests/package versions when reproducing. The lab uses
trust authentication inside the isolated network so distinct roles can be tested
without real secrets; this is not a production authentication template. The proxy
exposes only loopback. Tests of 500-ms timeouts and clean pool reuse require this
native route and remain blocked in the current coding environment.

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
