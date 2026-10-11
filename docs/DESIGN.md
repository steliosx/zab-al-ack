

Current-name rendering for Zab-al-ack a9; revision 1.2 architecture is unchanged. Namespace changes and the retained historical rendering are documented in [RENAME.md](RENAME.md) and release amendment R13.
This is the public, site-neutral rendering of design revision 1.2. Read it with `final_review_notes.md` and `SPEC_AMENDMENTS.md`. Site labels and secret-manager references are generic; implementation scope and acceptance requirements are preserved.

# Alerta → Zabbix acknowledgement write-back — release-one design baseline

| Field | Value |
|---|---|
| Status | **Design baseline frozen; deployment validation pending** |
| Baseline | Release-one design revision 1.2, including actor-authority and final-review interpretations |
| Frozen | 2026-09-08 |
| Revision | 1.2 — consolidation errata (1.1) and privilege clarifications (1.2) applied 2026-09-08 (see Errata table) |
| Reference versions inspected | Alerta server v9.1.0, Web UI revision `e6eb99e`, Zabbix 7.0 API, PostgreSQL 18 documentation, PgBouncer current documentation |
| Deployed versions | **Not verified.** See gate G1. |

### Errata applied in revision 1.1

| # | Section | Correction |
|---|---|---|
| E1 | §5.3, §5.5, T14 | Candidate detection separated from binding validation; malformed markers reach diagnostic capture. |
| E2 | §4.5, §5.6, T36 | Repeat Ack on an already-acknowledged alert is rejected by the reference alarm model (HTTP 409); "coalesce" assumption removed. |
| E3 | §4.1, §6.2, T11 | Trusted-operator limitation restored; T11 narrowed to the actor's authorised scope. |
| E4 | §6.1, §6.2 | Verification lease distinguished from delivery lease; forged candidates cannot consume instance delivery capacity. |
| E5 | §8, §12 | Projection-function owner's underlying table privileges specified; `zabalack_owner` description corrected. |
| M1–M4 | §5.3, §9, §10, §12, Appendix B | Topology labelled as required/unverified; SQL-outbox polling stated explicitly, bus non-authoritative; capture-record allowlist separated from export allowlist; reference manifest. |
| E6 (1.2) | §8, §12, T38 | Source-identity fields are JSON keys inside `attributes`, not protected columns; `UPDATE (attributes)` does not restrict keys; T38 boundary corrected. |
| E7 (1.2) | §5.8, §12, §13, T39 | Zab-al-ack-object migrations separated from trigger attachment and enable/disable authority; bypass runbook names the principal able to disable capture. |

Architecture, failure policy, pilot boundary and gates G1–G8 are unchanged by revisions 1.1 and 1.2.

This document is the single current implementation contract. Later accepted decisions supersede earlier proposals; superseded alternatives are listed only in Appendix A. Statements about upstream behaviour refer to the inspected reference versions, not to the installed environment. Nothing in this document is evidence that the callback is installed, that installed versions match the inspected source, or that any acceptance test has passed.

---

## 1. Current state and scope

### 1.1 Evidence position

Zabbix → Alerta ingestion is verified. Alerta → Zabbix acknowledgement write-back has not been evidenced in the supplied deployment information. Treat it as unavailable until configuration inspection and an end-to-end test establish otherwise. The earlier `zbx-alerta-bridge` design was intended architecture, not a deployment record. The required inbound fields have not been confirmed in the current media-type export or in real-event payloads.

### 1.2 Release-one definition

> For an allowlisted, one-to-one Zabbix alert cohort, an authorised human acknowledgement through the tested Alerta `/action` path atomically creates durable action evidence for one original problem event. A continuously running, per-instance-limited worker verifies the signed binding, validates the exact frozen target against Zabbix and the approved cohort, performs the acknowledgement write, records the API outcome and projects its status. Unsupported routes, actors, mappings and multi-member cases remain visible and local-only.

### 1.3 In scope

- Authorised human acknowledgements via the single-alert `/action` route (UI or personal authenticated API session).
- One original Zabbix problem event per eligible Alerta alert.
- Acknowledge plus neutral audit comment (`action=6`), or acknowledge only (`action=2`) where customer policy disallows the comment.
- Durable capture, asynchronous delivery, result projection, reconciliation, capture-health gating, host-drift detection.

### 1.4 Deferred to later releases

- Multi-member acknowledgement (several source problems per Alerta alert) and the membership store.
- Webex/chatops and service-account acknowledgements.
- Native `/_bulk/alerts/action` and bulk status routes.
- Symptom-only NOC view filtering.
- Mirroring Alerta unacknowledgement, close or delete to Zabbix.
- Zabbix Update operation → Alerta import (opposite direction) with loop prevention.
- Wake-up notification (`pg_notify`) for the worker.

### 1.5 Ownership split

Zabbix owns monitoring and source-event state. Alerta owns operator workflow and alert-family history. The Zab-al-ack ledger in the Alerta database is authoritative for acknowledgement delivery; Alerta attributes are projections.

---

## 2. Architecture

```text
Zabbix problem / update / recovery
        │  versioned media type (per-fact payloads)
        ▼
Alerta API ── pre_receive normaliser (validate, strip reserved fields, attach eligibility)
        │
Engineer acknowledges (single-alert /action)
        │
        ├─ take_action     : route, actor, cohort, capture-health checks; action_id; unsigned intent
        ├─ status_change   : (final effective callback) append signed proof to history text
        └─ set_alert       : one UPDATE writes status, attributes and new `ack` history entry
                │
                ▼
   AFTER UPDATE trigger → zabalack capture function (SECURITY DEFINER)
                │  same transaction: capture record + outbox row
                ▼
   Polling worker (250 ms idle) ── per-instance leases
        ├─ verify signed binding
        ├─ resolve cohort (customer, instance, trigger → host set)
        ├─ event.get   : validate exact frozen event
        ├─ event.acknowledge
        └─ result → ledger → zabalack projection function → alert attributes
                │
   Reconciler (60 s fast cycle, nightly sweep, 15 min host drift)
```

### 2.1 Components

| Component | Location | Responsibility |
|---|---|---|
| Zabbix media type (revised, versioned) | Zabbix | Emit problem/update/recovery facts with the source-identity contract (§3). |
| `pre_receive` normaliser plugin | Alerta API nodes | Validate contract, strip reserved integration fields from producer input, attach eligibility from controlled configuration snapshot. No external lookups. |
| Acknowledgement/signing plugin | Alerta API nodes | Route/actor/cohort/health checks in `take_action`; signed proof in final `status_change`. |
| Zab-al-ack schema (`zabalack`) | Alerta PostgreSQL database | Capture trigger and function, capture records, outbox/ledger, execution leases, cohort configuration, installation metadata, projection and claim/completion functions, `capture_health()`. |
| Delivery worker | Both sites; reaches every instance endpoint it serves | Verification, validation, delivery, result recording, projection. |
| Reconciler | Worker process or sibling | Fast cycle, nightly drift sweep, host-inventory drift, projection repair. |
| Capture-health probe | Inside the acknowledgement plugin | Cached read-only check via plugin-owned connection (§5.7). |
| Endpoint registry | Worker configuration | `zabbix_instance → endpoint → token reference → CA bundle`. |

### 2.2 Delivery model

Asynchronous only in release one. A bounded inline first attempt remains technically valid and may be reconsidered only if measurement shows asynchronous dispatch, not Zabbix or the UI, prevents the required responsiveness. Target p95 single-alert delivery under one second, subject to measurement. Reconciliation repairs exceptions; it is not the delivery mechanism.

---

## 3. Inbound contract

### 3.1 Required fields

| Field | Contract |
|---|---|
| `source_system` | Controlled value `zabbix`, validated against the authenticated ingest identity. |
| `zabbix_instance` | Stable logical Zabbix identity; not an endpoint URL. |
| `zabbix_event_id` | Original **problem** event ID of the latest occurrence. Selects the candidate target; **never** the authority for the delivery target. |
| `zabbix_trigger_id` | Trigger identity; selects the cohort entry. |
| `zabbix_cause_event_id` | Validated `{EVENT.CAUSE.ID}` where applicable; otherwise null. |
| `zabbix_recovery_event_id` | Separate recovery identity; never substituted for the problem ID. |
| `source_contract_version` | Version of the media-type/normalisation contract. |

Unresolved macro literals are not valid numeric IDs. The pilot must cover problem, update and recovery operations and symptom events.

### 3.2 Normalisation rules

- Alerta identity keeps the existing stable trigger/family key. `event` is not changed.
- `pre_receive` strips the reserved namespace (`zabbix_ack_sync*`, intent and proof fields) from producer input.
- Alerts created before the contract revision lack the fields: `skipped` with reason `legacy_missing_mapping`, logged rate-limited and structured. The acknowledgement still succeeds locally.
- Non-Zabbix alerts bypass the integration silently.
- PostgreSQL deduplication merges attributes at the top level: incoming keys overwrite, other keys survive. Ingestion owns source-identity fields; the integration owns `zabbix_ack_sync*`.

### 3.3 Tenant gate

```text
AUTH_REQUIRED  = True
CUSTOMER_VIEWS = True

Ingestion identity: customer-bound; minimum ingestion scope; no admin or admin:alerts scope.
Eligible alert:     non-null approved top-level customer, agreeing with the authenticated ingestion mapping.
```

In the inspected code `assign_customer()` returns a payload-supplied customer for admin-scoped credentials regardless of `CUSTOMER_VIEWS`. A populated customer field is therefore not evidence of enforcement; configuration and behavioural tests are both required. Existing null-customer alerts remain local-only until their mapping is validated.

---

## 4. Eligibility and authorisation

### 4.1 Cohort configuration

```text
(top-level customer, zabbix_instance, zabbix_trigger_id)
    → approved host-ID set
    → cohort policy and revision
```

The alert ID is part of the immutable action snapshot and signature but is not the enrolment key. Source attributes select a candidate cohort entry; the persisted top-level `customer`, the actor's authenticated customer scope and the cohort configuration authorise it. `attributes.customer` never substitutes for the top-level field.

Release-one eligibility: one eligible Alerta alert represents one unambiguous original problem event at acknowledgement time, with no multi-trigger or multi-source fan-in. Both Zabbix problem-generation mode and Alerta correlation rules must be inventoried; single-event triggers alone are not sufficient. Anything else returns `skipped_ambiguous_source`.

### 4.2 Actor authority (final clause)

Alerta's authenticated customer scoping is the authority for the operator's customer access. The write-back plugin confirms that the alert's persisted top-level customer is within the authenticated actor's customer scope; it does not maintain a separate identity-to-customer authorisation database.

For non-administrative operators, the plugin requires the alert customer to be present in the authenticated customer scope. For administrative operators, cross-customer write-back requires an explicit approved exception policy; administrative access does not bypass the integration's eligibility checks.

Cohort configuration answers a different question: whether this customer, Zabbix instance, trigger and host set are approved for automated acknowledgement write-back. Customer access does not by itself make every source event eligible. Null or ambiguous customer ownership is ineligible.

**Limitation (release one).** Release one verifies that the frozen target belongs to a cohort authorised for the authenticated operator. It retains a trusted-operator boundary within that authorised scope and does not claim tamper-proof historical ingestion provenance: an operator authorised for several cohort entries of the same customer could select a valid event from another approved trigger, and no protected alert-to-trigger lineage store exists to detect that. The pre-read validates the requested source event and its associations, not which ingestion request created the Alerta record. The worker never substitutes another event or searches for the latest event.

### 4.3 Actor policy

| Actor | Write back | Attribution |
|---|---|---|
| Authenticated human via UI or personal authenticated API session | Yes | Verified user identity (private ledger only). |
| Approved Webex bot on behalf of a mapped human | Deferred | — |
| Approved autonomous service account | Deferred | — |
| Unknown or shared API key | No | `skipped_actor_policy`. |
| Zabbix ingestion/update credential | Never | Imported source activity; no return callback. |

For API-key authentication the reference code sets `g.login` from the key's owner and `g.user_id` to `None`; that attributes to a credential owner, not to a person.

### 4.4 Supported entry paths

| Path | Release-one decision |
|---|---|
| UI single-alert acknowledgement → `PUT /alert/<id>/action` | Supported. |
| API `PUT /alert/<id>/action` | Supported; same guarantees. |
| UI multi-select | Supported only after confirming the deployed UI issues per-alert `/action` requests with the same actor context. |
| `PUT /alert/<id>/status` | Local-only. Produces `status` history, not `ack`; no signing context. Scripts migrate client-side to `/action`. |
| `/_bulk/alerts/action`, bulk status | Excluded. No signing context is created on these routes, so no proof and no executable capture. Load tests use bounded individual `/action` requests. |
| Chatops | Excluded. |

Reference hook sequence for `/action`: `take_action` → transition → `status_change` → save → `post_action`. The `/status` route calls `status_change` only. Native bulk may invoke action and status processing without `post_action`.

### 4.5 Transition table

| Alerta activity | Zabbix write-back |
|---|---|
| Authorised explicit human acknowledgement (`/action`) | Acknowledge the frozen problem event. |
| Shelve, blackout | None. |
| Unshelve, including restoration to `ack` | None. |
| Acknowledgement expiry | No Zabbix unacknowledgement. |
| Severity-change reopen / auto-unack | No Zabbix unacknowledgement. |
| Duplicate-driven status reset | None. |
| Manual unacknowledgement in Alerta | None in release one; limitation visible. |
| Close, delete, recovery | No source closure. Recovery is an inbound monitoring event. |
| Acknowledgement imported from Zabbix | None. |
| Repeat Ack request while already acknowledged | Preserve the installed alarm model's response. In the reference default model (v9.1.0) `ack` on an `ack` alert raises `InvalidAction`, which the `/action` route returns as HTTP 409; no new acknowledgement history entry is written and the trigger has nothing to capture. Existing delivery work is unchanged; no new source effect, no expiry extension. Installed alarm model to be verified under G1. |

Repeated UI/API acknowledgement requests, replayed captured work and explicit delivery retries are three separate operations. Captured work is deduplicated through the action and effect identities (§5.6). A delivery retry reuses the existing action. A rejected repeat request is never represented as a committed acknowledgement, and the trigger is not assumed to capture rejected requests. Alerta is not patched to make repeat Ack succeed.

Handlers act on action provenance and the signed binding, never on `new_status == ack` alone.

---

## 5. Acknowledgement capture

### 5.1 Plugin stages

| Stage | Responsibility |
|---|---|
| `take_action` | Validate route, authenticated actor and customer scope, cohort eligibility, capture health. Generate `action_id`. Build unsigned intent. Store request-local context `g.zabalack_context[full_alert_id][action_id]`. **Do not append the proof.** |
| Final effective `status_change` | Require exact context match for this alert and pending action and the final `ack` transition. Validate the final intent against the prepared context. Append the signed proof to the text returned for history persistence. |
| Request teardown | Discard the context. No context is passed to the worker; the committed capture record is the worker's input. |

Rationale: `from_action()` passes `text` through the status-change hook and uses the returned text in the history entry, so a proof added in `take_action` would reach every status-change plugin before commit. Attributes modified by plugins are persisted before the acknowledgement history write, so an intent attribute is not proof that the acknowledgement committed.

Context invariants: full canonical alert ID; no fallback between alerts; existence of the dictionary does not enable excluded routes; duplicate hook invocation for the same action creates no new identity and no second proof.

Plugin ordering: the signer must appear once in the **effective routed** status chain for every eligible cohort, and every plugin after it must declare the coexistence contract as class attributes: `ACK_FOREIGN_SOURCE_PASSTHROUGH = True` and `ACK_OWNED_SOURCE_SYSTEMS` (a non-empty frozenset of the `source_system` values it owns) that does not contain this alert's source, so the alert is provably foreign to it and it returns it unchanged; a trailing plugin without both declarations, or one that owns the same source, blocks signing, as before. Position in `PLUGINS` alone is not sufficient. Routing rules may reorder plugins; contract tests assert the effective order. Other plugins are audited for pre-commit use of `text` (HTTP calls, logging, exception formatting, tracing, signal subscribers).

### 5.2 Intent and proof

Intent payload (attribute, reserved namespace): `action_id`, authenticated actor, full Alerta alert ID, top-level customer, frozen source snapshot (`zabbix_instance`, `zabbix_event_id`, `zabbix_trigger_id`), approved route/action, cohort revision, policy version, issued-at and capture-expiry times.

Binding proof (history text, appended after the operator's note):

```text
[zabalack:v1 action=<uuid> kid=<key-id> mac=<authentication-code>]
```

The MAC covers the intent payload with a fixed deterministic encoding. HMAC key material is available to the signing plugin and the verifying worker; an asymmetric scheme (private key on the plugin, public key on the worker) is an optional hardening. No signing key in PostgreSQL. Verification uses constant-time comparison. Key rotation retains verification for the outstanding-work window without extending the four-hour execution lifetime.

The proof is visible in internal Alerta history after commit. It is not forwarded to Zabbix, is redacted from external notifications and ordinary logs, and must not leak through provisional attributes or response diagnostics before commit.

### 5.3 Trigger predicate

`AFTER UPDATE` trigger on `alerts` with a `WHEN` clause so ingestion updates do not enter capture processing. Capture requires all of:

**Row-local `WHEN` predicate** (candidate detection; no subqueries are permitted in `WHEN`):

1. A **newly written** head history entry: `NEW.history[1] IS DISTINCT FROM OLD.history[1]` on `update_time`, `type` and `text`, so an absent previous entry is handled deliberately. Array length is not used; it is constant once `HISTORY_LIMIT` is reached.
2. `NEW.history[1].type = 'ack'` (the ordinary ack action yields `ChangeType.ack`; generic `action` is the fallback for unknown names).
3. Resulting `NEW.status = 'ack'`.
4. A bounded indication of the reserved marker in the new entry's text (prefix match within a size limit). Syntactic or cryptographic validity is **not** a prerequisite for candidate processing.

**Capture function** (binding validation):

5. Cohort lookup against the `zabalack` cohort table happens here, not in `WHEN`. A marker on a non-Zabbix or non-enrolled alert does not enter the mandatory-capture path.
6. Within the enrolled cohort the function distinguishes well-formed candidates from malformed-marker, missing-intent and mismatched-intent cases. Malformed candidates (invalid UUID, truncated or oversized marker, unexpected intent shape) produce bounded diagnostic evidence, not exceptions. Only worker-verified candidates become executable work. A new acknowledgement without any marker remains local-only under the route policy in §4.4.

The capture function records `capture_id` (database-generated), the intent payload, the marker, `captured_at = clock_timestamp()`, and the **restricted capture-record allowlist**: alert ID, top-level customer, source snapshot fields, head history entry `type`/`update_time`/`user`, resulting status, and the proof context needed for verification. It does not copy the full alert, full history, the operator's free-text note or identity data beyond the authenticated login. The ledger/export allowlist (§12) is narrower still. The function performs no HTTP, bus calls, target lookups or retry loops.

### 5.4 Lifetimes

| Clock | Value | Measured from |
|---|---|---|
| Capture lifetime | 60 s (proposed) | Intent issuance to `captured_at`. Evaluated by the worker against the database-observed capture time, not against worker processing time. |
| Execution lifetime | 4 h | Original server-observed action time. Retries do not reset it. |

### 5.5 Failure policy

> External delivery failure leaves the saved acknowledgement pending. Inability to save mandatory local evidence prevents the eligible acknowledgement from committing.

| Captured condition | Local acknowledgement | External result |
|---|---|---|
| New `ack` entry with matching marker and intent | Proceeds if capture commits | Worker verifies before authorising delivery. |
| Marker present, intent absent | Proceeds if diagnostic capture commits | `failed_intent_invalid`, reason `intent_missing`; no Zabbix call. |
| Marker and intent action IDs disagree | Proceeds if diagnostic capture commits | `failed_intent_invalid`, reason `binding_mismatch`; no Zabbix call. |
| Marker malformed (invalid UUID, truncated, oversized) | Proceeds if diagnostic capture commits | `failed_intent_invalid`, reason `marker_malformed`; no Zabbix call. |
| Old staged intent present, new acknowledgement without proof | Local-only | Staged intent is neither consumed nor authorised. |
| Repeat Ack request on an `ack` alert | Rejected by the alarm model (reference: HTTP 409) | Nothing captured; existing work unchanged. |
| Mandatory capture/diagnostic insertion fails | Eligible acknowledgement transaction fails | No external work. |

Unrelated ingestion and non-eligible acknowledgements continue when capture is faulty; the trigger predicate scopes the blast radius. Swallowing the capture error would restore unconditional local fail-open at the cost of the atomic-capture guarantee; that is an alternative, not the selected behaviour.

### 5.6 Replay protection

- Uniqueness constraint on the verified action identity.
- Separate effect key preventing multiple actions or retries from duplicating the same source effect.
- `capture_id` distinct from action identity: a forged capture presenting a genuine `action_id` must not block the genuine action.
- No mutable `used` attribute as the authority; `set_alert()` and attribute routes can rewrite whole dictionaries.
- Deduplication applies to captured work and delivery retries. A repeat Ack request rejected by the alarm model produces no capture and needs no deduplication; where an audit of rejected repeat requests is required, it is recorded by the plugin or the API access log, not by the capture trigger.

### 5.7 Capture-health gate

Function `zabalack.capture_health()` (read-only, `SECURITY DEFINER`, controlled `search_path`) returns:

| Field | Healthy value |
|---|---|
| `in_recovery` (`pg_is_in_recovery()`) | `false` |
| `database_name` (`current_database()`) | Configured Alerta database |
| `deployment_id` | Configured logical deployment identifier (stable across the site-A/site-B pair; differs for unrelated installations). Application-defined; read from `zabalack` installation metadata. |
| `zabalack_schema_version` | Tested integration schema version |
| Trigger/function checks (`pg_trigger`: `tgrelid`, `tgfoid`, `tgtype`, `tgenabled`) | Expected table, function, firing configuration, enabled |
| `checked_at` | Database-observed time |

Operating policy:

| Setting | Value |
|---|---|
| Connection | Plugin-owned Psycopg connection to the same writer endpoint and database, probe identity `zabalack_probe`; initialised after fork; at most one per API process; not shared with Alerta's connection or transaction. |
| Call | `BEGIN READ ONLY; SET LOCAL statement_timeout = '500ms'; SELECT zabalack.capture_health(); COMMIT;` as separate client statements. Rollback on error; discard an unusable connection. |
| Cache | Per API process, keyed by database target and configuration revision; maximum successful age 5 s (monotonic clock). |
| Refresh failure or unhealthy result | Invalidate immediately; no last-known-good. |
| Reconnect, failover, configuration reload | Invalidate. |
| Eligible acknowledgement without fresh healthy evidence | Reject before save with a propagated `AlertaException` (generic exceptions may be suppressed by the dispatcher). |
| Non-eligible activity | Not blocked. |

The 500 ms value bounds the server-side statement only; connection acquisition and transport need their own bounds. Function-level `statement_timeout` is not a substitute: the timeout is armed when the client command starts. The five-second cache is a bounded stale window, not atomic protection; migrations drain or fence the acknowledgement path, invalidate caches, apply changes, run behavioural tests, then resume. The probe validates its own target; probe/application configuration alignment and the capture canary establish that Alerta writes through the intended installation.

### 5.8 Emergency capture bypass

Distinct from delivery pause. While active, the plugin issues no executable intent and projects `zabbix_ack_sync=skipped`, `zabbix_ack_sync_reason=skipped_capture_bypass`, with configuration revision and change reference. Order: pause delivery → distribute and confirm bypass configuration on all serving nodes (drain inconsistent nodes) → disable mandatory capture. Queued work is held, not erased.

Disabling mandatory capture has two mechanisms, in order of preference:

| Mechanism | Effect | Principal required |
|---|---|---|
| Capture-disable flag in the `zabalack` configuration table, read by the capture function at the start of each invocation | Trigger stays attached; function records `skipped_capture_bypass` diagnostics and inserts no executable work | Operator role with `EXECUTE` on the `zabalack` flag-setting function; audited with change reference |
| `ALTER TABLE alerts DISABLE TRIGGER <name>` | Trigger does not fire | Owner of `alerts` (or a role with that ownership authority). A `TRIGGER` grant alone is insufficient. Break-glass only. |

The runbook names the approved principal for each mechanism. The worker and probe identities hold neither. The UI interprets the bypass attribute together with saved history; a failed acknowledgement must not display as acknowledged because the attribute was staged earlier.

---

## 6. Delivery worker

### 6.1 States

Internal: `captured_unverified` → `verified` → `authorised` → `delivery_claimed` → terminal. Displayed: `pending` (reasons include `maintenance_hold`, `verifier_hold`), `ok`, `failed`, `skipped`, `expired`. Uncertain external outcome is recorded separately from ordinary retryable failure. Missing state displays as unknown, never `ok`.

Two distinct leases:

| Lease | Keyed by | Grants | Never |
|---|---|---|---|
| `verification_lease` | `capture_id` | Exclusive right to verify one captured candidate. Database-only coordination. | Endpoint or credential resolution; a delivery slot; any Zabbix call. |
| `delivery_lease` | verified action identity and source event | Per-event execution lease plus one per-instance delivery slot. | Issued to an unverified or unauthorised candidate. |

A forged or malformed candidate therefore consumes verifier time only, never a trusted instance's delivery capacity or its credentials.

### 6.2 Sequence

```text
1. Lease   : verification_lease on capture_id (short transaction; commit).
             No target endpoint or credential resolution from the untrusted payload.
2. Verify  : MAC over captured intent; actor/history consistency; capture within capture lifetime.
             Invalid MAC / malformed   → failed_intent_invalid (terminal, no Zabbix read or write).
             Key unavailable           → hold + verifier-health incident (not forgery).
3. Authorise: cohort entry for (top-level customer, instance, trigger); actor authority per §4.2;
             execution lifetime unexpired.
4. Claim   : delivery_lease — per-event execution lease and per-instance delivery slot, with owner,
             database time, expiry and fencing generation. Commit the short claim transaction; then HTTP.
5. Validate: event.get (params below). Require exact eventid, objectid = frozen trigger,
             returned host set = approved host set, current cohort policy.
6. Dispose :
             acknowledged = 1                 → ok, reason already_acknowledged, no write,
                                                completion_evidence = source_ack_observed
             acknowledged = 0, r_eventid = 0  → write
             acknowledged = 0, r_eventid ≠ 0  → write (original problem eligible; never the recovery event)
             empty result                     → not visible under filters/permissions; hold/investigate
             identity or host-set mismatch    → failed_validation (specific reason)
7. Write   : event.acknowledge (only when required by step 6).
8. Complete: JSON-RPC id matches, no error, result.eventids contains the ID → ok,
             completion_evidence = write_response. HTTP 200 alone is insufficient.
             No routine post-write event.get. Record the outcome; release or transition both leases.
```

Validation read (`method`/`params` portion):

```json
{
  "method": "event.get",
  "params": {
    "eventids": ["<frozen-problem-event-id>"],
    "source": 0, "object": 0, "value": 1,
    "groupids": ["<approved-customer-host-group-id>"],
    "output": ["eventid","objectid","source","object","value","clock","acknowledged","r_eventid"],
    "selectHosts": ["hostid"]
  }
}
```

`groupids` matches any one host of a multi-host trigger; the complete returned host set is compared against the approved set. `selectHosts` is not a group-membership inventory; approved host IDs come from cohort configuration. Later attempts require fresh validation; it may be combined with an ambiguous-outcome reconciliation read.

Acknowledgement write:

```json
{
  "jsonrpc": "2.0",
  "method": "event.acknowledge",
  "params": {
    "eventids": ["<frozen-problem-event-id>"],
    "action": 6,
    "message": "[zabalack] Acknowledged by the monitoring operations team.\nRequested at <UTC timestamp>. Reference: <opaque-public-reference>."
  },
  "id": 1
}
```

`action=6` = acknowledge (2) + message (4); no close or suppress. `action=2` where policy disallows the comment. One event per write. `Authorization: Bearer` to `/api_jsonrpc.php`. The opaque reference maps to the internal action in the ledger and is reused across attempts. The engineer's free-text note is not copied by default. Engineer identity, Alerta IDs and internal action IDs stay out of the comment.

### 6.3 Retry classification

| Outcome | Decision |
|---|---|
| Valid write response with the requested ID | Terminal `ok`. |
| Event already acknowledged (pre-read) | `ok/already_acknowledged`; no write; no `delivered_by_this_action`. |
| Timeout or connection loss after send | Outcome unknown; read state/history for the reference before any repeat. |
| HTTP transient / endpoint outage | Backoff and retry for that instance only. |
| Expired token, permission error, invalid CA, malformed payload | `failed`; suspend repeated writes until corrected. |
| Original problem resolved | Eligible within lifetime; completion is `ok`. |
| Event absent or inaccessible | Quarantine; do not assume housekeeping. |
| Deletion independently confirmed | `skipped_source_deleted`. |
| Validation mismatch | `failed_validation`; no write. |

Serialise writes per source event. Duplicate-comment reduction is strong but exactly-once remote execution is not claimed.

### 6.4 Operating limits (initial, unmeasured)

| Control | Value |
|---|---|
| Write and validation timeouts | 1 s connect / 3 s read / 5 s overall |
| Reconciliation read timeouts | 2 s / 15 s / 20 s |
| Write concurrency | 2 per logical instance, aggregated across all workers and sites; validation and write share the budget |
| Background read concurrency | 1 per instance; canaries share it; at most one request started per second per instance |
| Idle poll | 250 ms, backing off |
| Fast reconciliation | Every 60 s; due non-terminal work and expired leases only; 200-action budget; checkpoint remainder |
| Read batching | Up to 50 IDs for state; 10 when acknowledgement history is requested |
| Drift sweep | Nightly, cursor-based, over mapped live events and a defined recent terminal window |
| Host-inventory drift | Every 15 min, `host.get` by approved `hostids` with `selectHostGroups` |
| Execution lifetime | 4 h from original action time |

Throughput estimate: `actions/s ≈ 2 / (t_validation + t_write + t_overhead)` per instance. Increase write concurrency to 4 only on batch-test evidence.

### 6.5 Expiry, pause, leases

Expired work cannot initiate a write; an attempt that timed out may have executed and stays eligible for read-only outcome reconciliation. The NOC shift lead reviews expired work; a still-relevant acknowledgement needs a new explicit action.

Maintenance pause is a durable per-instance record (owner, change reference, reason, expected end). New work becomes `pending/maintenance_hold`; failure counters do not increment for the pause; in-flight outcomes are still recorded; age is not frozen. Resume: expire stale work, validate connectivity and credentials, drain at normal concurrency.

A lease does not cancel an HTTP request already sent by an old worker; failover uses the fencing procedure and abandoned attempts enter unknown-outcome reconciliation.

### 6.6 Host-drift dispositions

| Observation | Behaviour |
|---|---|
| Approved host present with expected identity | Retain approval. |
| Approved host absent | `cohort_host_missing_or_not_visible`; investigate. |
| Validation returns unknown host ID or different set | `failed_validation` with host-set reason; no write. |
| Host recreated under new ID | Requires approved cohort revision; no substitution by name or address. |
| Inventory request fails | Report check failure; allowlist unchanged. |

---

## 7. Zabbix-side requirements

- Dedicated user per logical instance (e.g. `svc-zabalack`); customer-visible display name appropriate to the neutral comment.
- Role: read access on relevant host groups; **Acknowledge problems**, **Add problem comments**; close, suppress, change severity, execute scripts disabled.
- API allowlist: `event.get`, `event.acknowledge`, `host.get`.
- Token: 90-day expiry, rotate before day 60, expiry warnings, replacement validated before retirement; the approved secret manager references only.
- Canary: authenticated `event.get` of a known synthetic event, plus a synthetic acknowledgement test at deployment, rotation and on schedule. Anonymous `apiinfo.version` and client-library `is_authenticated` are not credential tests.
- TLS: internal CA bundle on worker nodes, hostname validation, no `verify=False`; pinned client dependency with explicit timeouts.
- Permission coverage: every tenant/host-group boundary, multi-host triggers, deliberate exclusions. Permission errors are surfaced from the API response; they are silent only if not inspected.
- Escalation operations intended to stop on acknowledgement must be conditioned on the event remaining unacknowledged.
- Update operations (gate G3): inventory all matching actions; webhook/script senders receive `{EVENT.UPDATE.MESSAGE}` and suppress recognised write-back echoes before side effects; native email/command update operations leave pilot scope or are split by action; genuine human updates keep their notifications. Recipient rules and already-acknowledged no-ops reduce, but do not remove, this exposure.
- Loop prevention uses the marker/reference **and** authenticated ingestion identity; the ingestion/update credential never triggers write-back.

---

## 8. Result projection

`PUT /alert/<id>/attributes` is not used: it requires `write:alerts` and performs a read-merge-write. Selected: a `SECURITY DEFINER` SQL projection function, `EXECUTE` only for the worker role, which takes an action reference, reads the authoritative ledger, validates customer/alert binding and generation, merges only the reserved namespace, never changes status, severity, history or source identity, and writes its own audit record. It creates no acknowledgement history and therefore no capture.

Underlying privileges: `SECURITY DEFINER` runs with the owner's privileges but does not grant the owner access to tables owned by another role. The projection-function owner receives only what the implementation requires on the Alerta schema: `USAGE` on the schema, `SELECT` on the columns needed to validate target and generation (`id`, `customer`, `status`, `attributes`, `update_time`), and `UPDATE (attributes)` on `alerts`. It receives no authority over `status`, `severity`, `history`, `customer` or source-identity columns. A column grant restricts the column, not JSON keys, so the function itself enforces the reserved namespace. The worker receives the function interface only, not the underlying grants. Projection is tested under the exact deployed owner and caller roles, including denial of direct worker `UPDATE`, not through a privileged migration session.

Source-identity fields (`zabbix_instance`, `zabbix_event_id`, `zabbix_trigger_id`, and the other §3.1 fields) are keys inside `attributes`, not separately protected SQL columns. `UPDATE (attributes)` therefore permits the owner to change them; nothing at the grant level prevents that. Enforcement boundaries:

| Boundary | Enforced by |
|---|---|
| Worker cannot directly update `alerts` | Database grants. |
| Projection function modifies only `zabbix_ack_sync*` and approved projection fields, preserving all source-identity keys | Function implementation and tests. |
| Function owner cannot directly update separately protected columns (`status`, `severity`, `history`, `customer`) | Absence of the corresponding column/table privileges. |
| A privileged administrator acting as the function owner cannot rewrite source keys inside `attributes` | **Not guaranteed** by `UPDATE (attributes)`. That role is inside the trusted administrative boundary. |

`SECURITY DEFINER` deliberately executes with the owner's privileges; the narrow callable interface, not an assumed restriction on the owner's JSON access, is the worker's protection. No JSON-specific access-control machinery is introduced.

Attributes: `zabbix_ack_sync` (`pending|ok|failed|skipped|expired`), `zabbix_ack_sync_reason`, action/member generation. Stale projections are repaired by a separate job; a stale result must not label a newer action `ok`. Missing alert → `projection_target_missing`; delivery result retained; no recreation, no repeat acknowledgement. The reference UI can display attributes as columns; labels and filters need acceptance testing.

Engineer-facing states: "Acknowledged in Alerta — Zabbix pending" (continue; no repeated clicking); "Zabbix synchronised"; "Zabbix sync failed" (integration owner); "Not synchronised — legacy/missing mapping" (acknowledge directly in the verified source event; never guess the latest event); urgent escalation during outage (acknowledge the exact event in Zabbix, record it; reconciliation recognises the state). A retry control requeues the existing action.

---

## 9. Reconciliation

Delivery in release one is direct SQL-outbox polling within the Alerta database; no message bus is on the delivery path. Reconciliation compares committed acknowledgement actions against delivery records and Zabbix, not merely alerts currently in `ack`. It repairs stranded captured work and incomplete dispatch bookkeeping, verifies ambiguous outcomes by opaque reference, clears resolved failures, rebuilds stale projections. Without a reference (`action=2` mode), reconciliation establishes `source_ack_observed`, not exact attribution. Never automatically re-acknowledges an event deliberately unacknowledged in Zabbix; reports it as drift. Alerta ack expiry is intentional local divergence. Empty reconciliation work generates no Zabbix query; permanent errors are not retried unchanged every cycle.

---

## 10. Required topology (site-A/site-B) — live application routing to be verified under G1

The statements below describe the topology the design requires. They are drawn from the supplied plans, not from inspection of both sites' application connections.

- One Patroni cluster, single writable primary; both sites' Alerta nodes write to it. Replication does not re-execute hooks or triggers; request retries, worker redelivery and DR replay still require idempotency.
- Logical instance ≠ site: a Zabbix database failing over keeps its `zabbix_instance`; independent databases need different IDs.
- Any worker serving an instance must reach its approved endpoint; per-instance queues, limits and circuit breakers isolate outages.
- DR: fence the old site's side-effecting consumers before activating the replacement. Local transaction atomicity is bounded by the platform's replication RPO.
- PgBouncer: probe identity creates a separate pool per PgBouncer instance; `min_pool_size` (previously proposed 3) is not inherited unquestioningly. Sizing register records API process count, maximum probe clients, probe pool min/max/reserve and aggregate backend budget; validated with pool/client/server statistics under load and failover. A forced database user must not collapse the probe identity into the application role.
- Illustrative: 4 nodes × 4 API processes → 16 probe clients, ≈3.2 health calls/s under continuous eligible traffic.

---

## 11. Observability, ownership, escalation

Logs: action ID, capture ID, Alerta ID, instance/event, result, attempts, latency, sanitised error class; no credentials, no proofs. Counters per instance: success, failure, skips, retries, dead letters, pending count, oldest pending age, last canary, credential expiry. Thresholds: warning at 2 min pending, alert at 5 min; immediate on invalid credentials, permission regression, capture-health failure, verifier-key unavailability. Alerts routed by instance and failure class (one incident per token failure, not per action). Health alerts use the out-of-band route when the failed component is on the alerting path.

| Condition | Primary | Escalation |
|---|---|---|
| Delivery failures / dead letters on active work | Integration on-call | Platform on-call → operations duty manager |
| Canary failure / growing oldest-pending age | Platform/monitoring on-call | Integration on-call; duty manager for sustained impact |
| Credential expiry warning | Credential owner + integration owner | Platform on-call before expiry; immediate on auth failure |
| Expired actions, unmapped legacy alerts, ambiguous source identity | NOC shift lead | Monitoring engineering; identity/security owner where applicable |

Paging: backup role after 10 minutes unacknowledged; duty manager for continued unowned impact. Named roster outstanding (gate G7).

---

## 12. Security and data handling

Roles and grants:

| Role | Rights |
|---|---|
| `zabalack_owner` (non-login) | Owns `zabalack` schema, tables, trigger function, projection/claim/completion/health functions. Supplies runtime privileges through the `SECURITY DEFINER` functions it owns; holds the minimum underlying grants on the Alerta schema listed in §8 (`USAGE`, column-level `SELECT`, `UPDATE (attributes)`). Zab-al-ack schema, table and function migrations run under this authority. It has **no** authority to attach, replace, enable or disable the trigger on `alerts` and does not own `alerts`. |
| Trigger installation principal | Explicitly authorised installation or break-glass step holding `TRIGGER` on `alerts` and `EXECUTE` on the capture function for `CREATE OR REPLACE TRIGGER`, and ownership authority over `alerts` for `ALTER TABLE … ENABLE/DISABLE TRIGGER`. Owning the function or the `zabalack` schema is insufficient for either. Recorded under G1 with the actual `alerts` owner and role inheritance. |
| Alerta runtime role | No direct write on the ledger; capture happens through the `SECURITY DEFINER` capture function invoked by the trigger. |
| Worker role | `EXECUTE` on claim, completion and projection functions only; no general modification rights on Alerta tables. |
| `zabalack_probe` | `CONNECT` on database, `USAGE` on `zabalack`, `EXECUTE` on `zabalack.capture_health()`; verify no inherited broader capability. |
| `zabalack_operator` (login) | `CONNECT` on database, `USAGE` on `zabalack`, `EXECUTE` on `zabalack.operator_call(text,jsonb)` (pause/resume, cohort and installation configuration, the §5.8 capture-disable flag, external-write enablement, retry/repair) and on `zabalack.capture_health()` for read-only diagnosis. Every operation records `actor_ref` and `change_ref` in `zabalack.audit`. No table privileges; cannot attach, enable or disable the trigger. |

All `SECURITY DEFINER` functions: restricted owner, controlled `search_path`, schema-qualified objects, `REVOKE EXECUTE … FROM PUBLIC` in the creating migration transaction. Backups, restore tests and migration checks include the `zabalack` schema, functions, grants and trigger attachment. `zabalack_owner` is not made the owner of `alerts` to simplify deployment.

Data classification, by layer:

| Layer | Allowlist |
|---|---|
| Capture record (`zabalack`, restricted) | As defined in §5.3: alert ID, top-level customer, source snapshot, head history entry metadata, resulting status, intent payload, marker and proof context needed for verification. Not the full alert, full history, operator free-text note or identity data beyond the authenticated login. |
| Ledger and dead letters | Action/effect identifiers, canonical customer/site references, target IDs, opaque actor reference, policy metadata, outcome and attempt records. No proofs after verification is complete, no email addresses, no alert text. Dead letters carry the same classification as the ledger. |
| Actor-reference-to-person mapping | Restricted, access-audited store. |
| Search, analytics, any bus export | Redacted projections. A bus export, if introduced, is a separate non-authoritative integration; it is not on the release-one delivery path (§9). |

site A–site B replication and any export mirroring are residency decisions. Retention values are unratified until gate G8 closes.

---

## 13. Packaging, deployment, rollback

- Pinned dependencies in Git; versioned wheel; Ansible deployment; no implicit Alerta or dependency upgrades.
- Compatibility manifest: Alerta server, Web UI, alarm model, Python, Psycopg, PostgreSQL schema, `zabalack` schema version, trigger/function definitions, integration package, Zabbix API/server, client libraries. Staging contract suite reruns before any Alerta or Zabbix upgrade; unrecognised versions pause source writes for the affected path while preserving capture.
- Capture validation: worker startup and periodic drift checks (schema version, trigger attachment and mode, function signatures, owners, grants, constraints, indexes, configuration revision); post-migration transactional execution tests under the intended roles; end-to-end canary. The worker never self-grants migration rights or auto-repairs schema objects.
- Installation runs as two separately controlled database steps in the same deployment job: (1) Zab-al-ack schema, table and function migrations under `zabalack_owner`; (2) trigger attachment or replacement, and any enable/disable, under the trigger installation principal (§12). Worker and probe identities receive neither. Installation, rollback and bypass are exercised in staging under those exact identities; a run as database superuser does not establish that the deployment identities are sufficient.
- Rollout: shadow mode first; drain one API node at a time, install, restart, verify health and hook evidence, return to service; enable writes only after all serving nodes and supported paths pass.
- Rollback order: stop outbound writes (pause consumer / write-disable switch, account for in-flight) → preserve ledger and pending entries on hold → remove plugin/configuration with rolling restarts → keep read-only reconciliation. Never delete queued evidence, auto-replay on restart, or unacknowledge Zabbix to "undo".

---

## 14. Acceptance test catalogue

| # | Test | Required result |
|---|---|---|
| T1 | Pilot alert acknowledged in Alerta | Same Zabbix event ID acknowledged; neutral comment with reference; ledger holds actor. |
| T2 | Repeat through each serving node and site | Identical behaviour. |
| T3 | Zabbix API unreachable | `pending`; no silent loss or false `ok`. |
| T4 | Recovery then new occurrence between request and delivery | Old action bound to E1; new problem untouched. |
| T5 | Intent staged, original ack fails, excluded bulk ack within capture lifetime | No authorised write-back. |
| T6 | Attribute/projection update while old `ack` is head of history | No capture. |
| T7 | Captured proof replayed; stale attribute dictionary reintroduced | No additional source effect. |
| T8 | Forged capture with a genuine action ID | Rejected; genuine action still processed. |
| T9 | Captured in time, worker delayed past capture lifetime but within execution lifetime | Delivered. |
| T10 | Source attributes change E1→E2 after capture | Work bound to E1. |
| T11a | Event/trigger identifiers replaced with an event outside the actor's authorised customer/cohort scope | Rejected; no source write. |
| T11b | Valid target selected within the actor's authorised cohort scope | Evaluated under the trusted-operator policy in §4.2; the test does not claim independent ingestion-lineage verification. |
| T12 | Multi-host trigger with one host in the customer group | Full host-set comparison rejects. |
| T13 | Valid event from authorised cohort | Exact event validated; no latest-event lookup. |
| T14 | Marker present with intent missing or mismatched; marker with invalid UUID; truncated marker; oversized marker | Diagnostic capture in each case; `failed_intent_invalid` with the specific reason; no Zabbix read or write; local acknowledgement unaffected. |
| T15 | Capture-health refresh fails after prior success | Eligible ack rejected; no stale success. |
| T16 | Probe reaches a standby or wrong deployment ID | Unhealthy. |
| T17 | Observer plugin before signer in effective chain | Never receives the proof pre-commit. |
| T18 | Routing sequence omits or moves the signer | Path not activated. |
| T19 | Ack save fails after proof generation | No executable capture; proof absent from notifications/logs. |
| T20 | Bypass enabled on part of the fleet | Inconsistency detected; no mixed tracked/untracked acks. |
| T21 | Trigger missing/disabled or broken function branch after migration | Detected by drift checks and execution tests. |
| T22 | Capture fault during eligible ack | Only that ack fails; ingestion and non-eligible acks continue. |
| T23 | Ingestion credential for customer A supplies customer B | Rejected. |
| T24 | Operator without customer B authority acknowledges B's alert | No write-back (`skipped_actor_policy`) or request rejected by Alerta scoping. |
| T25 | Event already acknowledged in Zabbix | `ok/already_acknowledged`; no write. |
| T26 | Resolved original problem | Acknowledged within lifetime; recovery event untouched. |
| T27 | Write-back on an alert whose Zabbix action has update operations | No unintended customer email/command; genuine human update still notifies. |
| T28 | Token expiry, permission denial, TLS failure | `failed`; single incident per instance; no repeated writes. |
| T29 | Response lost after successful write | Unknown outcome resolved by reference lookup; no duplicate comment. |
| T30 | 50- and 200-alert batches via per-alert `/action` | Measured validation+write latency, Alerta responsiveness, PHP-FPM queueing, DB load, timeout rate, tenant fairness. |
| T31 | site-A/site-B worker failover with in-flight request | Fenced; abandoned attempt reconciled, not blindly resent. |
| T32 | Maintenance pause and resume | Hold, no failure counters, stale work expired on resume. |
| T33 | Host recreated under new ID | `failed_validation` until cohort revision. |
| T34 | Health-check timeout through PgBouncer path | 500 ms bound honoured; no aborted-transaction leak. |
| T35 | Probe role inheritance | No privileges beyond CONNECT/USAGE/EXECUTE. |
| T36 | Repeat Ack request on an already-`ack` alert | Installed alarm model's response observed (reference: HTTP 409); no new history entry; no capture; ledger action unchanged; no expiry extension. |
| T37 | Forged or malformed candidate flood against one instance | Consumes verification leases only; no delivery slot, endpoint or credential resolution for that instance; genuine actions proceed. |
| T38 | Projection under deployed owner and caller roles | Function changes only the approved projection namespace and preserves all source-identity keys within `attributes`. Direct worker modification of `alerts` denied. Function owner lacks direct modification privileges on separately protected columns (`status`, `severity`, `history`, `customer`). The test does not claim that column-level grants prevent the trusted owner from modifying arbitrary keys within the granted `attributes` column. |
| T39 | Installation, rollback and capture bypass under the exact deployment identities (not superuser) | `zabalack_owner` completes Zab-al-ack-object migrations but cannot create, replace, enable or disable the trigger; the installation principal can; the capture-disable flag works through the `zabalack` function; worker and probe identities can do none of these. |

None of these is passed until exercised against the pinned implementation and recorded.

---

## 15. Deployment gates

Run G1–G4 (read-only inventories) first; G5–G6 evaluate the resulting configuration; G7–G8 in parallel. Statuses: *planned*, *configured*, *tested successfully* — kept distinct.

| Gate | Evidence required | Owner | Evidence ref | Date | Result | Exception |
|---|---|---|---|---|---|---|
| G1 Version and configuration inventory | Installed Alerta server/UI, Python, PostgreSQL, PgBouncer, Zabbix versions; `AUTH_REQUIRED`, `CUSTOMER_VIEWS`; ingestion-key customer mappings and scopes; effective database routing; owner of `alerts` and migration/installation role relationships including inheritance; installed alarm model; API-process counts; probe-pool settings. Secret references only. | | | | | |
| G2 Live ingestion contract | Exported media type; real problem, update, recovery payloads; top-level customer assignment; original problem-event identity; logical instance; legacy/missing-field handling. | | | | | |
| G3 Update-operation inventory | Matching actions, recipients, webhooks, email and command operations; approved handling of write-back updates; evidence human updates keep notifications. | | | | | |
| G4 Pilot cohort inventory | Approved (customer, instance, trigger) entries and host-ID sets; trigger problem-generation modes; Alerta correlation rules; effective plugin routing/order; explicit exclusions. | | | | | |
| G5 Tenant permission coverage | Positive/negative tests for customer assignment, operator scoping, privileged exceptions, source validation; Zabbix permissions for `event.get`, `event.acknowledge`, `host.get`. | | | | | |
| G6 Implementation and batch acceptance | Catalogue §14 including measured 50/200-alert batches, already-acknowledged handling, outages, expiry, pause, failover. | | | | | |
| G7 Operational ownership | Named primary/backup responders, alert destinations, escalation, maintenance/bypass procedures, engineer runbook. | | | | | |
| G8 Records-policy mapping | Approved classification, access, residency, retention, legal hold, deletion for capture evidence, ledger, diagnostics, logs, identity references. | | | | | |

---

## 16. Amendment procedure

A gate finding reopens only the affected assumption (e.g. a different hook sequence on the installed version, unrestricted ingestion credentials, unexpected correlation fan-in, a notification action that cannot stay in pilot scope). The amendment records: evidence, affected clause, required change, tests to rerun. It does not restart the architecture discussion.

---

## Appendix A — Decision history (superseded alternatives)

| Superseded | Replaced by | Reason |
|---|---|---|
| Direct-callback Alerta plugin adapted from `alerta-contrib/zabbix`; "no additional service" | Trigger capture + ledger + worker | Contributed plugin acknowledges up to ten trigger events, closes on Alerta close, uses password auth, wrong field names; dual-write and fail-open needs. |
| "Direct callback first, durable retries later" | Durable capture from day one | Multi-node topology, bulk operations, shared integration governance. |
| Inline first attempt with async fallback | Asynchronous only | Documented architecture choice, not a technical law; reconsider only on measurement. |
| Per-occurrence Alerta event key `zabbix:<instance>:problem:<id>` | Stable trigger/family key; one-to-one cohort; membership store deferred | Breaks flapping behaviour, duplicate counts, correlation, reporting. |
| Standalone normaliser service in front of Alerta | `pre_receive` plugin | No new failure point on the alerting path. |
| Separate "integration database" for outbox | `zabalack` schema inside Alerta's database | Same-transaction capture requires the same database. |
| Initial source-bindings table | Acknowledgement-time derivation + frozen snapshot | No transactional writer available; unnecessary for one-to-one cohort. |
| `pg_notify` worker wake-up | 250 ms indexed polling | PgBouncer transaction pooling does not support `LISTEN`; NOTIFY payload visibility. |
| Intent consumption + short TTL as binding | Signed proof in the `ack` history entry | Consumption does not prove the consuming acknowledgement came from the authorised request (T5). |
| Trigger predicate `history[1].type = 'action'` | `type = 'ack'` and newly written head entry | Ack action yields `ChangeType.ack`; attribute-only updates leave an old ack at the head. |
| Proof appended in `take_action`; signer last in `PLUGINS` | Proof in final effective `status_change`; routed order verified | `from_action` feeds `text` through the status hook; routing rules reorder plugins. |
| Signature verified in PostgreSQL | Worker-side verification | No signing key or crypto extension in the database. |
| "Never-attempted work needs no preliminary read" | Mandatory pre-write `event.get` validation | Editable source attributes cannot be trusted; cross-tenant substitution. |
| Engineer identity, Alerta ID and action ID in the Zabbix comment | Neutral comment with opaque reference | Customer-visible comments in multi-tenant Zabbix. |
| `PUT /alert/<id>/attributes` for projection | `SECURITY DEFINER` projection function | `write:alerts` scope too broad; read-merge-write race. |
| `ALTER FUNCTION … SET statement_timeout` | Caller-side `SET LOCAL` | Timeout is armed at client command start. |
| Probe via Alerta's backend database object | Plugin-owned Psycopg connection, probe role | Non-public backend methods; aborted-transaction isolation. |
| "Filter native update operations on a comment marker" | Webhook-side filtering; native email/command out of pilot scope | Zabbix 7.0 has no update-message substring condition. |
| "Callback does not exist"; "media type does not emit fields" | "Not evidenced"; "not confirmed in the live export" | Absence not established by lack of inspection. |
| "`CUSTOMER_VIEWS=False` nulls every customer" | Configuration + behavioural tenant gate | Admin-scoped credentials bypass customer checks regardless of the setting. |
| "Health-check volume is negligible" | Counted in pool sizing register | Separate probe pools × `min_pool_size` × PgBouncer instances. |
| Per-tenant "permission errors are silent" | Errors surfaced from API response | Silence would be an integration defect. |
| `userid + clock` as action-level idempotency key | Opaque reference; `source_ack_observed` without it | Identifies account and time, not the causing request. |
| Repeat Ack coalesces onto the existing action (rev 1.0 §4.5) | Rejected by the reference alarm model; nothing captured (rev 1.1 E2) | Verified reference-code mismatch: `InvalidAction` → HTTP 409. |
| Trigger requires a syntactically valid marker (rev 1.0 §5.3) | Bounded marker indication in `WHEN`; validity assessed in the capture function (E1) | Malformed markers must reach diagnostic capture. |
| Single "claim" before verification (rev 1.0 §6.2) | `verification_lease` then `delivery_lease` (E4) | Forged candidates must not consume delivery capacity or credentials. |
| T38 "owner cannot modify source columns" (rev 1.1) | Boundary table in §8; T38 reworded (E6) | Source fields are JSON keys; `UPDATE (attributes)` cannot restrict them. |
| "Migrations run as `zabalack_owner`" (rev 1.1 §12) | Zab-al-ack-object migrations vs trigger installation principal (E7) | `CREATE TRIGGER` needs `TRIGGER` on `alerts`; `ENABLE/DISABLE TRIGGER` needs table ownership. |

## Appendix B — Reference manifest

Retrieval dates and full commit identifiers are recorded only where they were supplied during review. Entries marked *to be recorded* must be completed before the reference set is treated as reproducible.

| Source | Version / identifier | Retrieved | Claims supported |
|---|---|---|---|
| Alerta server source (`alerta/alerta`) | tag `v9.1.0`; full commit *to be recorded* | *to be recorded* | Hook sequence for `/action` and `/status`; `from_action()` passing `text` through the status hook; `ChangeType.ack` vs `action`; attribute persistence before history write; `assign_customer()` admin override; `get_customers()` conditional on `CUSTOMER_VIEWS`; `InvalidAction` → HTTP 409 on repeat Ack; `history[]` composite storage; JSONB top-level merge on dedup; `PUT /alert/<id>/attributes` permission and behaviour; plugin exception handling. |
| Alerta Web UI (`alerta/alerta-webui`) | revision `e6eb99e` (full identifier *to be recorded*) | *to be recorded* | Single-alert Ack dispatches `/action`; attribute columns. |
| Alerta documentation — plugins | https://docs.alerta.io/plugins.html (9.1) | *to be recorded* | Hook names and routing rules. |
| Alerta documentation — configuration | https://docs.alerta.io/configuration.html (9.1) | *to be recorded* | `HISTORY_LIMIT`, `CUSTOMER_VIEWS`, `AUTH_REQUIRED`, `PLUGINS_RAISE_ON_ERROR`. |
| Alerta documentation — API query syntax | https://docs.alerta.io/api/query-syntax.html (9.1) | *to be recorded* | Attribute predicates (deferred symptom filter). |
| `alerta-contrib/plugins/zabbix` | `master` at review time; commit *to be recorded* | *to be recorded* | Superseded contributed-plugin behaviour (Appendix A). |
| Zabbix 7.0 — `event.get` | https://www.zabbix.com/documentation/7.0/en/manual/api/reference/event/get | *to be recorded* | Filters, `objectid`, `selectHosts`, `selectAcknowledges`, `r_eventid`, `acknowledged`. |
| Zabbix 7.0 — `event.acknowledge` | https://www.zabbix.com/documentation/7.0/en/manual/api/reference/event/acknowledge | *to be recorded* | `action` bitmask, returned `eventids`, ack skipped when already acknowledged, message still added. |
| Zabbix 7.0 — `host.get` | https://www.zabbix.com/documentation/7.0/en/manual/api/reference/host/get | *to be recorded* | `hostids`, `selectHostGroups`. |
| Zabbix 7.0 — event object | https://www.zabbix.com/documentation/7.0/en/manual/api/reference/event/object | *to be recorded* | Acknowledge record fields (`userid`, `clock`). |
| Zabbix 7.0 — action object | https://www.zabbix.com/documentation/7.0/en/manual/api/reference/action/object | *to be recorded* | No update-message substring condition. |
| Zabbix 7.0 — update operations; operations | https://www.zabbix.com/documentation/7.0/en/manual/config/notifications/action/update_operations ; …/action/operation | *to be recorded* | Update-operation side effects; acknowledged-condition on escalation steps. |
| Zabbix 7.0 — macros by location | https://www.zabbix.com/documentation/7.0/en/manual/appendix/macros/supported_by_location | *to be recorded* | `{EVENT.ID}`, `{EVENT.CAUSE.ID}`, `{EVENT.RECOVERY.ID}`, `{EVENT.UPDATE.MESSAGE}`. |
| Zabbix 7.0 — acknowledgment; user roles; API tokens; API overview | …/manual/acknowledgment ; …/web_interface/frontend_sections/users/user_roles ; …/users/api_tokens ; …/manual/api | *to be recorded* | Role permissions; API method allowlists; token expiry; Bearer authentication; JSON-RPC error handling. |
| PostgreSQL 18 — trigger behaviour; `CREATE TRIGGER` | https://www.postgresql.org/docs/18/trigger-definition.html ; …/sql-createtrigger.html | *to be recorded* | Transactional trigger failure; `WHEN` predicates, no subqueries in `WHEN`. |
| PostgreSQL 18 — `CREATE FUNCTION`; privileges | …/sql-createfunction.html ; …/ddl-priv.html | *to be recorded* | `SECURITY DEFINER`, `search_path`, default `PUBLIC` execute, column-level grants. |
| PostgreSQL 18 — `SET`; client connection defaults | …/sql-set.html ; …/runtime-config-client.html | *to be recorded* | `SET LOCAL` scope; `statement_timeout` semantics. |
| PostgreSQL 18 — `NOTIFY` | …/sql-notify.html | *to be recorded* | Deferred wake-up option (Appendix A). |
| PostgreSQL 18 — `pg_trigger`; PL/pgSQL implementation | …/catalog-pg-trigger.html ; …/plpgsql-implementation.html | *to be recorded* | Health-check catalogue fields; late parsing of unexecuted branches. |
| PostgreSQL 18 — admin functions; date/time functions | …/functions-admin.html ; …/functions-datetime.html | *to be recorded* | `pg_is_in_recovery()`, `current_database()`, `clock_timestamp()`. |
| PgBouncer — features; config; usage | https://www.pgbouncer.org/features.html ; …/config.html ; …/usage.html | *to be recorded* | No `LISTEN` in transaction pooling; per-database/user pools; `min_pool_size`; statistics. |
| Psycopg 2.9 — basic usage | https://www.psycopg.org/docs/usage.html | *to be recorded* | Transaction abort until rollback; implicit transaction on `SELECT`. |
| Python `hmac` | https://docs.python.org/3/library/hmac.html | *to be recorded* | `compare_digest`. |
| Flask — application context | https://flask.palletsprojects.com/en/stable/appcontext/ | *to be recorded* | Scope of `g`. |
| Webex webhooks | https://developer.webex.com/docs/api/guides/webhooks | *to be recorded* | Deferred chatops scope only. |
| Python Requests — advanced usage | https://docs.python-requests.org/en/latest/user/advanced/ | *to be recorded* | Connect/read timeouts are not a total deadline. |
| PHP-FPM configuration | https://www.php.net/manual/en/install.fpm.configuration.php | *to be recorded* | `pm.max_children` as a frontend concurrency bound. |
