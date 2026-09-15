# Environment acceptance procedures

These procedures are executable checklists for the deployment-specific portions of T1–T41. They are **MANUAL_GATE_PENDING** until an authorised engineer records commands, identities, timestamps and observations against the installed environment. Local reference tests complement them; no local or mocked result closes G1–G8. This implementation request authorises an isolated lab only. Obtain separate authorisation before any production mutation, restart, source acknowledgement or notification test.

For every run, create an evidence record containing test ID, environment, logical deployment/instance IDs, reference/deployed versions, non-secret role and operator references, approved change ID, exact command or UI sequence, start/end time, exit status, expected/observed outcomes, redacted attachments and final status. Allowed statuses are `PASS`, `FAIL`, `NOT_RUN`, `BLOCKED_ENVIRONMENT`, `MANUAL_GATE_PENDING`. A partially exercised catalogue item must identify the untested clause.

## T2

Prerequisites: G1 inventory, synthetic pilot cohort and explicit permission for the selected environment. List every serving API process/node and both sites; confirm all connect to the same logical writable deployment. Route a fresh synthetic single-alert `/action` acknowledgement directly through each serving node using that node's normal authenticated HTTP path. Do not use the native bulk endpoint. For each request record API result, new history head, capture ID, verified action reference, original Zabbix event ID, validation read, write result and projected state. Confirm the actor/customer boundary and final effective signer chain are identical. Assert one source effect per event and no untested node remains in service. A single-node lab cannot satisfy the all-site clause.

## T20

Prerequisites: all serving nodes enumerated, approved maintenance/bypass change and drain authority. Pause delivery; record in-flight attempts and retain their outcomes. Drain a selected lab/staging API node, give it a differing bypass/configuration revision, and attempt to register/readiness-check it. It must remain ineligible to serve tracked acknowledgements. Reconcile fleet fingerprints before allowing traffic. Confirm a marker-free local acknowledgement under bypass records saved history plus `skipped_capture_bypass`, revision and change reference; no trigger diagnostic is expected for the marker-free acknowledgement. A marked candidate with the database flag active must create only a bypass diagnostic. Physically disabling the trigger produces no diagnostic. Confirm worker/probe cannot alter bypass or trigger state. Restore the approved uniform configuration while still drained, run the canary, then restore traffic deliberately. Evidence must demonstrate detection and fencing, not merely two configuration files with different values.

## T27

Prerequisites: redacted live media-type and complete matching action/update-operation exports (G2/G3), approved isolated recipients/sinks and inventory credentials distinct from the delivery role. Enumerate every action matching each cohort, including inherited/escalation/update operations and all native email/command/webhook destinations. For a fresh synthetic event, execute the integration acknowledgement and inspect the Zabbix update history plus each sender's delivery/command audit. Assert recognised neutral-reference echoes cause no customer email, command or webhook side effect. Make a genuine authenticated human update with unrelated text and prove intended notifications remain. Native email or command update operations that cannot suppress the echo must be excluded or split by approved action configuration before that cohort is enabled. A mocked webhook or static string check cannot prove this result.

## T30

Run `python tests/performance/run_batch.py --help` for the isolated-loopback load harness. Generate separate manifests of exactly 50 and 200 fresh synthetic alerts and original source events. Each manifest must designate one supported single-alert `/action` request per alert; do not create a successful repeat-Ack route. Perform a baseline observation window and then each bounded-concurrency batch through the pinned Alerta API. Include a second population whose original source event is already acknowledged. Record request success/409/error counts, Alerta response latency p50/p95/max, commit-to-source completion p50/p95/max, validation-plus-write duration, pending age/queue depth, per-instance active delivery leases and per-tenant progress. Record hardware, Python/PostgreSQL/Alerta/Zabbix/PgBouncer versions and all emulation. Independently collect PostgreSQL activity/locks/CPU, PgBouncer clients/servers/pools, PHP-FPM status/queue/max-children and host resource utilisation through approved read-only monitoring. Inject no real notification destinations. The Python harness reports API/projection latency only; a missing PHP-FPM or DB metric remains unmeasured. Pass requires the configured limits hold and all requested measures are reviewed; a timing-only mock run is not full T30.

## T31

Prerequisites: authorised disposable two-worker/two-site or equivalent fault-injection environment, shared PostgreSQL writer and a controllable Zabbix response gate. Start an action, block the remote response after the server has accepted the request, and terminate/fence the old worker process. Record the durable pre-send attempt, fencing generation and lease expiry. Start the replacement only after old side-effecting consumers are fenced. Its first operation for the abandoned attempt must be a reference/history reconciliation read, never a blind acknowledgement resend. Release the remote response/commit gate and prove completion uses the stable opaque public reference with no duplicate comment. Repeat crashes before send, after send, after remote commit but before local completion, and before/after projection. A local expired lease does not prove the old HTTP request stopped. Record unknown outcomes and the source platform's replication/RPO implications explicitly.

## T34

Prerequisites: real PgBouncer transaction pool with separate probe identity, explicit routing inventory and real PostgreSQL. Use the plugin's caller-side sequence of separate `BEGIN READ ONLY`, `SET LOCAL statement_timeout='500ms'`, `SELECT zabalack.capture_health()` and `COMMIT` commands. In the isolated lab, make the selected health query wait on a lock/sleep beyond its server deadline. Measure the server cancellation boundary separately from total connection/acquisition/transport time. Assert the caller rolls back/discards after error, invalidates its successful cache, rejects an eligible acknowledgement with `AlertaException`, and does not use a stale success. Reuse the same client after the obstruction ends; assert no aborted transaction or leaked read-only/timeout setting. Confirm process-count/pool budgets and that forced database user configuration does not collapse the probe into the application role. Direct PostgreSQL execution alone cannot pass the PgBouncer transport clause.

## T40

Prerequisites: installed multi-tenant Alerta with `AUTH_REQUIRED` and `CUSTOMER_VIEWS` enabled, an approved
engineer account authorised for at least two customers, and a separate reader account whose scope overlaps the
engineer's without containing it. Acknowledge one eligible alert belonging to a customer both accounts share.
As the reader, retrieve that alert and the collection containing it, in both the default and the JSONP response
modes, and confirm no response carries the intent attribute or any customer outside the reader's own scope.
The engineer's login is not prohibited outright: committed history legitimately carries it under the separate
history layer of §12, and A1 retains the committed proof. Confirm instead that the login appears only in
history attribution and never inside `attributes`. Repeat the retrievals before the worker verifies the capture, after projection completes,
with the worker stopped, and after an acknowledgement that failed before its history write. Then exercise the
search path: submit `attributes.zabbix_ack_sync_intent:<term>` probes to `/alerts/count` and `/alerts` as the
reader and confirm each is refused with HTTP 400, not answered with a zero count — a zero count is satisfied by
an empty database. Submit the same selector with a term present only in the withheld scope and with a term
present in no intent, and confirm the two refusals are identical in status and message; a refusal that varied
with the term would itself be the oracle. Cover every route to the value: the keyed term, the `_.key` alias, a
phrase, a sub-expression, `_exists_`, `q.df=attributes`, the `attributes.<key>` direct filter, restricted and
whole-column `sort-by`, and `group-by=attributes` on `/alerts/topn/count` and its flapping and standing
siblings, which otherwise return the whole attributes object as a plain dictionary. Those aggregate endpoints
read `FROM alerts, UNNEST(service) svc`, so the exercised alert must carry a nonempty service array or the
check passes on an empty result. Then confirm legitimate querying still works: a permitted keyed attribute
search, a permitted `sort-by` and a permitted `group-by` must all succeed, because a deployment that disabled
attribute querying wholesale would refuse everything without protecting anything. Repeat the retrievals through
`/alerts/history`, which serialises separately and carries the alert's current attributes. Finally, with a
second session and both sessions bound to the application role, observe an acknowledgement paused between its
staged-attribute commit and its history write and confirm the selector is refused during that interval while
permitted data for the same alert remains visible, and repeat after a failed acknowledgement and one for which
no database cohort exists. Record the exact account scopes, the alert
identity, every endpoint and response mode exercised, and the observed counts. Confirm in the same session that
capture still succeeds, the operator's unrelated attributes and source-identity keys are unchanged, and the
committed history proof is intact. Reader protection applied only to response bodies does not pass T40.

## T41

Prerequisites: the approved installed database under the exact deployment principals, and authorisation to
observe stored rows. This item covers A5 stage two as shipped in migration 008: retirement of the staged intent
from `public.alerts.attributes`, and the generation fence `zabalack.alert_generations` that replaces the guard
which read it.

Upgrade sequence, with the principal recorded for each step. Before applying anything, record the installation's
current `zabalack.schema_version` and the contents of `zabalack.schema_migrations`. 008 upgrades schema 3 and refuses
any other version: an installation carrying the superseded `008_intent_retirement.sql` .. `013_seed_ledger_on_verification.sql`
set reports 9 and needs a forward migration written for it, which this file is not. Apply
`008_generation_fence.sql` through the ordinary migration path as `zabalack_owner` and record the schema version
moving 3 to 4. Then run
`sql/install/09_installer_roles.sql` as `zabalack_owner` with `installer_roles` set to the comma-separated list
of installation principals that are members of `zabalack_owner`; record which roles those are, or record that
the list is empty because the migrations run as a superuser and no role holds `zabalack_owner`. Confirm
`zabalack.installation.installer_roles` afterwards, and that it is not `NULL`: until it is recorded,
`capture_health()` reports `functions_ok` false and `13_reseal.sql` refuses to seal. Then run
`sql/install/12_application_grant.sql` as `zabalack_owner` with `alert_app_role` set to the role the Alerta
application connects as; record which role that is (the alerts owner under the upstream default, a separate
login role where the deployment splits them). Then re-seal the readiness manifest with
`sql/install/13_reseal.sql`, also as `zabalack_owner`; it refuses unless the schema version is 4, the version-4
objects are present and the trigger fingerprint still matches the sealed one, since 008 does not change the
trigger. Confirm `zabalack.capture_health()` reports `functions_ok` and `trigger_ok` afterwards, and that workers
pinned to schema 3 report unhealthy until upgraded. Deploy the plugin only after the grant: a plugin that cannot
reach `zabalack.stage_generation()` refuses every acknowledgement with `zabalack_generation_unavailable` (503),
which is the intended failure. Acknowledgement paths must be fenced across the whole sequence, as §2 of the
deployment guide already requires; an acknowledgement staged by the old plugin between the migration and the
plugin deployment is recorded only when it captures, at the observation tier.

Record the grant's exact extent: `12_application_grant.sql` gives the application role `USAGE` on `zabalack`,
`EXECUTE` on `zabalack.stage_generation(text,uuid,jsonb)` and nothing else — no table privilege in the schema
and no other function from this grant. Where the application connects as the alerts owner, the upstream default,
that role also holds 005's grants — `capture_ack()` for the trigger and `seal_installation()` for the installer —
which are expected and are the reason §2 of the deployment guide prefers a separate login role; record which
topology applies. The script revokes the entry point from any former grantee and records the role in
`zabalack.installation.application_role`, and revokes that former grantee's `USAGE` on the schema with it (the
alerts owner and the runtime roles keep theirs, which the map names in their own right); confirm that
`zabalack.stage_generation()` has that one non-owner grantee
and no other and that the role holds `USAGE` on the schema, that no non-superuser role is a member of it, and that it
is not, and cannot assume, any `zabalack_*` role (`pg_auth_members` in both directions — a membership is not an ACL, so
neither the grantee list nor the sealed digests move with it; a member of the application role stages through the entry
point, and an application role that can assume `zabalack_worker` reaches `worker_call()` and its verification
operations. The grant script refuses a role in either position), that no function in `zabalack` is executable by
`PUBLIC`, that the role executes no other function in `zabalack` and holds no privilege on a table there — as itself
or as any role it can assume, the alerts owner's own `capture_ack()` and `seal_installation()` grants excepted where
that is the application role — and that `zabalack.alert_generations` admits no grantee but its owner. A stray grant
moves the digests, so it reads unhealthy until a re-seal agrees with it; the effective-rights assertion is what
survives that seal, and `worker_call()` in the application's hands is a signature check skipped. `capture_health()`
asserts all of it under `functions_ok` — together with the schema's whole grant map, not only the application's corner of it:
the grantees of every routine in `zabalack` — procedures as well as functions, and record `prokind` for each, since a `SECURITY DEFINER` procedure runs as its owner exactly as a function does — are exactly the ones the installation makes, and none of them missing — record each grant as held, not merely the absence of strays; a revoked `EXECUTE` leaves nothing unexpected to find while the role it answers to gets `permission denied` (`worker_call`/`project` to `zabalack_worker`,
`operator_call` to `zabalack_operator`, `capture_health` to `zabalack_probe` and `zabalack_operator`, `capture_ack` and
`seal_installation` to the alerts owner, `stage_generation` to the recorded application role, every other function to no one), no
relation admits a grantee at all, the fence's identity sequence included; every routine body is the one 008 shipped — record `zabalack.installation.routine_bodies_md5` and `zabalack.catalog_fingerprints()->>'routines'` and confirm they agree; 008 sets that column and no seal replaces it, and 008 refuses to migrate a sealed tree that has already drifted, which is the case to record as *not* raised during the upgrade; every routine keeps `search_path` pinned to `pg_catalog, zabalack` and every routine anyone but its owner can execute is `SECURITY DEFINER` — record `prosecdef` and `proconfig` for each, since `SECURITY INVOKER` there removes the operator's path rather than weakening it; no grant carries `GRANT OPTION`, so record `aclexplode(...).is_grantable` as false for every non-owner entry on the functions, the schema and the owner's columns on `public.alerts`; the schema and everything in it is owned by
`zabalack_owner`, since a `SECURITY DEFINER` function runs as its owner; schema `zabalack` admits only `USAGE`, and only to the
three runtime roles, the alerts owner and the recorded application role — so record its ACL and confirm no `CREATE` to any
role but the owner, nothing to `PUBLIC`, and that each runtime role still holds that `USAGE`, without which its fully
qualified call fails at schema access while every function grant still reads right — and the alerts owner too, where the
deployment splits it from the application role, since without it 005's `capture_ack()` and `seal_installation()` grants
and `10_trigger.sql` fail at schema access; and each keeps the attributes
`00_roles.sql` declares (no `SUPERUSER`, `CREATEDB`, `CREATEROLE`, `BYPASSRLS`, `REPLICATION` or `INHERIT`; `LOGIN` for
the three runtime roles only) (its `UPDATE` is `setval()`, and a sequence moved backwards
inverts the fence's ordering), and `zabalack_owner` still holds exactly the `public.alerts` grants `00_roles.sql` makes it — record the column ACL and confirm `SELECT` on `id`, `customer`, `status`, `attributes`, `update_time`, `UPDATE` on `attributes`, and nothing else, since `stage_generation()` runs as this owner and `public.alerts` is outside the schema the digests cover; and the recorded application role can still open a session as itself — record `rolcanlogin`, `rolconnlimit` and its `CONNECT`, since Alerta connects as that role and no member of it is permitted, so one that cannot connect leaves nothing able to stage; and the three runtime roles can still open a session — record each one's `CONNECT` and its `rolconnlimit`, a limit of zero refusing every new session as flatly as a revoked grant; `zabalack_owner` is `NOLOGIN` and needs none, and its `USAGE` on schema `public` is not recorded here because losing that makes `capture_health()` and `13_reseal.sql` raise rather than pass, which the re-seal now names; and none of the four is itself a member of any role — record `pg_auth_members` with each of the four as `member` and confirm it is empty, since one that can `SET ROLE` to the alerts owner writes `public.alerts` past the projection boundary and no `has_*_privilege()` lookup shows it; and no role outside the recorded installer topology can act as any of the four — record, for every
non-superuser role, `pg_has_role(role, '<each of the four>', 'MEMBER')`, and confirm the only ones that answer
true are the roles named in `installer_roles`, against `zabalack_owner` alone; reach, not the direct edge, since a
member of a named installer follows the chain to `zabalack_owner` and no direct `pg_auth_members` row shows it; a `NOINHERIT` member resolves no privilege through any ACL while `SET ROLE` hands it the role whole, and
the fingerprint hashes a membership only where the member is itself one of the four, so an outside one moves no digest at
all — whoever reaches
`worker_call()` records captures as verified without the signature check, and whoever reaches `operator_call()` holds the controls —
even under a matching
seal, `13_reseal.sql` refuses to seal a tree that breaks any of them, and a false value there is a real
misconfiguration, not a probe fault. A change of application role is a re-run of the grant script for the new role
followed by a re-seal, recorded as a change; a change of installation principal is a re-run of
`09_installer_roles.sql` naming the whole new list — it replaces the record rather than adding to it, so a retired
principal stops being exempt the moment it is left out — followed by a re-seal, recorded the same way.

Record the backfill's result. 008 records one generation for every capture that produced an action or still awaits
verification (`captured_unverified`, `verifier_hold` in the outbox), in capture order — a capture awaiting
verification whose live intent has already left its row has no other record, and left out it would let the older
verified acknowledgement stand as the newest generation while the newer one, once verified, is refused — then one
for every staged intent found on a live row; a capture that failed, was rejected or expired records nothing. All
are observation-tier rows; a row with none acquires its generation at its first staging. Verification after the
upgrade appends nothing to the fence: the order is the capture order, fixed at migration. Run it against live rows and record its scope, its bound and its completion;
historical backups, replicas and previously produced exports are out of its reach and remain governed by G8.

For each lifecycle path recorded in amendment A5 — verified capture, capture-side evidence failure, capture
bypass, worker-side rejection, verifier hold, stopped worker, a missing database cohort, and an acknowledgement
abandoned before its history write — establish the state on an eligible alert, then confirm the stored intent is
retired from `public.alerts.attributes` and that `zabalack.captures.intent` is preserved byte-for-byte alongside
source-identity keys, unrelated operator attributes and the separately protected columns.

Prove the generation protection on the installed environment, through the real entry points: acknowledge A
through `/alert/<id>/action`, verify and project it; stage B on the same alert and let it fail before its
history write; delete B's reserved keys through `/alert/<id>/attributes`, and separately replay A's whole
attribute dictionary through the same endpoint; confirm A's projection is refused in both cases with
`projection_action_generation_mismatch` and that `zabalack.newest_generation(<id>)` still names B. Retire B's
intent with the sweep and confirm the same. Confirm a fence row after a real acknowledgement carries
`recorded_by='staging'`. Distinguish the two failure points: an acknowledgement refused at the staging step
(`zabalack_generation_unavailable`, 503) or by Alerta's own transition — `ack` on a shelved, closed or expired
alert (409), or on one in `assign` (200, `skipped_no_ack_transition`) — leaves neither a fence row nor a staged
intent, while one that fails after the staging commit (a refused signing, a capture fault) keeps both: the fence
row permanently, the intent until the sweep retires it. Such a staging is superseded only by a later
acknowledgement through the protected operation, and until then the earlier action's re-projection is refused;
that is the designed answer, and the remedy is to acknowledge again.

Record the retirement cadence actually observed across the fleet: the slot is claimed in the installation row,
so exactly one worker per `retirement_interval_seconds` performs the scan however many workers run; the rest
return `skipped: not_due`, and a batch that reached its limit, or that had to leave a row another session held to
the next pass (`deferred`), leaves the slot due so the next cycle continues draining. Confirm the interval is set to a value the deployment accepts, since it bounds how long a staged intent
persists; it is changed only by `zabalack_owner`, with
`UPDATE zabalack.installation SET retirement_interval_seconds=<seconds> WHERE singleton` (the claim clamps it to
1–86400), recorded as a change under that principal — no `operator_call` operation or CLI command sets it.
Measure the sweep's cost on the installed table: `public.alerts` carries no index for the
`attributes ? 'zabbix_ack_sync_intent'` predicate and `zabalack_owner` cannot create one, so the bounded batch
limits matching rows, not rows scanned. The batch is chosen from the rows that are due, so a staging still inside
its window takes no place in it, and a row another session holds costs a pass nothing: its place is filled from
the due rows beyond it, skipping whatever else is held, so a pass makes progress whenever any due row is free,
and the held rows are reported as `deferred`, which keeps the slot due for them. The due test is made against
the clock at each evaluation, never a time saved when the sweep began, so an acknowledgement staged on a row
while the scan is in flight is left inside its window when the sweep reaches it; that test and the verifier's
(`captured_at` no earlier than `issued_at`) share one requirement, that the application host's clock does not
run ahead of the database's. The sweep runs under its own statement budget
(`retirement_timeout_seconds`, default 30 s, distinct from the 3 s every other worker call gets); a scan that
does not fit is cancelled, rolled back with its claim and retried every fast cycle at the same cost, and
retirement then never happens. Record the measured scan time; where it approaches the budget, the alerts owner
adds `CREATE INDEX CONCURRENTLY … ON public.alerts (id) WHERE attributes ? 'zabbix_ack_sync_intent'` as an
approved change under the same principal that installs the trigger. The index changes no `zabalack` object; it
is optional only while the scan fits the budget.

Rollback: see `docs/rollback.md` §008. The SQL is rolled back before the plugin or not at all — a pre-008
plugin's acknowledgements on an alert that already carries a staging-recorded generation are refused projection
until one goes through the protected operation again — and reverting `project()` must also stop the sweep.
Nothing shipped deletes a fence row; a rollback does not either.

A passing disposable lab does not close this item.

## Additional deployed clauses

For T11a/T11b/T12/T23/T24, repeat positive/negative tenant tests with actual customer-scoped credentials and every approved admin exception. Valid within-scope substitution remains inside the release-one trusted-operator policy; do not call it protected ingestion lineage. For T28 verify actual token expiry, role denial and the installed CA chain without publishing tokens. For T35/T38/T39 execute role assertions under the exact deployment principals including inheritance; a lab superuser setup session is not the assertion identity. For T36 verify the installed alarm model preserves HTTP 409 and creates no new history/capture/action/expiry. For T21/T39 preserve actual owner, function, trigger, grant and migration evidence from the same approved deployment change. G7/G8 require named ownership and an approved records policy independently of any test success.
