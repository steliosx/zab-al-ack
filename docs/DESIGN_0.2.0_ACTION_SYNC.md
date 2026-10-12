# Design amendment — 0.2.0 action synchronisation (Alerta → Zabbix)

Status: DRAFT r2 for review (Codex, Astra, Voalack session). r2 answers Codex design
findings D1-D6 on r1 (6f3fe47a); each section below names the finding it closes. Implementation starts after the
0.1.0b1 tag (owner decision). Owner scope decisions of 11 October 2026 are in the owner-local
record `OWNER-DECISIONS-ZABALACK-ACTION-EXPANSION-20261011.md`; this document turns them into
a design. Nothing here changes the frozen 0.1.0b1 candidate.

## 1. Scope

| Decision | Value |
|---|---|
| Direction | Alerta → Zabbix only. No import of Zabbix operations. |
| Alerta actions in scope | `ack` (existing), `unack`, `close`, `shelve`, `unshelve`, Alerta's automatic transition `timeout` (ack-timeout and shelve-timeout), and severity-change reopen (dedupe/correlate path). **Notes are deferred to 0.2.1** (D1, §4.4): Alerta 9.1.0 has no post-persistence note hook, so a committed capture for notes needs its own design. `expired` produces no Zabbix write unless the owner decides otherwise (§9). |
| Zabbix operations | acknowledge (2, or 6 with message), unacknowledge (16), close (1, or 5 with message), suppress (32 with `suppress_until`), unsuppress (64). Message (4) is used only as the companion of ack/close, never alone in 0.2.0. Not in scope: severity (8), cause/symptom rank (128/256). |
| Entry paths | `PUT /alert/<id>/action` and Alerta housekeeping (`GET /management/housekeeping`, which runs `process_action` with `timeout`/`expired`); the dedupe/correlate ingestion path for severity-change reopen. Out of scope, recorded as skipped: direct `PUT /alert/<id>/status`, every `/_bulk/*` route, tag/attribute edits, `DELETE /alert/<id>`. |
| Failure semantics | Asynchronous with projection for every kind, exactly as release one: Alerta applies the action; the worker delivers; the outcome (`ok`, `refused` with reason, `uncertain`) is projected into alert attributes. No synchronous Zabbix pre-checks. |
| Sources | Zabbix-sourced alerts only (`source_system == "zabbix"`, one-to-one cohorts). |

## 2. Action-kind table (r2: D3, D6)

Facts: Alerta 9.1.0 alarm model `alerta` (`models/alarms/alerta.py`), hooks in `utils/api.py`,
housekeeping in `management/views.py:159-224`; Zabbix 7.0 `event.acknowledge`. Alerta rejects a
repeated `ack` on an acknowledged alert and a repeated `shelve` on a shelved alert locally
(`InvalidAction`, alarm model lines 195-196 and 213-214), so those requests never reach capture.
`timeout` restores `ack` when the previous status was `ack` (lines 173-177); only when the
previous status was not `ack` does it open the alert. Zabbix skips a repeated operation on an
event already in the target state (acknowledge on acknowledged, suppress on suppressed, close
on resolved) and does **not** extend `suppress_until` on a second suppress.

Every row has three outcomes, decided in this order by the worker (§6): **no-op** (a fresh
read-only `event.get` shows the bound event already in the target state → `ok_noop`, with the
event snapshot as evidence, no write); **write** (precondition holds → exactly one
`event.acknowledge`, then a fresh read confirms the specific field changed → `ok`); **refused**
(authoritative evidence that the write cannot apply, see §6) → `refused: <reason>`.

| Alerta activity | Hook that sees it | Alerta transition (model) | Zabbix write | Target-state field for no-op / success evidence | Refusal reasons |
|---|---|---|---|---|---|
| Human `ack` | `take_action`, `status_change` | open → ack (ACK-1) | 2, or 6 with neutral message | `acknowledged == 1` | — (existing) |
| Human `unack` | `take_action`, `status_change` | ack → previous status (UNACK-1) | 16 | `acknowledged == 0` | — |
| Human `shelve` | `take_action`, `status_change` | open → shelved (SHL-1); ack → shelved (ACK-2) | 32, `suppress_until` = capture commit time + the **effective** timeout Alerta accepted for this action (the `timeout` kwarg of `post_action`, which is the request value or `SHELVE_TIMEOUT`); never a stale `alert.timeout` | `suppressed == 1` and `suppression_data[].suppress_until` ≥ expected | — |
| Human `unshelve` | `take_action`, `status_change` | shelved → previous status (UNSHL-1) | 64 | `suppressed == 0` | — |
| Human `close` | `take_action`, `status_change` | * → closed (OPEN-3/ACK-3/SHL-2/BLK-1) | 1, or 5 with neutral message (message sent once; a repeat close is a no-op and sends no second message) | `r_eventid != 0` | `manual_close_disabled` (from `trigger.get` `manual_close == 0` on the bound trigger, read before the write); `insufficient_rights` (exact JSON-RPC permission code only, never parsed text); `not_problem_event` (`value != 1` or `object != 0` on the bound event) |
| Housekeeping `timeout`, previous status shelved | `take_action(action="timeout")`, `post_action` | shelved → open (OPEN-0) **or** → ack (ACK-0) when the status before shelving was ack | 64 only (never 16; a restored ack stays acknowledged in Zabbix) | `suppressed == 0` | — |
| Housekeeping `timeout`, previous status ack | `take_action(action="timeout")`, `post_action` | ack → open (OPEN-0) | 16 | `acknowledged == 0` | — |
| Housekeeping `expired` | `take_action(action="expired")` | * → expired (EXP-0) | none by default (§9 Q1) | — | recorded `skipped: expired_no_zabbix_counterpart` |
| Severity-change reopen (ACK-4) | `status_change` only (dedupe/correlate; no `take_action`) | ack → open | 16 | `acknowledged == 0` | — |
| Repeated `ack` on ack, repeated `shelve` on shelved | rejected by Alerta before any hook (`InvalidAction`) | none | none | — | nothing is captured; nothing to record |
| `open`, `assign`, unknown action strings | `take_action` | per model | none | — | `skipped: no_zabbix_counterpart` |
| Direct `/status`, bulk routes, delete, tag/attribute edits | `status_change` / none | varies | none | — | `skipped: entry_path_out_of_scope` (bulk tag/attributes/delete fire no hooks at all; nothing to record) |

Lab expectations (replaces r1 §8 assumptions): a second `shelve` is a local Alerta rejection,
not a Zabbix renewal; a `suppress` on an already suppressed event is a Zabbix no-op and the
ledger records `ok_noop`, not an extended `suppress_until`; `timeout` on an alert shelved from
`ack` returns the alert to `ack` and the Zabbix write is unsuppress only.

## 3. Actor model (r2: D5)

| Actor type | Identity recorded in the signed intent | Where it appears |
|---|---|---|
| `human` | `g.user_id` (must be non-null), `g.login`, scopes, customers, as today. API keys set `user_id = None` in Alerta 9.1.0 and are never human. | `/action` |
| `system` | `actor.type = "system"`, `actor.trigger = "timeout"`, `actor.invoked_by = g.login` of the principal that called `GET /management/housekeeping` (an API key or user), its scopes and customers | housekeeping transitions |
| `model` | `actor.type = "model"`, `actor.cause = "severity_rise_reopen"`, `actor.invoked_by = g.login` of the **ingestion principal** (Alerta records that login on the dedupe/correlate history rows, `models/alert.py:314, 374`), its scopes and customers | severity-change reopen during `POST /alert` |

Trusted cause provenance (D5): a `model` intent is admitted only when the ledger can bind it
to the committed Alerta history rows of the same ingestion: a `severity` history row and a
`status` history row for the alert with `update_time` equal to the ingesting alert's
`create_time`, status `ack → open`, `user == actor.invoked_by`. The capture trigger (as in
002 for `ack`) checks those rows in the same transaction; a route predicate alone never
admits a model write. The signed/verifier-enforced tuple for every kind is: `actor.type`,
`actor.invoked_by`, `customer`, `cohort_revision`, `policy_revision`, `kind`,
`zabbix_action` (bitmask), typed `params` (`message: str | null`, `suppress_until: int | null`),
`route`, `issued_at`, `capture_expires_at`, `execution_expires_at`. Mutating any field
invalidates the binding.

Cohort policy gains `mirror_system_transitions` and `mirror_model_transitions`, both default
**false**, enforced twice: at admission (the capture trigger refuses the kind when the flag is
false for the cohort revision bound in the intent) and at delivery (the worker re-reads the
flag for the bound cohort revision and records `skipped: mirror_disabled` instead of writing).
`system` and `model` intents never carry operator text; their companion message, when a kind
includes one, is the neutral team text naming the trigger or cause.

## 4. Capture paths (r2: D1, D4, D5)

Baseline rule (DESIGN 5.3) stays: a kind is captured only when the committed Alerta history
row and resulting status match that kind's **capture predicate**; the predicate is evaluated by
the capture trigger inside Alerta's transaction, not by the request path alone.

| Kind | Route predicate | Committed history row (`type`, `change_type`) | Resulting status | Actor types |
|---|---|---|---|---|
| `ack` | `PUT /alert/<id>/action`, action `ack` | `ack`, status | `ack` | human |
| `unack` | `PUT /alert/<id>/action`, action `unack` | `unack`, status | previous status (`open` or `shelved`... as the model returns) | human |
| `shelve` | `PUT /alert/<id>/action`, action `shelve` | `shelve`, status | `shelved` | human |
| `unshelve` | `PUT /alert/<id>/action`, action `unshelve` | `unshelve`, status | previous status | human |
| `close` | `PUT /alert/<id>/action`, action `close` | `close`, status | `closed` | human |
| `timeout_unshelve` | `GET /management/housekeeping`, action `timeout`, status before = `shelved` | `timeout`, status | `open` or `ack` | system |
| `timeout_unack` | `GET /management/housekeeping`, action `timeout`, status before = `ack` | `timeout`, status | `open` | system |
| `model_unack` | `POST /alert` (dedupe/correlate), status before = `ack` | `severity` + `status` rows with `update_time == create_time` | `open` | model |

### 4.1 Request path (`/action`)
Unchanged mechanics: stage an unsigned intent in `take_action`; sign the binding in the final
`status_change`; the capture trigger verifies the predicate on commit. The intent gains the
tuple fields of §3.

### 4.2 Housekeeping path
`take_action(action="timeout")` fires inside the housekeeping request with the housekeeping
principal in `g`. The plugin derives the kind from the status before the transition and stages
as in 4.1; the capture predicate requires the `timeout` history row. The coexistence contract
(R26) applies unchanged.

### 4.3 Status-change-only path (`model_unack`)
Severity-change reopen reaches plugins only through `status_change` during `POST /alert`.
When the previous status is `ack`, the new status `open`, the alert Zabalack-owned and
`mirror_model_transitions` true for the bound cohort revision, the hook stages **and signs** a
`model_unack` intent in that call (there is no later hook). The capture trigger admits it only
with the history provenance of §3. Fail-safe: the whole staging is wrapped; any exception is
recorded as `skipped: model_capture_failed` on the alert and the hook returns the inputs
unchanged, so ingestion never fails because of Zabalack. No partially executable intent can
exist: an intent without a committed capture row is never visible to the worker.

### 4.4 Notes: deferred to 0.2.1 (D1)
Alerta 9.1.0 calls `process_note` **before** `alert.add_note` (`views/alerts.py:613-614`) and
before `note.update` (`:692-693`); there is no post-persistence hook, and `process_note` may
persist attributes before the note operation fails or a later plugin rejects it. A committed
capture for notes therefore needs a database-side binding to the persisted note row (an
AFTER INSERT/UPDATE capture on `public.notes` keyed to a staged intent, with the note id and
revision as identity, and an explicit failure contract for add, edit and double edit). That is
a separate design (0.2.1); 0.2.0 does not deliver notes to Zabbix, and the worker rejects any
intent of kind `note`.

### 4.5 Legacy intents and versioning (D4)
Existing signed intents are `version 1`, kind `ack`, with the companion-message choice
recorded in their `comment` flag; migration 013 derives `zabbix_action` (2 or 6) from that flag
for each existing row instead of a blanket default, and sets `kind = 'ack'`,
`actor_type = 'human'`. New intents are `version 2` with the §3 tuple; the verifier accepts
version 1 only for kind `ack` and version 2 for every kind. SQL rejects NULL in the new columns.
Mixed-version fencing: the worker reads a capability row written by 013; until it is present,
version 2 intents are refused at admission (`skipped: schema_capability_missing`) and nothing
is staged for new kinds.

## 5. Ledger changes — migration 013, forward-only (r2: D2, D4)

- `actions` gains `kind text NOT NULL`, `zabbix_action integer NOT NULL`, `params jsonb NOT NULL`,
  `actor_type text NOT NULL`, `event_seq bigint NOT NULL`; 013 backfills existing rows as in
  §4.5 (no blanket defaults) and adds `CHECK` constraints against a new `action_kinds` reference
  table (kind, zabbix_action, requires_message, requires_suppress_until, allowed_actor_types).
- Every predicate of the form `i->>'action'<>'ack'` in 003, 006, 008, 009, 010, 011, 012 is
  superseded in 013 by kind-aware functions; shipped migration bytes and checksums stay
  immutable; 013 replaces the functions forward-only. Installer, worker and reader grants stay
  separate; the rollback script for 013 preserves pending evidence (rows are never deleted).
- **Operation identity and order (D2).** Every captured operation keeps its own `action_id`.
  There is no cross-kind coalescing. Within one bound (instance, event), the capture trigger
  assigns `event_seq` from a per-event sequence at commit, which is the total order; ties are
  impossible by construction. The worker admits operations for an event strictly in `event_seq`
  order, one at a time (advisory lock on (instance, event)): the next operation is admitted
  only when every earlier one is settled (`ok`, `ok_noop`, `refused`, `skipped`). An
  **uncertain** earlier write blocks all later operations on that event and suspends the
  instance (A6 unchanged); there is no automatic replay. ack A1 → unack U → ack A2 therefore
  delivers three operations in order, each with its own no-op/write decision against the live
  event. Identical consecutive operations (same kind and params, both undelivered) collapse to
  the later one only, recorded as `superseded_by`.
- **Desired state for drift (D2).** The reconciler compares the event's `acknowledged`,
  `suppressed` and `r_eventid` with the expected state after the **latest settled** operation
  for that event, never with an individual older operation.
- Outcome states: `ok`, `ok_noop` (read-only branch), `refused` with reason enum
  (`manual_close_disabled`, `insufficient_rights`, `event_resolved`, `not_problem_event`),
  `skipped` with reason, `uncertain` (unchanged semantics), `superseded_by`.

## 6. RPC changes (r2: D6)

- Delivery of one operation is: (1) fresh read-only `event.get` of the bound event with the
  fields `acknowledged`, `suppressed`, `suppression_data`, `r_eventid`, `value`, `object`,
  `objectid`; verify binding (trigger, cohort hosts) as today; (2) **no-op branch**: if the
  target-state field already matches (§2), settle `ok_noop` with the snapshot hash as evidence,
  no write; (3) **refusal branch**: for `close`, read `trigger.get` for the bound trigger and
  refuse `manual_close_disabled` when `manual_close == 0`; refuse `not_problem_event` when the
  snapshot is not a problem event of a trigger; (4) **write**: exactly one `event.acknowledge`
  with the kind's bitmask and typed params; (5) **success evidence**: a second fresh
  `event.get` must show the target-state field changed; the event-id echo alone is not proof
  of which bit applied. If (5) does not confirm, the outcome is `uncertain`, not `ok`.
- `ZabbixClient.call` write scope: `action` ∈ {2, 6, 16, 1, 5, 32, 64}, exactly one `eventids`
  entry, `message` present iff the bitmask includes 4, `suppress_until` present iff it includes
  32; `cause_eventid`/`severity` never present.
- Companion message: neutral team text + public reference, sent once per operation; a repeat
  close is a no-op and sends nothing.
- Refusal evidence is authoritative data (trigger flag, event fields) or exact JSON-RPC error
  codes mapped one-to-one; error message text is never parsed.
- **A6 retained in full**: every ambiguous application/internal/unclassified JSON-RPC error,
  including an HTTP 200 error envelope, every non-200 HTTP response (F2) and every timeout
  after a sent write leaves the operation `pending/uncertain`, suspends the affected instance,
  raises an incident and permits read-only reconciliation only. Settlement of an uncertain
  operation is a reconciliation outcome, never an automatic circuit reset. Late-attributable
  report protection (006/011) and no-replay invariants are unchanged.
- Per-kind settlement evidence: ack/unack `acknowledged`; shelve `suppressed == 1` with
  `suppress_until` ≥ expected (actual expiry is Zabbix's; the reconciler records when
  `suppressed` returns to 0); unshelve `suppressed == 0`; close `r_eventid != 0`.

## 7. Projection

Attribute family grows from `zabbix_ack_sync_*` to `zabbix_sync_<kind>_*` (r2: plus
`zabbix_sync_<kind>_event_seq` and `_outcome` so operators can see order and no-op/refusal) with the same
fields (`intent`, `reason`, `config_revision`, `local_at`, result). The reader query guard
and serializer filters cover the whole `zabbix_sync_` prefix. The release-one names stay as
aliases for `ack` so existing dashboards keep working.

## 8. Lab and evidence

Deployment-lab cases per kind (r2): human unack; close with manual close enabled → resolved;
close with manual close disabled → `refused: manual_close_disabled`; shelve → `suppressed == 1`
with `suppress_until` from the effective timeout; unshelve → `suppressed == 0`; housekeeping
timeout on an alert shelved from open → open + unsuppress; from ack → ack + unsuppress only;
repeat shelve → Alerta rejection, nothing captured; suppress on an already suppressed event →
`ok_noop`; ack/unack/ack sequence → three ordered operations; an uncertain earlier operation
blocks later ones and suspends the instance; concurrent workers on one event → single
admission; bulk status change → recorded skipped, no Zabbix call; model reopen with
`mirror_model_transitions` false → `skipped: mirror_disabled`, with the flag true → unack with
history provenance; mutated actor/kind/mask/params → verifier refusal.
Native-lab SQL cases for migration 013 and the kind-aware fences. Coexistence profile:
a Voalack-owned alert receiving each kind → Zabalack makes no write and raises nothing.

## 9. Open questions for the owner (asked after review)

1. `expired`: no Zabbix write (default) or close the problem if manual close is enabled?
2. Notes: accept deferral to 0.2.1 with a database-side committed capture (recommended), or
   drop note delivery from the roadmap?
3. `mirror_system_transitions` / `mirror_model_transitions`: keep both default false and let
   each customer cohort opt in (recommended)?

## 10. Prerequisites and ordering

1. Ownership binding at ingestion (coexistence F1 / Astra): required before adding kinds,
   because every new kind widens the attack surface of attribute-based ownership.
2. alerta-ack-core shared install (3b) for the hooks this design adds (housekeeping-path
   `take_action`, ingestion-path `status_change`).
3. Then migration 013, RPC, plugin, projection, lab cases, release 0.2.0.

No part of this amendment applies to 0.1.0b1; the tag proposal and frozen artifacts are
unaffected.
