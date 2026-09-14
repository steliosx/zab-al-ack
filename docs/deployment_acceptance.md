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
modes, and confirm no response carries the intent attribute, the engineer's login or any customer outside the
reader's own scope. Repeat the retrievals before the worker verifies the capture, after projection completes,
with the worker stopped, and after an acknowledgement that failed before its history write. Then exercise the
search path: submit `attributes.zabbix_ack_sync_intent:<term>` probes to `/alerts/count` and `/alerts` as the
reader, using a term present only in the withheld scope and a control term present in no intent, and confirm
both return zero. A zero from the control alone proves nothing. Record the exact account scopes, the alert
identity, every endpoint and response mode exercised, and the observed counts. Confirm in the same session that
capture still succeeds, the operator's unrelated attributes and source-identity keys are unchanged, and the
committed history proof is intact. Reader protection applied only to response bodies does not pass T40.

## T41

Prerequisites: the approved installed database under the exact deployment principals, and authorisation to
observe stored rows. For each lifecycle path recorded in amendment A5 — verified capture, capture-side evidence
failure, capture bypass, worker-side rejection, verifier hold, stopped worker, a missing database cohort, and an
acknowledgement abandoned before its history write — establish the state on an eligible alert, then confirm the
stored intent is retired from `public.alerts.attributes` and that `zabalack.captures.intent` is preserved
byte-for-byte alongside source-identity keys, unrelated operator attributes and the separately protected
columns. Prove the generation protection under concurrency: retire a newer unverified intent while an older
projection is in flight, and confirm the older projection does not overwrite the newer state. Run the bounded
backfill against live rows and record its scope, its bound and its completion; historical backups, replicas and
previously produced exports are out of its reach and remain governed by G8. Record the schema version before and
after, the re-seal of the readiness manifest, and the principal that performed each step. A passing disposable
lab does not close this item.

## Additional deployed clauses

For T11a/T11b/T12/T23/T24, repeat positive/negative tenant tests with actual customer-scoped credentials and every approved admin exception. Valid within-scope substitution remains inside the release-one trusted-operator policy; do not call it protected ingestion lineage. For T28 verify actual token expiry, role denial and the installed CA chain without publishing tokens. For T35/T38/T39 execute role assertions under the exact deployment principals including inheritance; a lab superuser setup session is not the assertion identity. For T36 verify the installed alarm model preserves HTTP 409 and creates no new history/capture/action/expiry. For T21/T39 preserve actual owner, function, trigger, grant and migration evidence from the same approved deployment change. G7/G8 require named ownership and an approved records policy independently of any test success.
