# Design amendment — 0.2.0 action synchronisation (Alerta → Zabbix)

Status: DRAFT r4 for review (Codex, Astra, Voalack session). r2 answered Codex design
findings D1-D6 on r1 (6f3fe47a); r3 answered R1-R6 on r2 (da9a001e); r4 answers S1-S4 on r3
(38766e9d) and records the Voalack-session invariants. Pinned Zabbix 7.0.10 source
(`CEvent.php`, `taskmanager_server.c`) now governs the suppression facts, not manual prose.
Each section names the findings it closes. Source facts cite the pinned Alerta 9.1.0 package
and the exact-head Zabalack sources. Implementation starts after the
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
| Human `ack` | `take_action`, `status_change` | open → ack (OPEN-1) | 2, or 6 with neutral message | `acknowledged == 1` | — (existing) |
| Human `unack` | `take_action`, `status_change` | ack → previous status (UNACK-1) | 16 | `acknowledged == 0` | — |
| Human `shelve` | `take_action`, `status_change` | open → shelved (SHL-1); ack → shelved (ACK-2) | 32, `suppress_until` **derived at delivery and by the verifier** from the committed history row: `history.update_time + history.timeout` (Alerta resolves the effective timeout inside `from_action`, `models/alert.py:649-657`, and stores it in the history row, `:684`; `post_action` does not receive it, R2). The signed intent carries only `duration_source = "history.timeout"`, never an absolute time. | a **manual** suppression row for the bound event (see §6, R5) | — |
| Human `unshelve` | `take_action`, `status_change` | shelved → previous status (UNSHL-1) | 64, **only** when the exclusive-control fence holds (§6, S2) | no manual suppression row remains (maintenance suppression may keep `suppressed == 1`; that is not drift) | `skipped: foreign_manual_suppression` when a manual row by another user exists |
| Human `close` | `take_action`, `status_change` | * → closed (OPEN-3/ACK-3/SHL-2/BLK-1) | 1, or 5 with neutral message (message sent once; a repeat close is a no-op and sends no second message) | request accepted (new attributable history entry with bit 1, §6) and, as completion, `r_eventid != 0` on a later read | pre-write only: `manual_close_disabled` (`trigger.get` `manual_close == 0`); `no_write_rights` (`trigger.get` with `editable = true` returns nothing); `not_problem_event` (`value != 1` or `object != 0`). No JSON-RPC error code is ever a refusal (A6). |
| Housekeeping `timeout`, previous status shelved | `take_action(action="timeout")`, `post_action` | shelved → open (OPEN-0) **or** → ack (ACK-0) when the status before shelving was ack | 64 only (never 16), under the same fence as `unshelve` | no manual suppression row remains | as `unshelve` |
| Housekeeping `timeout`, previous status ack | `take_action(action="timeout")`, `post_action` | ack → open (OPEN-0) | 16 | `acknowledged == 0` | — |
| Housekeeping `expired` | `take_action(action="expired")` | * → expired (EXP-0) | none by default (§9 Q1) | — | recorded `skipped: expired_no_zabbix_counterpart` |
| Severity-change reopen (ACK-4) | `status_change` only (dedupe/correlate; no `take_action`) | ack → open | 16 | `acknowledged == 0` | — |
| Repeated `ack` on ack, repeated `shelve` on shelved | `take_action` runs first (`process_action` precedes `from_action`), then `from_action` raises `InvalidAction` and nothing is committed | none | none | — | no successful capture: the staged intent never meets a committed history row and expires; recorded `skipped: local_rejection` if the pre-stage left a trace |
| `open`, `assign`, unknown action strings | `take_action` | per model | none | — | `skipped: no_zabbix_counterpart` |
| Direct `/status`, bulk routes, delete, tag/attribute edits | `status_change` / none | varies | none | — | `skipped: entry_path_out_of_scope` (bulk tag/attributes/delete fire no hooks at all; nothing to record) |

Lab expectations: a second `shelve` is a local Alerta rejection (nothing captured); on pinned
Zabbix 7.0.10 a `suppress` task on an already manually suppressed event **updates** the existing
manual row's expiry and owner (`taskmanager_server.c:1121-1127`), so the ledger records the
observed effect, never a platform skip (S2); `timeout` on an alert shelved from `ack` returns the
alert to `ack` and the Zabbix write is unsuppress only; `unshelve` with a foreign manual row is
`skipped`, not a write.

## 3. Actor model (r2: D5)

| Actor type | Identity recorded in the signed intent | Where it appears |
|---|---|---|
| `human` | `g.user_id` (must be non-null), `g.login`, scopes, customers, as today. API keys set `user_id = None` in Alerta 9.1.0 and are never human. | `/action` |
| `system` | `actor.type = "system"`, `actor.trigger = "timeout"`, `actor.invoked_by = g.login` of the principal that called `GET /management/housekeeping` (an API key or user), its scopes and customers | housekeeping transitions |
| `model` | `actor.type = "model"`, `actor.cause = "severity_rise_reopen"`, `actor.invoked_by = g.login` of the **ingestion principal** (Alerta records that login on the dedupe/correlate history rows, `models/alert.py:314, 374`), its scopes and customers | severity-change reopen during `POST /alert` |

Trusted cause provenance (D5, R1): the severity-change reopen (alarm model ACK-4) occurs
only on the **correlate** ingestion path; dedupe keeps the severity and therefore never
reopens an acknowledged alert through ACK-4. Correlate writes exactly **one** history row,
`change_type = severity`, carrying the new status and `update_time == create_time` of the
incoming alert, `user = g.login` of the ingestion principal (`models/alert.py:356-379`). A
`model_unack` intent is admitted only when the capture trigger, in the same transaction, finds:
(a) that single committed severity row for the alert with `status = open`,
`update_time == NEW.create_time` (the incoming alert's create time, equal to the row's
`update_time`), `user == actor.invoked_by`; (b) the immediately preceding history row for the
alert has `status = ack`; (c) a severity rise in the alarm model's numeric map, where a **lower** number is **more**
severe: `Severity[NEW.previous_severity] > Severity[NEW.severity]` (alarm model `trend`,
lines 106-107, `More_Severe`), with the model's default-previous guard (an unknown previous
severity is not a rise). Pinned PostgreSQL `correlate_alert` updates `create_time`
(`base.py:245-251`), so `NEW.create_time` is the incoming time and the newly prepended
history row is the one with `update_time == NEW.create_time`. Any missing element fails closed
(`skipped: model_provenance_missing`). Dedupe status rows and older history rows are never
accepted as cause. The signed/verifier-enforced tuple for every kind is: `actor.type`,
`actor.invoked_by`, `customer`, `cohort_revision`, `policy_revision`, `kind`,
`zabbix_action` (bitmask), typed `params` (`message_mode: "none" | "neutral_reference"`,
`duration_source: null | "history.timeout"`), `route`, `issued_at`, `capture_expires_at`,
`execution_expires_at`. The exact version-2 signed key set is
`{version, action_id, alert_id, customer, source, actor, route, kind, zabbix_action, params,
cohort_revision, policy_revision, issued_at, capture_expires_at, execution_expires_at}`;
no absolute `suppress_until` and no free text are ever signed. Mutating any field invalidates
the binding. (S4)

Cohort policy gains `mirror_system_transitions` and `mirror_model_transitions`, both default
**false**, enforced twice: at admission (the capture trigger refuses the kind when the flag is
false for the cohort revision bound in the intent) and at delivery (the worker re-reads the
flag for the bound cohort revision and records `skipped: mirror_disabled` instead of writing).
`system` and `model` intents never carry operator text; their companion message, when a kind
includes one, is the neutral team text naming the trigger or cause.

## 4. Capture paths (r2: D1, D4, D5)

Baseline rule (DESIGN 5.3) stays: a kind is captured only when the committed Alerta history
row and resulting status match that kind's **capture predicate**; the predicate is evaluated by
the capture trigger inside Alerta's transaction, not by the request path alone. The restricted
capture-record allowlist (DESIGN 5.3) gains two columns for 0.2.0: `history_timeout` (the
newly prepended history row's `timeout`, integer seconds) and `history_update_time`
(already captured). Both are copied from `NEW.history[1]` at capture, immutable afterwards;
verification and delivery derive the suppression deadline from this captured snapshot only,
never from live history or any other timestamp-matching row (S4). Deadline derivation:
`suppress_until = floor(epoch_seconds(history_update_time at UTC)) + history_timeout`, an
integer; `history_timeout` must be a positive integer, otherwise the capture is
`skipped: invalid_duration`; if the derived deadline is already past when the worker is about
to send, the operation settles `skipped: deadline_passed` and no suppress is issued.

| Kind | Route predicate | Committed history row (`type`, `change_type`) | Resulting status | Actor types |
|---|---|---|---|---|
| `ack` | `PUT /alert/<id>/action`, action `ack` | `ack`, status | `ack` | human |
| `unack` | `PUT /alert/<id>/action`, action `unack` | `unack`, status | previous status (`open` or `shelved`... as the model returns) | human |
| `shelve` | `PUT /alert/<id>/action`, action `shelve` | `shelve`, status | `shelved` | human |
| `unshelve` | `PUT /alert/<id>/action`, action `unshelve` | `unshelve`, status | previous status | human |
| `close` | `PUT /alert/<id>/action`, action `close` | `close`, status | `closed` | human |
| `timeout_unshelve` | `GET /management/housekeeping`, action `timeout`, status before = `shelved` | `timeout`, status | `open` or `ack` | system |
| `timeout_unack` | `GET /management/housekeeping`, action `timeout`, status before = `ack` | `timeout`, status | `open` | system |
| `model_unack` | `POST /alert` correlate path only, status before = `ack` | one `severity` row, `status = open`, `update_time == create_time`, preceding row `status = ack`, severity rise | `open` | model |

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
Severity-change reopen reaches plugins only through `status_change` during `POST /alert`
(correlate path; the hook receives the existing alert with the proposed status). When the
previous status is `ack`, the new status `open`, the alert Zabalack-owned and
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
Existing signed intents are `version 1`, kind `ack`, and carry **no** message flag (exact-head
`crypto.py` `INTENT_KEYS`, lines 50-51). The companion-message choice is the bound cohort
revision's `comment_enabled` (`worker.py:153-154`, validated in `policy.py:66-81`). Migration
013 therefore derives each legacy row's `zabbix_action` from the **immutable bound cohort
revision** (`cohort_revision` in the signed intent → `zabalack.cohorts` row): 6 if
`comment_enabled`, else 2; where an attempt was already sent, the recorded attempt wire
parameters take precedence and must agree, otherwise the row is marked `HOLD:
legacy_binding_conflict`. A legacy row whose bound revision is missing is marked `HOLD:
legacy_binding_missing` and is never defaulted, re-signed, rebound or replayed; its signed
bytes, MAC, attempt shape and uncertainty are preserved. Legacy rows receive `event_seq` in
`issued_at` order per event (ties by `action_id`), all below any new row. (R3) New intents are `version 2` with the §3 tuple; the verifier accepts
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
- **Operation identity and order (D2, R4).** Every captured operation keeps its own
  `action_id`. There is no cross-kind coalescing. Ordering invariant: every capture for an
  alert runs inside Alerta's UPDATE of that `public.alerts` row, which holds the row lock
  until commit; with one-to-one cohorts one alert maps to one bound event, so captures for one
  event are serialised by that lock. To make the order commit-visible rather than merely
  unique, the capture trigger locks a canonical per-event anchor row
  (`zabalack.event_anchor(instance, event_id)`, `SELECT ... FOR UPDATE`, created on first
  capture) and assigns `event_seq = anchor.last_seq + 1`; a rolled-back capture releases the
  lock without advancing the anchor. The worker admits operations for an event strictly in
  `event_seq` order, one at a time, under the same anchor lock: the next operation is admitted
  only when every earlier one is in a **terminal** state: `ok`, `ok_noop`, `refused`,
  `skipped`, `expired`, or `superseded`. An **uncertain** earlier write blocks all later
  operations on that event and suspends the instance (A6 unchanged); there is no automatic
  replay. ack A1 → unack U → ack A2 therefore delivers three operations in order, each with
  its own no-op/write decision against the live event. **Supersession is one atomic terminal
  transition**: under the anchor lock, when two consecutive undelivered operations have the
  same kind and params, the earlier moves `captured → superseded` (terminal, with
  `superseded_by`) in the same statement that marks the later one eligible; the later one is
  re-checked for binding and cohort eligibility before delivery. A later operation that ends
  `expired`, `refused`, `failed` or `skipped` never erases an earlier **effective** state.
- **Desired state for drift (D2, R4, S2).** The reconciler folds **evidenced** effects per
  field, from the evidence recorded with each `ok`/`ok_noop` outcome, not from the kind alone:
  expected `acknowledged` from the latest evidenced ack/unack-family outcome; expected manual
  suppression (present/absent, observed `suppress_until`, observed owner) from the latest
  evidenced shelve/unshelve-family outcome, where a maintenance-only `ok_noop` records "no
  manual row" and therefore folds to *absent*; expected resolution from the latest evidenced
  close. Operations that ended `refused`, `failed`, `skipped`, `expired` or `superseded`
  contribute nothing. Drift is a mismatch against that evidenced per-field expectation.
- **One outcome enum (S3).** Live: `captured`, `claimed`, `uncertain`. Terminal (release the
  predecessor barrier and contribute no effective state unless noted): `ok` and `ok_noop`
  (contribute their evidenced effect), `refused` (pre-write authoritative reasons:
  `manual_close_disabled`, `no_write_rights`, `not_problem_event`), `failed` (verified
  no-effect dispatch/validation rejection `-32600/-32601/-32602`, as today; never derived from
  error text), `skipped` (with reason), `expired`, `superseded` (with `superseded_by`).
  `uncertain` never becomes terminal by expiry; only reconciliation settles it, and while it
  stands it blocks later operations on the event and keeps the instance suspended (A6, 011/012
  late-attributable fencing).

## 6. RPC changes (r2: D6; r3: R5, R6; r4: S1, S2)

Platform facts for pinned Zabbix 7.0.10 (source, not manual prose): `event.acknowledge`
applies acknowledge/unacknowledge/message synchronously but queues **tasks** for close,
suppress and unsuppress that the server's task manager applies later
(`CEvent.php:1147-1157`, `taskmanager_server.c`). A suppress task on an event that already
has a manual suppression row **updates** that row's expiry and owner (`taskmanager_server.c:
1121-1127`); an unsuppress task **deletes all** manual rows for the event regardless of owner
(`:1110-1111`); the unsuppress API check tests for any manual row, not the caller's
(`isEventSuppressed`, `CEvent.php:1728-1735`); maintenance-only suppression does not stop a
suppress task from creating a manual row. History timestamps are integer seconds
(`time()`, `CEvent.php:1064`, stored at `:1182`).

- Delivery of one operation: (1) fresh read-only `event.get` of the bound event with
  `acknowledged`, `suppressed`, `suppression_data` (`maintenanceid`, `userid`,
  `suppress_until`), `r_eventid`, `value`, `object`, `objectid`, and `selectAcknowledges`
  (`acknowledgeid`, `userid`, `clock`, `action`, `message`, `suppress_until`); verify binding
  (trigger, cohort hosts) as today; (2) persist the **pre-send evidence watermark** for this
  attempt: the maximum `acknowledgeid` seen and the set of `acknowledgeid`s present (S1);
  (3) no-op branch per kind (below) → `ok_noop` with the snapshot as evidence; (4) pre-write
  refusal branch on authoritative data only; (5) exactly one `event.acknowledge` with the kind's
  bitmask and typed params; (6) **acceptance evidence**: a fresh read must show a **new**
  `acknowledges[]` entry with `acknowledgeid` greater than the watermark and not in the seen
  set, `userid` equal to the bound instance's API user, and `action` including every requested
  bit; for bit 4 the entry's `message` must equal the neutral text with this operation's public
  reference. The entry is then **claimed** by this attempt (stored `evidence_acknowledgeid`,
  unique per instance) so no later attempt can consume it. Clock values are recorded but never
  used to select or exclude evidence (S1). If no such new, unclaimed, attributable entry exists,
  the outcome is `uncertain`; nothing is resent. (7) **completion evidence** for task-executed
  bits: `r_eventid != 0` for close, the manual suppression row for suppress, its absence for
  unsuppress, observed on the same or a **later** read-only read. Acceptance settles the
  operation `ok` with `completion = pending`; the reconciler records completion when observed
  and raises drift if the task never completes within the configured window. A second write is
  never used to resolve progress.
- **Service-user exclusivity.** The bound instance's API user is dedicated to Zabalack; the
  deployment inputs (G1) record that no other actor uses it. The watermark/claim contract does
  not depend on this, but operator-facing attribution does, so it is stated and checked.
- **No-op and fence policy for suppression (S2).** These are integration policies, labelled as
  such, not platform guarantees. `shelve`: if any manual suppression row exists, the policy is
  **no write**; the outcome is `ok_noop: existing_manual_suppression` with the observed owner,
  `suppress_until` (finite or 0 = indefinite) and `maintenanceid` state recorded as evidence;
  the ledger never claims this operation established its own duration. If only maintenance
  suppression exists, the policy is to write (a manual row will be created); the recorded
  evidence is the resulting manual row. `unshelve`/`timeout_unshelve`: because the platform
  deletes all manual rows regardless of owner, the **exclusive-control fence** is: write only if
  the fresh read shows exactly one manual row, owned by our API user, whose `suppress_until`
  equals a deadline this ledger recorded for this event; otherwise `skipped:
  foreign_manual_suppression` and the alert's projection says so. A replacement between the
  read and the write cannot be excluded by the API, so the acceptance read records which rows
  disappeared; if a foreign row was removed, the operation is `ok` with `collateral =
  foreign_row_removed` and an incident is raised for operator review. Per cohort,
  `allow_unsuppress` (default false, §9) must be true before any unsuppress is written.
- **Pre-write refusal (R6, S4).** Only: `trigger.get` with `editable = true` returning nothing
  → `refused: no_write_rights`; `manual_close == 0` → `refused: manual_close_disabled`; event
  not a problem event of a trigger → `refused: not_problem_event`. After a write is sent, no
  JSON-RPC code is a refusal: `-32500`/`-32603` and every application/internal/unclassified
  error, HTTP 200 error envelope, non-200 response (F2) or timeout leaves the operation
  `uncertain`, suspends the instance, raises an incident and permits read-only reconciliation
  only (A6). Verified dispatch/validation rejections (`-32600/-32601/-32602`) are `failed`
  (terminal, no effect). Error text is never parsed.
- `ZabbixClient.call` write scope: `action` ∈ {2, 6, 16, 1, 5, 32, 64}, exactly one `eventids`
  entry, `message` present iff the bitmask includes 4, `suppress_until` present iff it includes
  32 and equal to the deadline derived from the captured snapshot (§4); `cause_eventid`/
  `severity` never present.
- Companion message: neutral team text + public reference, sent once per operation; the
  default policy (owner question 0) is that a missing bit 4 in the acceptance evidence leaves
  the operation `uncertain`.
- Late-attributable report protection (006/011/012) and no-replay invariants are unchanged.

## 7. Projection

Attribute family grows from `zabbix_ack_sync_*` to `zabbix_sync_<kind>_*` (r2: plus
`zabbix_sync_<kind>_event_seq` and `_outcome` so operators can see order and no-op/refusal) with the same
fields (`intent`, `reason`, `config_revision`, `local_at`, result). The reader query guard
and serializer filters cover the whole `zabbix_sync_` prefix. The release-one names stay as
aliases for `ack` so existing dashboards keep working.

## 8. Lab and evidence

Deployment-lab cases per kind (r2): human unack; close with manual close enabled → resolved;
close with manual close disabled → `refused: manual_close_disabled`; shelve → `suppressed == 1`
with `suppress_until` from the effective timeout; unshelve → no manual row remains (maintenance suppression may persist); unshelve with a foreign
manual row → `skipped: foreign_manual_suppression`; same-second ack/unack/ack with watermark
claims; pre-existing matching history → uncertain, no resend; delayed close task completion
observed by the reconciler; housekeeping
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
0. Companion message policy: a missing message bit leaves the operation uncertain (default)
   or is best-effort (ack/close counts as `ok` without its message)?
2. Notes: accept deferral to 0.2.1 with a database-side committed capture (recommended), or
   drop note delivery from the roadmap?
3. `mirror_system_transitions` / `mirror_model_transitions`: keep both default false and let
   each customer cohort opt in (recommended)?
4. `allow_unsuppress` per cohort: default false (unshelve never writes to Zabbix until the
   cohort opts in, because the platform removes all manual suppression rows regardless of
   owner) — recommended.

## 10. Prerequisites and ordering

1. Ownership binding at ingestion (coexistence F1 / Astra): required before adding kinds,
   because every new kind widens the attack surface of attribute-based ownership.
2. alerta-ack-core shared install (3b) for the hooks this design adds (housekeeping-path
   `take_action`, ingestion-path `status_change`).
3. Then migration 013, RPC, plugin, projection, lab cases, release 0.2.0.

## 11. Zabalack's own coexistence invariants (Voalack session, 11 October 2026)

These are Zabalack's guarantees with Zabalack's evidence; no other plugin's trigger enforces
them for us:

1. Every Zabalack hook (`take_action` for every kind, housekeeping `take_action`, the ingestion
   `status_change` path) returns a foreign alert unchanged and raises nothing. Evidence: the
   coexistence lab profile per kind, and unit tests per hook with a foreign-source alert.
2. Zabalack never writes `event`, `resource`, `id` or `create_time` on any alert, owned or
   foreign, on any path; its write set on `public.alerts` is `attributes` only, plus its own
   schema. Evidence: a static write-set test over the plugin and SQL and a runtime assertion in
   the lab that those columns are byte-identical before and after each operation.
3. Zabalack never truncates `public.alerts`. Evidence: a static test; no `TRUNCATE` in the
   repository outside the lab's own disposable database setup.
4. The non-ACK paths (housekeeping/system actor, ingestion/model actor) carry their own
   source-path and transaction evidence; they inherit nothing from the ACK-path qualification.

No part of this amendment applies to 0.1.0b1; the tag proposal and frozen artifacts are
unaffected.
