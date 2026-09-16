# Non-destructive rollback and emergency bypass

**a9 namespace change:** read [RENAME.md](RENAME.md) before installation or rollback. This release uses a fresh `zabalack` database namespace and refuses an existing Ackline schema.

Rollback preserves the ledger, capture records, pending entries and source state. Never drop `zabalack`, delete queued evidence, auto-replay on restart, reset an action lifetime, or unacknowledge Zabbix to undo the integration. Restoring a database backup is a separately approved recovery operation with replication-RPO and remote-side-effect consequences.

## Normal rollback

1. Under the approved operator identity, select each logical instance and pause delivery with owner/change/reason. Set the local external-write switch false everywhere and verify every serving worker. Fencing includes the old site before activating a replacement; lease expiry does not cancel an HTTP request already sent.
2. Record all active attempts and possible unknown outcomes. Permit graceful shutdown/recording; retain source-read-only reconciliation to search stable references and record observed state. A timed-out or killed process is not evidence of a failed remote write. Hold queued actions without extending their original four-hour lifetime.
3. Remove only the reviewed integration hooks/configuration from each existing application through rolling drain/install/restart/verify. Preserve all unrelated plugins, authentication, routing and media configuration. Do not delete `zabalack` or its tables. If source media changes must be reverted, use the reviewed live-export diff and retain original problem/recovery mapping; no broad automatic replacement is provided.
4. Keep source-read-only reconciliation and projection repair. The CLI `reconcile --read-only` may update database findings/projections, and it drains staged intents from live rows on every run (A5 stage two), page by page up to ten pages of `fast_budget` rows, so with the worker stopped the abandoned intents of removed acknowledgement paths still leave `public.alerts` before the application adapter is removed. Remove the adapter only after a run reports `retirement.complete: true`, which says a sweep ran, left nothing due behind it, and counted no row still carrying the key (`remaining: 0`); a staging inside its window and grace is left to a later pass by design, so a run that reports `remaining` above zero is repeated once the window and grace have passed (60 s plus `grace_seconds`, 300 by default; the run reports `retry_at`, the time by which every row it counted can be due, and the retirement slot is brought forward to no later than it, so that retry is not refused as `not_due` by the interval while the worker is stopped) — the adapter outlives every such row; a run that stops with work remaining (`continues`), or whose sweep fails, exits non-zero (`error_class` `RetirementError`) and is not that cleanup, and a run that found the slot not due (`skipped: not_due`, another caller's interval) reports `complete: false` without failing; for a completely read-only DB inventory use `status`/`doctor` with the intended role. Review expired work and deliberate source unacknowledgement as drift, without automatic re-acknowledgement.

`deploy/rollback.py` generates a reviewable plan by default. It performs only selected audited pauses when `--apply` is supplied; `--stop-worker-service` is a separate explicit option. It never deletes evidence or disables the trigger. Example for a later authorised change:

```sh
python deploy/rollback.py --config /reviewed/operator.json --instance SELECTED_INSTANCE --owner OPERATOR_REFERENCE --change-ref CHANGE_REFERENCE
# Review the plan; only then rerun with --apply if that change is authorised.
```

Re-enabling after rollback is a new gated change, not an automatic restart behavior. Reuse the existing action for controlled retries only when still eligible; new explicit human actions follow ordinary alarm-model transitions.

If a remote attempt remains unresolved after read-only reconciliation, do not force a retry or edit its evidence. The `settle-unknown` operator control requires independent fencing/drain/no-effect evidence and an explicit confirmation, as documented in `WORKER_IMPLEMENTATION.md` and `runbook.md`. It preserves the original action lifetime and never sends a source write; a separate retry is needed only for an unexpired eligible action.

## Capture bypass when mandatory evidence capture prevents eligible local Ack

Delivery pause and capture bypass are different controls. Capture bypass intentionally permits local-only acknowledgements with `skipped_capture_bypass` projection. Record the change and named principals from G7 before activation.

1. Pause outbound delivery and hold queued work under the operator role.
2. Distribute a new plugin config revision with `capture_bypass=true`, `bypass_change_ref` and external writes false. Drain inconsistent/unconfirmed API nodes; verify every serving node uses bypass before changing database capture behavior. Do not interpret a staged projection as a saved acknowledgement.
3. Preferred DB path: the approved `zabalack_operator` invokes CLI `bypass --enabled --owner ... --change-ref ...` with the same config revision. Its restricted audited function sets the capture-disable flag. The trigger remains attached. A **marked** candidate entering the function may produce `skipped_capture_bypass` diagnostics. A marker-free acknowledgement does not invoke a marker-dependent trigger; its evidence is saved history, projection and change audit.
4. If the flag/function/configuration/diagnostic path is broken, the separately approved principal holding actual `alerts` ownership authority may execute the reviewed `sql/rollback/disable_trigger.sql`. `zabalack_owner`, worker and probe do not hold that authority. A `TRIGGER` grant alone is insufficient. A physically disabled trigger creates no diagnostics at all. Record this gap in the change audit and inspect saved history.
5. Repair and validate capture under exact identities while acknowledgement paths stay drained/bypassed. Use the separately controlled enable script, turn the DB flag off through the audited operator function, verify health and execution canaries, then restore identical non-bypass plugin revisions across the fleet. Clear stale health caches. Resume delivery only through the gated enablement procedure after expiring stale work and reconciling unknown outcomes.

The flag route is not guaranteed to work when the capture function itself is broken. The physical route is break-glass, never a worker self-repair feature. See `final_review_notes.md`, T20/T21/T22/T39 and `deployment_acceptance.md`.

## a6 schema-3 hold

Migration 007 fixes settled-work expiry and cannot be removed by a Python
rollback. Preserve migrations and ledger data, keep global external writes
disabled, and hold workers. Earlier workers require schema 2 and are deliberately
incompatible with schema 3. Do not change migration checksums or undo source
acknowledgements. See `docs/deployment.md` for explicit readiness resealing and
a separately reviewed compatible forward recovery.

## 008 generation fence (schema 4)

Migration 008 (A5 stage two) replaces the projection guard that read the staged
intent with the append-only fence `zabalack.alert_generations`, and the signer
records every staging through `zabalack.stage_generation()`. Three consequences
for rollback:

1. **Roll the SQL back before the plugin, or neither.** A pre-008 plugin stages
   without recording a generation. On an alert that already carries a
   staging-recorded generation, its later acknowledgements are recorded only when
   they capture, at the observation tier, which never outranks a staged one — so
   their projection is refused until an acknowledgement goes through the
   protected operation again. Reverting `project()` to its 002 form restores a
   guard that reads an attribute the sweep has removed, so a rollback of
   `project()` must also stop the sweep (`retire_intents`), and it must be done
   under `zabalack_owner` as a further forward migration, never by editing 008.
2. **Nothing deletes a fence row, including a rollback.** The table stays; a
   later re-application of the fence design picks its generations up unchanged.
3. **008 applies only to a schema-3 installation, and refuses anything else.**
   `zabalack.schema_migrations` is keyed by *filename*, so the runner's drift
   check speaks only for a file it has already seen. An installation carrying
   the superseded `008_intent_retirement.sql` .. `013_seed_ledger_on_verification.sql`
   set has none of those names in this tree and reports schema 9; without a
   precondition the runner would apply `008_generation_fence.sql` cleanly on top
   of it — over the ledger objects this design replaces — and then set the
   recorded version back to 4. 008 therefore refuses any incoming version but 3,
   naming that case. Such an installation needs a forward migration written for
   it; this file is not that migration and must not be applied to it. An
   installation that applied an *earlier form of this same file* is refused
   already, by the runner, on the digest.
4. **The application grant is inert without the function.** `USAGE` on the
   schema plus `EXECUTE` on one function confers nothing else; it may stay.
   Re-seal the readiness manifest after any catalog change, as
   `13_reseal.sql` does, or every worker reports `functions_ok` false.

Workers pinned to schema 4 hold when the version is moved back; earlier workers
require schema 3 and are deliberately incompatible with 4.

## 009 abandoned-attempt sweep (schema stays 4)

Migration 009 replaces `worker_call()` with 008's body carrying one wider
predicate: `reconcile_due` fences an action whose attempt against an expired
lease generation is still unsettled, in any of the non-terminal states a
delivery can be abandoned in, rather than in `delivery_claimed` alone. It ships
no new structure, and the recorded schema version deliberately stays 4, so a
worker pinned to 4 runs against a tree with or without it.

1. **Rolling it back returns the gap, not a safe older behaviour.** Without it,
   an action requeued by an operator `retry` while its delivery lease was live
   keeps `uncertain=false` after the send is abandoned: the reconciler's fast
   recovery never examines it, `settle_unknown` refuses it because it is not
   uncertain, and once the execution deadline passes the expiry statement
   records it terminally `expired` — an outcome nobody observed. Prefer a
   forward migration to reverting this one.
2. **It re-records `installation.routine_bodies_md5`.** No seal writes that
   column; `capture_health()` and `13_reseal.sql` compare the live routine
   digest against it. Replacing `worker_call()` again — by rollback or by a
   later migration — must re-record it in the same transaction that changes the
   body, or every worker reports unhealthy. 009 refuses to run at all unless the
   digest it finds already matches what 008 recorded, so drift cannot be carried
   across it and blessed as shipped.
3. **Nothing it writes is undone by removing it.** Actions it fenced stay
   `unknown`/uncertain, which is the truthful record of a send whose outcome was
   never observed; they are settled through the ordinary fenced-and-settled
   operator route.

