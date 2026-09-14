# Worker, RPC and reconciliation implementation

`zabalack.worker` independently polls verification and delivery. Each database
call is a short transaction through the restricted `zabalack.worker_call` facade.
PostgreSQL owns verified action/effect uniqueness, per-event generation fences,
two instance slots across workers/sites, background read leases, pause, expiry,
permanent-error circuits and attempt evidence. Local task limits only bound
process resources. Malformed candidates are rejected before endpoint/credential
resolution and cannot reserve verified action IDs. The verifier consumes a
bounded fair SQL candidate queue; unavailable keys hold without forgery claims.

The worker rechecks the signed actor, customer and configured cohort, and SQL
rechecks its authoritative controlled cohort before promotion, claim and each
write. A committed intent in attributes alone does not promote work. Frozen event
IDs are used throughout. The RPC pre-read returns all trigger hosts, compared
against the complete approved set. Resolved original problem events remain
eligible; already acknowledged events are observed without comments or an
attribution claim. No latest-event lookup exists.

`rpc.py` uses HTTPX 0.28.1 without retry middleware or redirects and without proxy
settings inherited from the environment. HTTPS uses an explicit CA and hostname
validation; the registry requires exact configured logical identity and an
approved API version. Only the three delivery-role methods are accepted. Production
configuration requires HTTPS. A loopback-only HTTP `Endpoint` exists for injected
mock transport unit fixtures and cannot be selected by production settings.
Protected token files use the common no-symlink/owner-permission reader. Parsed
JSON rejects duplicate keys and nonfinite values; response size, JSON-RPC identity
and the sole returned event ID are validated. Ordinary logs have a field allowlist
and never contain source notes, secrets, proofs or upstream error strings.

`asyncio.timeout` supplies an actual overall HTTP deadline in addition to connect
and read limits. PostgreSQL calls have an eight-second caller deadline and a
bounded close, in addition to connection and transaction-local statement limits.
These are cancellation bounds; neither a timeout nor a lease proves that a remote
request stopped. The durable attempt is created before sending. Lost responses,
shutdown after send and malformed post-send responses stay uncertain. Late results
are retained as evidence without overwriting a newer lease generation.

The reconciler never calls `event.acknowledge`. Unknown action=6 outcomes are read
with acknowledgement history and the constant opaque reference. A matching
neutral comment plus acknowledged state provides `reference_observed`; otherwise
acknowledged state provides only `source_ack_observed`, including action=2 mode.
A reference found after source unacknowledgement is drift, with no automatic
re-acknowledgement. Terminal read outages preserve completed delivery evidence.
Empty due queues perform no source reads. Fast work is limited to 200 due actions;
nightly scans use a cursor and retain skipped rows when a background slot is busy.
Host inventory drains an indexed 15-minute due queue in batches of at most 50 host
IDs, retains unknown IDs as drift and never approves replacements automatically.

## Explicitly settling an unresolved remote attempt

A missing reference and an unacknowledged event do not establish that a timed-out
request has stopped. The action remains uncertain and even the ordinary retry
command cannot initiate a write. An engineer may establish independent evidence
that the old sender is fenced, remote processing has finished and the exact event
and acknowledgement history contain no source effect. Record that evidence under
the approved change, then explicitly settle the same action:

```bash
zabalack --config /etc/zabalack/config.json settle-unknown \
  --action ACTION_UUID --disposition no-effect-confirmed \
  --evidence-ref REVIEWED_EVIDENCE_REFERENCE \
  --confirmation REMOTE_ATTEMPT_FENCED_AND_SETTLED \
  --owner APPROVED_OPERATOR_REFERENCE --change-ref APPROVED_CHANGE_REFERENCE
```

This command is an audited operator assertion, not a software proof of remote
cessation. SQL requires uncertainty and no active delivery lease. It records a
failed disposition within the original lifetime, or expired after it, and does
not send anything. An unexpired action then needs a separately selected `retry`;
an expired action needs a new explicit human action. The closed source-effect
generation permits that later human action without extending the earlier expiry.
A direct acknowledgement of the verified exact event in Zabbix is another
operational option: read-only reconciliation observes it and resolves uncertainty.

## Evidence boundaries

`tests/unit/test_worker.py`, `tests/rpc/` and the independent `tests/security/`
regressions are unit/mocked evidence. `tests/postgres/test_worker_path.py` runs the
actual signed capture → SQL verification → delivery lease → mock source call → SQL
outcome → projection path, including a lost-response/reference recovery case.
In this sandbox it ran against PostgreSQL 18.3 compiled to WASM through PGlite,
with explicit `SET ROLE zabalack_worker` for runtime calls. A third scenario verifies
audited no-effect settlement requires independent evidence and a separate retry. That is supplemental engine
evidence only: it does not establish native login authentication, multi-process
concurrency, statement cancellation, PgBouncer, real Zabbix permissions or HA.
The same test is runnable on the isolated native PostgreSQL lab. G1–G8 stay open.

## Monitoring export

`zabalack --config /etc/zabalack/config.json metrics` emits Prometheus text for
an existing monitoring/textfile collector. Gauges come from restricted PostgreSQL
aggregates: pending count and oldest age by instance, uncertainty, success/failure
counts, retries, pauses and durable circuits. Active incidents are grouped by
instance and failure class; verifier-key failure is a global verifier incident
because an unverifiable candidate cannot authoritatively identify its instance.
Configured token expiry metadata is emitted without reading source tokens. No
scrape server, notification destination or scheduled monitoring job is enabled.
