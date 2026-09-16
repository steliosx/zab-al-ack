# Native delivery crash and race tests

`tests/postgres/test_native_delivery_faults.py` is a dedicated fresh native
PostgreSQL 18 phase. It creates signed synthetic capture through the real Alerta
history trigger, verifies it with the production Worker, and spawns independent
worker processes using the production Postgres client and direct
`zabalack_worker` login. Each process checks `session_user=current_user` and runs
the production health check. The loopback HTTPS source uses an ephemeral trusted
certificate, protected synthetic token/key/DSN files, exact JSON-RPC response
binding and an fsynced effect ledger. No external source endpoint is accepted.

The database observer adds process barriers before/after production calls; it
does not alter their inputs or outputs. Gates cover claim, before-write,
committed attempt and completion. The source can persist an acknowledgement
and then drop its response or hold the connection at a remote-commit gate.

The five native cases cover:

- Five competing processes, two distinct active owners/slots for one instance,
  and a sixth contender while two committed remote effects await responses.
- A committed source acknowledgement whose HTTP response is dropped: durable
  uncertainty, six delivery polls after the persisted retry time without a resend, and read-only
  reconciliation of the exact neutral reference.
- Actual `SIGKILL` after the source effect commits and before completion: a
  replacement generation inherits uncertainty without a second send.
- A stale pre-send owner: `before_write` succeeds, the real 30-second lease
  expires, a replacement claims, and the old `start_attempt` is fenced.
- A successful response paused before completion: after real lease expiry and
  replacement, the late completion stays immutable evidence and cannot settle
  or overwrite the replacement's uncertain state.

Lease and retry eligibility waits use `clock_timestamp()` on native PostgreSQL
and bounded polling. No timestamps are edited. Native execution includes three
actual lease expiries and two 60-second uncertainty backoffs, so allow roughly
four minutes for this phase. Every barrier, connection,
child join and teardown is bounded. Teardown stops processes and the local
server while keeping source effects, allowlisted process checkpoints and ledger
summaries in the pytest temporary directory. It never deletes the database or
resets source acknowledgements. Use a unique `--basetemp` inside
`build/evidence-raw/` when retaining a CI failure bundle; do not reuse it because
pytest clears an existing basetemp at startup. Keep these raw files out of source
archives, and redact JUnit with the existing evidence tooling.

On a failed native phase, `tools/native_failure_summary.py` exports only bounded
allowlisted process/ledger facts and aggregated pytest failure classifications.
The latter contain exception classes, fixed categories, known SQLSTATEs inferred
from PostgreSQL exception classes, and counts. A bounded `source_test` identifier
is included only when the JUnit module and base function match a top-level
`test_*` definition in the current phase's fixed source files. Test source is
parsed without execution, at most once per module the JUnit names; parameter
values are discarded. Missing, unsupported or unsafe identities are omitted
without discarding the failure classification. Counts and classifications cover
every case in the bounded document; `truncated` marks dropped export detail
rather than unread input.
No raw JUnit identifiers, tracebacks, messages, connection details or raw files
are exported. The summary is bound to
the current source/harness record, written to `evidence/native-failure-summary.json`
and printed as JSON in the hosted job log. Missing, unsafe or oversized JUnit
input is explicitly unavailable. This diagnostic summary is not acceptance
evidence and does not replace a successful native rerun.

The native runners (`tools/native_ci.py` on Compose; `tools/native_local.py`
on locally installed PostgreSQL 18 and PgBouncer, see `docs/local-services.md`)
select `tests/postgres/test_native_delivery_faults.py` in their
separate `delivery_faults` phase; a fresh cluster avoids interference from
unrelated global delivery queues. Run it with
`python tools/native_ci.py --isolated-lab --phase delivery_faults`. The isolated
DSN and destructive-lab sentinel are provided by the runner. Explicit WASM mode,
a missing native DSN, missing POSIX process support,
or missing OpenSSL produces an explicit skip, never a native pass.

`python -m pytest tests/native_support/test_fault_harness.py -q` independently
checks the real HTTPS helper's commit/drop/cancellation behavior and transparent
barrier forwarding without PostgreSQL. These are offline helper checks only.
The native suite and helper checks do not prove deployed authentication, real
Zabbix effects, PgBouncer contention, cross-site HA, or deployment gates G1–G8.
