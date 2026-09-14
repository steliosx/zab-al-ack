# Non-destructive rollback and emergency bypass

**a9 namespace change:** read [RENAME.md](RENAME.md) before installation or rollback. This release uses a fresh `zabalack` database namespace and refuses an existing Ackline schema.

Rollback preserves the ledger, capture records, pending entries and source state. Never drop `zabalack`, delete queued evidence, auto-replay on restart, reset an action lifetime, or unacknowledge Zabbix to undo the integration. Restoring a database backup is a separately approved recovery operation with replication-RPO and remote-side-effect consequences.

## Normal rollback

1. Under the approved operator identity, select each logical instance and pause delivery with owner/change/reason. Set the local external-write switch false everywhere and verify every serving worker. Fencing includes the old site before activating a replacement; lease expiry does not cancel an HTTP request already sent.
2. Record all active attempts and possible unknown outcomes. Permit graceful shutdown/recording; retain source-read-only reconciliation to search stable references and record observed state. A timed-out or killed process is not evidence of a failed remote write. Hold queued actions without extending their original four-hour lifetime.
3. Remove only the reviewed integration hooks/configuration from each existing application through rolling drain/install/restart/verify. Preserve all unrelated plugins, authentication, routing and media configuration. Do not delete `zabalack` or its tables. If source media changes must be reverted, use the reviewed live-export diff and retain original problem/recovery mapping; no broad automatic replacement is provided.
4. Keep source-read-only reconciliation and projection repair. The CLI `reconcile --read-only` may update database findings/projections; for a completely read-only DB inventory use `status`/`doctor` with the intended role. Review expired work and deliberate source unacknowledgement as drift, without automatic re-acknowledgement.

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
