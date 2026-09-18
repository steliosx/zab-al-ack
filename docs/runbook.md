# Engineer runbook

See the [illustrated lab walkthrough](visual-guide.md) for selected CLI, action
and ledger output, with explicit evidence limits.

**a9 namespace change:** read [RENAME.md](RENAME.md) before installation or rollback. This release uses a fresh `zabalack` database namespace and refuses an existing Ackline schema.

The ledger is authoritative for delivery; alert attributes are a generation-guarded view. A saved Alerta acknowledgement can remain pending during source outages. Operators must not repeatedly click Ack to force delivery: the reference alarm model returns HTTP 409 on Ack-on-Ack. A retry reuses the existing action and does not extend its lifetime. The worker always targets the frozen original problem ID; never select “latest event”.

## Existing UI

Configure the existing Web UI's attribute columns to display `zabbix_ack_sync` and `zabbix_ack_sync_reason` if the deployed UI supports the reference setting. Keep the action/generation fields available in alert detail for diagnosis; do not expose actor-to-person mappings or proof material in public widgets. No UI fork is required or installed. Verify the actual column setting/filter syntax under G1/G6.

| Projection | Meaning | Operator action |
|---|---|---|
| Missing | Unknown / no integration result | Inspect eligibility; never treat as successful sync |
| `pending` | Acknowledged locally; source processing/hold remains | Continue workflow; inspect reason and oldest age |
| `ok` | Write response or source acknowledgement observed | Read completion evidence before attributing it to this action |
| `failed` | Invalid intent, policy/validation, credential or permanent API problem | Assign the integration owner; correct the cause before a controlled retry |
| `skipped` | Local-only mapping/actor/route/ambiguous/bypass case | Use the exact verified source event manually if needed; do not guess |
| `expired` | Four-hour action execution window ended | NOC shift lead reviews; automatic retries cannot refresh age |

`already_acknowledged` / `source_ack_observed` means no extra comment or write. It does not identify the actor who acknowledged Zabbix. A missing projection target is retained as `projection_target_missing`; it must not recreate the alert or repeat delivery. A generation mismatch means an older result cannot label a newer action successful.

## Capture function failure modes

`zabalack.capture_ack()` classifies malformed markers and intents as bounded diagnostics (`diagnostic_reason`) and never raises for them. It does raise, and therefore rolls back the acknowledgement `UPDATE`, when the `zabalack.installation` singleton row is missing or when the `zabalack.captures`/`zabalack.outbox` insert itself fails: an enrolled Zabbix alert then cannot be acknowledged in Alerta (HTTP 500 from the acknowledgement route) until the installation is repaired. This is deliberate fail-closed behaviour for mandatory capture; the capture-health probe rejects eligible acknowledgements earlier when `capture_health()` cannot read the row. Repair the `zabalack` schema (`sql/migrate.py`) or use the §5.8 bypass procedure; do not disable the trigger to clear the symptom without the break-glass authorisation.

## Commands and least privilege

Always select the reviewed JSON config explicitly. `config-check` is offline and redacted. `status`, `doctor`, `verifier-health`, and `inspect` use the configured worker DB identity; their intent is inspection, not policy changes. Dedicated inventory tools remain separate. Operator commands require `operator_dsn_file`, complete target selection, owner reference and change reference; worker/probe identities do not acquire those rights.

```sh
zabalack --config /reviewed/config.json config-check
zabalack --config /reviewed/config.json status
zabalack --config /reviewed/config.json doctor
zabalack --config /reviewed/config.json verifier-health
zabalack --config /reviewed/config.json inspect --state held --limit 50
zabalack --config /reviewed/config.json inspect --state failed --limit 50
zabalack --config /reviewed/config.json inspect --state expired --limit 50
zabalack --config /reviewed/config.json reconcile --read-only --once
zabalack --config /reviewed/config.json reconcile --read-only --nightly
```

Reconciliation is source-read-only but records DB outcomes and may repair projections. It uses bounded due work; an empty queue must cause no Zabbix read. Every reconciliation cycle, the worker's and the standalone command's alike, also asks the database to retire staged intents (`retire_intents`, A5 stage two), a local operation that sends no source write; with the worker stopped, the standalone unit is what keeps abandoned intents from staying on live rows. The standalone command drains page by page, up to ten pages of `fast_budget` rows per run, and reports `retirement.complete: true` only when a sweep ran, left nothing due behind it, and no row still carries the key (`remaining: 0`) — a staging inside its window and grace is left to a later pass, and the adapter must outlive it; a run that stops with work remaining, or whose sweep fails, exits non-zero rather than reporting the cycle complete. The worker normally schedules the 60-second fast cycle, bounded nightly sweep and 15-minute approved-host inventory. The standalone reconciliation unit is one-shot; the supplied timer is optional and is never installed or started automatically.

Examples for a **later authorised** operational change:

```sh
zabalack --config /reviewed/operator.json pause --instance SELECTED_INSTANCE --owner OPERATOR_REFERENCE --change-ref CHANGE_REFERENCE --reason 'planned maintenance' --expected-end 2026-09-10T12:00:00Z
zabalack --config /reviewed/operator.json resume --instance SELECTED_INSTANCE --owner OPERATOR_REFERENCE --change-ref CHANGE_REFERENCE
zabalack --config /reviewed/operator.json retry --action 00000000-0000-4000-8000-000000000001 --owner OPERATOR_REFERENCE --change-ref CHANGE_REFERENCE
zabalack --config /reviewed/operator.json repair --action 00000000-0000-4000-8000-000000000001 --owner OPERATOR_REFERENCE --change-ref CHANGE_REFERENCE
```

The UUID above is a synthetic placeholder. Use the recorded action UUID, never mint a replacement for a retry. Inspect the command's `--help` in the exact package version for guarded enable/disable/circuit-reset controls; no resume or retry is a substitute for permission repair, cohort revision approval or expiry review.

An unresolved remote attempt cannot be retried merely because a reference is absent. After independently verifying that the old sender is fenced, remote processing is complete and exact-event state/history show no effect, the authorised operator may use `settle-unknown --action UUID --disposition no-effect-confirmed --evidence-ref REVIEWED_EVIDENCE_REFERENCE --confirmation REMOTE_ATTEMPT_FENCED_AND_SETTLED --owner OPERATOR_REFERENCE --change-ref CHANGE_REFERENCE`. This records an audited operator assertion, not a software proof of remote cessation, and sends nothing. An unexpired action then requires a separate retry; an expired action remains expired and needs a new explicit human action. See `WORKER_IMPLEMENTATION.md` for the full evidence procedure.

## Failure handling

| Observation | Required response |
|---|---|
| Capture-health/probe failure | Eligible Ack fails closed. Noneligible local activity remains available. Inspect writer/deployment/schema/trigger identity and bounded probe failure; no stale successful refresh fallback |
| Token expired, permission regression or bad CA | One incident per logical instance/failure class; pause/hold the affected instance, validate corrected references/permissions and reset its circuit explicitly |
| API timeout after send, lost response or worker crash | Mark outcome unknown. Reconcile the stable neutral reference before another action=6 write. Absence of a reference alone does not prove the old request stopped |
| `action=2` ambiguous outcome | Source state may be observed, but exact attribution is unavailable; never invent a reference or guarantee exactly-once effects |
| Host ID missing/recreated or complete host set differs | Keep the existing approval unchanged; fail validation and require a reviewed cohort revision. Do not substitute by hostname/IP |
| Maintenance pause | Pending hold, no failure count increment; age continues. Resume expires stale actions before normal bounded drain |
| Source deliberately unacknowledged after delivery | Record drift; do not automatically re-acknowledge |
| Source event no longer visible | Quarantine and investigate. Inaccessibility does not independently prove deletion/housekeeping |
| Projection lag / absent target | Repair from ledger through its restricted function. Never replay the source side effect to repair UI state |
| Acknowledgement failed after its staging committed (signer or capture 503 with `zabbix_ack_sync=pending`, `awaiting_capture`) | The staging's generation is recorded and permanent; the earlier action's re-projection is refused (`projection_action_generation_mismatch`) until an acknowledgement goes through the protected operation again. Fix the cause, then acknowledge again; the sweep retires the stale intent. Never edit the fence |
| Site failover | Fence old side-effecting consumers before activating the replacement; retain unknown attempts and account for replication RPO |

During an urgent source outage, the NOC may acknowledge the **exact verified event** directly in Zabbix under its existing authority and record the action. Reconciliation can observe acknowledgement without attribution. Never send engineer notes, actor names, internal alert/action IDs or proof markers into the customer-visible neutral comment.

## Monitoring, ownership and scheduled checks

Use existing monitoring with worker structured logs, status snapshots and the declarative `zabbix/monitoring.rules.yml` thresholds. The rule names are a mapping contract; the package does not start an unsolicited HTTP exporter or new dashboard. Adapt collectors to actual emitted counters/status, validate names, and attach approved destinations under G7. Pending over 2 minutes warns; over 5 minutes alerts. Credential/permission/capture-health/verifier-key failures are immediate. Group incidents by instance plus failure class, never one page per action. Backup after 10 minutes unowned, then duty manager per the approved roster. Use an out-of-band route when the failing component is itself on the alerting path.

Run authenticated synthetic `event.get` canaries and separately authorised synthetic acknowledgements after deployment, rotation and on the approved schedule. Do not use anonymous API version lookup as authentication evidence. Keep canaries under the separate background-read budget and approved destinations. Token policy starts with 90-day expiry/rotation before day 60; the actual token record and owner are deployment inputs.

For failure/HA/batch/notification acceptance use `deployment_acceptance.md` and T1–T41/T11a/T11b mapping in `acceptance_matrix.csv`. For emergency capture bypass and rollback use `rollback.md`. Retention/deletion remains disabled until G8 approves the policy; do not clean up captures or identity mappings merely to make storage graphs look healthy.
