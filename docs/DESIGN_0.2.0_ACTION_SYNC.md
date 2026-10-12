# Design amendment — 0.2.0 action synchronisation (Alerta → Zabbix)

Status: DRAFT r8. r8 makes the owner's record-only suppression decision (§9 Q1) unconditional
throughout the 0.2.0 contract (Codex V1 on r7, 18318b84). Codex gave AGREEMENT_DESIGN_ONLY on r6
(26d7197f); Astra's bounded coexistence review of r5/r6 (note 3ebfe880, peer-relayed) found
§11.2 and §11.5 consistent at design level. The owner's five §9 decisions of 12 October 2026
are recorded in §9. Design agreement is not implementation, runtime or qualification approval.
**0.2.0 never writes Zabbix suppression.** The suppression write path that earlier revisions
explored is kept only as a future specification in Appendix A; nothing in Appendix A is
reachable in 0.2.0.

Revision history (historical): r2 answered Codex findings D1-D6 on r1 (6f3fe47a); r3 answered
R1-R6 on r2 (da9a001e); r4 answered S1-S4 on r3 (38766e9d); r5 answered T1-T5 on r4 (2b160814)
and first made suppression fail closed; r6 answered U1-U2 on r5 (1c07a6bc); r7 recorded the
owner decisions on r6. Pinned Zabbix 7.0.10 source (`CEvent.php`, `taskmanager_server.c`)
governs the suppression facts, not manual prose.
Each section names the findings it closes. Source facts cite the pinned Alerta 9.1.0 package
and the exact-head Zabalack sources. Implementation starts after the
0.1.0b1 tag (owner decision). Owner scope decisions of 11 October 2026 are in the owner-local
record `OWNER-DECISIONS-ZABALACK-ACTION-EXPANSION-20261011.md`; this document turns them into
a design. Nothing here changes the frozen 0.1.0b1 candidate.

## 1. Scope

| Decision | Value |
|---|---|
| Direction | Alerta → Zabbix only. No import of Zabbix operations. |
| Alerta actions in scope | `ack` (existing), `unack`, `close`, Alerta's automatic `timeout` on an acknowledged alert (ack-timeout, `system` actor), and severity-change reopen (correlate path, `model` actor). **Suppression kinds** (`shelve`, `unshelve`, `timeout_unshelve`) are **recorded only, never written, in 0.2.0** (owner decision §9 Q1): they settle `skipped: suppression_fence_unavailable` because pinned Zabbix 7.0.10 offers no fence for other users' suppression (T2). **Notes are deferred to 0.2.1** (owner decision §9 Q4; D1, §4.4). `expired` produces no Zabbix write (owner decision §9 Q3). |
| Zabbix operations | acknowledge (2, or 6 with message), unacknowledge (16), close (1, or 5 with message). Suppress (32) and unsuppress (64) are **not sent in 0.2.0** (step 0,
§9 Q1; future specification in Appendix A). Message (4) is used only as the companion of ack/close, never alone in 0.2.0. Not in scope: severity (8), cause/symptom rank (128/256). |
| Entry paths | `PUT /alert/<id>/action` and Alerta housekeeping (`GET /management/housekeeping`, which runs `process_action` with `timeout`/`expired`); the dedupe/correlate ingestion path for severity-change reopen. Out of scope, recorded as skipped: direct `PUT /alert/<id>/status`, every `/_bulk/*` route, tag/attribute edits, `DELETE /alert/<id>`. |
| Failure semantics | Asynchronous with projection for every kind, exactly as release one: Alerta applies the action; the worker delivers; the outcome (`ok`, `refused` with reason, `uncertain`) is projected into alert attributes. No synchronous Zabbix pre-checks. |
| Sources | Zabbix-sourced alerts only (`source_system == "zabbix"`, one-to-one cohorts). |

## 2. Action-kind table (r2: D3, D6)

Facts: Alerta 9.1.0 alarm model `alerta` (`models/alarms/alerta.py`), hooks in `utils/api.py`,
housekeeping in `management/views.py:159-224`; Zabbix 7.0 `event.acknowledge`. Alerta rejects a
repeated `ack` on an acknowledged alert and a repeated `shelve` on a shelved alert locally
(`InvalidAction`, alarm model lines 195-196 and 213-214), so those requests never reach capture.
`timeout` restores `ack` when the previous status was `ack` (lines 173-177); only when the
previous status was not `ack` does it open the alert. On pinned Zabbix 7.0.10 a repeated
acknowledge on an acknowledged event and a close on a resolved event change nothing, which the
no-op branch observes. Suppression has no such property: a suppress is queued whenever the
event is not closed (`CEvent.php:1147-1151`) and the task updates an existing manual row's
owner and expiry (`taskmanager_server.c:1121-1127`). No-write on an existing manual row is
therefore Zabalack's integration **policy**, not a platform fact (U1).

Every row has these outcomes, decided in this order by the worker (§6). **Step 0, 0.2.0
suppression release gate (U2, V1)**: for every admitted capture of `shelve`, `unshelve` or
`timeout_unshelve`, the worker settles `skipped: suppression_fence_unavailable` before any
read-based no-op, refusal or write, **whatever** the cohort's risk-policy value (absent,
false, true, unbound or invalid). No suppression kind produces `ok`, `ok_noop`, `accepted`
or a remote write in 0.2.0, and none contributes to the suppression desired state. This gate
does not change capture: an action Alerta rejects locally is still never captured. All other
kinds continue with: **no-op** (a freshthe steps below. Then: **no-op** (a fresh
read-only `event.get` shows the bound event already in the target state → `ok_noop`, with the
event snapshot as evidence, no write); **refused** (authoritative pre-write evidence that the
write cannot apply) → `refused: <reason>`; **write** (exactly one `event.acknowledge`). For the
synchronous bits (2, 4, 16) a fresh read then confirms attributable evidence → `ok`. For the
task-executed bit (in 0.2.0 only 1, close) the write yields **`accepted`** (attributable history entry) and
the operation stays **outstanding** until a later read evidences completion (`ok`) or the
bounded completion window lapses (§5); `accepted` is not terminal and does not release the
barrier (T1).

| Alerta activity | Hook that sees it | Alerta transition (model) | Zabbix write | Target-state field for no-op / success evidence | Refusal reasons |
|---|---|---|---|---|---|
| Human `ack` | `take_action`, `status_change` | open → ack (OPEN-1) | 2, or 6 with neutral message | `acknowledged == 1` | — (existing) |
| Human `unack` | `take_action`, `status_change` | ack → previous status (UNACK-1) | 16 | `acknowledged == 0` | — |
| Human `shelve` | `take_action`, `status_change` | open → shelved (SHL-1); ack → shelved (ACK-2) | **none in 0.2.0** (step 0) | — | settles `skipped: suppression_fence_unavailable`; future write specification in Appendix A |
| Human `unshelve` | `take_action`, `status_change` | shelved → previous status (UNSHL-1) | **none in 0.2.0** (step 0) | — | settles `skipped: suppression_fence_unavailable`; future write specification in Appendix A |
| Human `close` | `take_action`, `status_change` | * → closed (OPEN-3/ACK-3/SHL-2/BLK-1) | 1, or 5 with neutral message (message sent once; a repeat close is a no-op and sends no second message) | request accepted (new attributable history entry with bit 1, §6) and, as completion, `r_eventid != 0` on a later read | pre-write only: `manual_close_disabled` (`trigger.get` `manual_close == 0`); `no_write_rights` (`trigger.get` with `editable = true` returns nothing); `not_problem_event` (`value != 1` or `object != 0`). No JSON-RPC error code is ever a refusal (A6). |
| Housekeeping `timeout`, previous status shelved | `take_action(action="timeout")`, `post_action` | shelved → open (OPEN-0) **or** → ack (ACK-0) when the status before shelving was ack | **none in 0.2.0** (step 0); never 16, so a restored ack stays acknowledged in Zabbix | — | settles `skipped: suppression_fence_unavailable` |
| Housekeeping `timeout`, previous status ack | `take_action(action="timeout")`, `post_action` | ack → open (OPEN-0) | 16 | `acknowledged == 0` | — |
| Housekeeping `expired` | `take_action(action="expired")` | * → expired (EXP-0) | none (owner decision §9 Q3) | — | recorded `skipped: expired_no_zabbix_counterpart` |
| Severity-change reopen (ACK-4) | `status_change` only (dedupe/correlate; no `take_action`) | ack → open | 16 | `acknowledged == 0` | — |
| Repeated `ack` on ack, repeated `shelve` on shelved | `take_action` runs first (`process_action` precedes `from_action`), then `from_action` raises `InvalidAction` and nothing is committed | none | none | — | no successful capture: the staged intent never meets a committed history row and expires; recorded `skipped: local_rejection` if the pre-stage left a trace |
| `open`, `assign`, unknown action strings | `take_action` | per model | none | — | `skipped: no_zabbix_counterpart` |
| Direct `/status`, bulk routes, delete, tag/attribute edits | `status_change` / none | varies | none | — | `skipped: entry_path_out_of_scope` (bulk tag/attributes/delete fire no hooks at all; nothing to record) |

Lab expectations (0.2.0): a second `shelve` is a local Alerta rejection (nothing captured);
every captured `shelve`, `unshelve` and `timeout_unshelve` settles
`skipped: suppression_fence_unavailable` with no Zabbix call, including when the cohort carries
an otherwise valid policy with `accept_suppression_collateral_risk = true` (negative case);
`timeout` on an alert shelved from `ack` returns the alert to `ack` in Alerta and makes no
Zabbix write.

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

Cohort policy gains `mirror_system_transitions` and `mirror_model_transitions`. Owner decision
§9 Q5: both default **true**; a cohort may set either to false (opt-out). This widens the
earlier recommended default deliberately and explicitly; it does not relax any evidence rule:
a `model` intent is still admitted only with the committed correlate provenance of this
section, and a `system` intent only with the housekeeping capture predicate of §4. The flags
are enforced twice: at admission (the capture trigger refuses the kind when the flag is
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
never from live history or any other timestamp-matching row (S4). The duration snapshot and deadline derivation below are part of the future
suppression specification (Appendix A) and are not used by any 0.2.0 write. Deadline derivation:
`suppress_until = floor(epoch_seconds(history_update_time at UTC)) + history_timeout`, an
integer; for suppression kinds only, `history_timeout` must be a positive integer, otherwise
the capture is `skipped: invalid_duration`; a suppress is eligible to send only while the
worker's clock (integer UTC seconds) is strictly **before** the derived deadline; at or after it
the operation settles `skipped: deadline_passed`. Ack/unack/close captures carry no duration
validation (T4).

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
  In 0.2.0 the `shelve`, `unshelve` and `timeout_unshelve` rows carry no deliverable
  `zabbix_action`; their only reachable outcome is `skipped: suppression_fence_unavailable`.
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
  only when every earlier one is **effect-complete** as defined by the single outcome enum
  below (T1, T3): a terminal state, where for task-executed bits terminal means completion
  evidenced, not acceptance. `uncertain` and `accepted` (outstanding) earlier operations block
  all later operations on that event; `uncertain` also suspends the instance (A6 unchanged);
  there is no automatic replay of either. ack A1 → unack U → ack A2 therefore delivers three operations in order, each with
  its own no-op/write decision against the live event. **Supersession is one atomic terminal
  transition**: under the anchor lock, when two consecutive undelivered operations have the
  same kind and params, the earlier moves `captured → superseded` (terminal, with
  `superseded_by`) in the same statement that marks the later one eligible; the later one is
  re-checked for binding and cohort eligibility before delivery. A later operation that ends
  `expired`, `refused`, `failed` or `skipped` never erases an earlier **effective** state.
- **Desired state for drift (D2, R4, S2, T1).** The reconciler folds **evidenced** effects per
  field, from the evidence recorded with each `ok`/`ok_noop` outcome only; `accepted` outcomes
  contribute nothing until completion is evidenced:
  expected `acknowledged` from the latest evidenced ack/unack-family outcome; expected resolution
  from the latest evidenced close. In 0.2.0 no suppression kind produces `ok` or `ok_noop`, so
  Zabalack holds **no** expected suppression state and never reports suppression drift; the
  suppression fold for a future release is in Appendix A. Operations that ended `refused`, `failed`, `skipped`, `expired` or `superseded`
  contribute nothing. Drift is a mismatch against that evidenced per-field expectation.
- **One outcome enum (S3, T1, T3) — the single authority for every barrier predicate.**
  Live, blocking: `captured`, `claimed`, `accepted` (task-executed bit written and attributably
  accepted; completion outstanding), `uncertain`. Terminal, barrier-releasing: `ok` and
  `ok_noop` (contribute their evidenced effect), `refused` (pre-write authoritative reasons:
  `manual_close_disabled`, `no_write_rights`, `not_problem_event`), `failed` (verified
  no-effect dispatch/validation rejection `-32600/-32601/-32602`, as today; never derived from
  error text; contributes no effect), `skipped` (with reason), `expired` (a never-sent
  operation whose execution lifetime lapsed), `superseded` (with `superseded_by`). Neither
  `uncertain` nor `accepted` ever becomes terminal by the passage of time: `uncertain` is
  settled only by reconciliation (A6, 011/012 late-attributable fencing) and keeps the
  instance suspended; `accepted` becomes `ok` when a later read evidences completion, and if
  the bounded completion window lapses it becomes `uncertain` (instance suspended, incident,
  read-only reconciliation), never `ok` and never released. Lab: a `failed` predecessor
  followed by a valid operation must deliver the successor.

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
  (3) suppression kinds never reach this sequence in 0.2.0: step 0 (§2) has already settled
  them `skipped: suppression_fence_unavailable`; for every other kind, the
  no-op branch per kind (below) → `ok_noop` with the snapshot as evidence; (4) pre-writeno-op branch per kind (below) → `ok_noop` with the snapshot as evidence; (4) pre-write
  refusal branch on authoritative data only; (5) exactly one `event.acknowledge` with the kind's
  bitmask and typed params; (6) **acceptance evidence**: a fresh read must show a **new**
  `acknowledges[]` entry with `acknowledgeid` greater than the watermark and not in the seen
  set, `userid` equal to the bound instance's API user, and `action` including every requested
  bit; for bit 4 the entry's `message` must equal the neutral text with this operation's public
  reference. The entry is then **claimed** by this attempt (stored `evidence_acknowledgeid`,
  unique per instance) so no later attempt can consume it. Clock values are recorded but never
  used to select or exclude evidence (S1). If no such new, unclaimed, attributable entry exists,
  the outcome is `uncertain`; nothing is resent. (7) For synchronous bits (2, 4, 16) acceptance
  evidence settles `ok`. For **task-executed** bits (in 0.2.0 only bit 1, close; 32/64 are never sent) acceptance evidence moves the
  operation to `accepted` (outstanding); **completion evidence** is observed on a **later**
  read-only read: `r_eventid != 0` for close (suppression completion evidence is future-only,
  Appendix A). Only then does the
  operation become `ok`. While `accepted`, the operation holds the event barrier (T1), so no
  later operation on the event decides against a state the pending task has not yet produced
  (lab: close accepted then ack; close accepted then unack). The completion window is bounded per instance; lapse makes the operation `uncertain`
  (§5). A second write is never used to resolve progress.
- **Service-user exclusivity (admission assumption).** The bound instance's API user is
  dedicated to Zabalack; the deployment inputs (G1) record that no other actor uses it. The
  watermark/claim contract prevents reuse of old evidence and double consumption; it does
  **not** prevent a fresh same-user entry made by another writer sharing the credential from
  exceeding the watermark. Exclusivity and per-event no-outstanding-attempt fencing are
  therefore verified admission requirements for operations without a public reference; where
  they cannot be verified the operation fails closed (`skipped: attribution_unverifiable`).
- **Suppression kinds are never written in 0.2.0 (T2, owner decision §9 Q1).** Pinned Zabbix
  7.0.10 provides no fence that holds through remote task completion: a suppress task
  overwrites the owner and expiry of any manual row present when it executes
  (`taskmanager_server.c:1121-1127`); an unsuppress task deletes every manual row regardless
  of owner (`:1110-1111`); a suppress task executing at or after its own finite deadline
  deletes all manual rows (`:1107-1111`); and no API condition offers compare-and-swap on a
  row identity. Zabalack therefore cannot build an exclusive-control guarantee on this
  platform. In 0.2.0, `shelve`, `unshelve` and `timeout_unshelve` are captured and recorded,
  settle `skipped: suppression_fence_unavailable` at step 0, and the alert's projection says
  so. No cohort setting, including `accept_suppression_collateral_risk = true`, changes this.
  The best-effort write policy a later release would need is in Appendix A.
- **Pre-write refusal (R6, S4).** Only: `trigger.get` with `editable = true` returning nothing
  → `refused: no_write_rights`; `manual_close == 0` → `refused: manual_close_disabled`; event
  not a problem event of a trigger → `refused: not_problem_event`. After a write is sent, no
  JSON-RPC code is a refusal: `-32500`/`-32603` and every application/internal/unclassified
  error, HTTP 200 error envelope, non-200 response (F2) or timeout leaves the operation
  `uncertain`, suspends the instance, raises an incident and permits read-only reconciliation
  only (A6). Verified dispatch/validation rejections (`-32600/-32601/-32602`) are `failed`
  (terminal, no effect). Error text is never parsed.
- `ZabbixClient.call` write scope in 0.2.0: `action` ∈ {2, 6, 16, 1, 5}, exactly one
  `eventids` entry, `message` present iff the bitmask includes 4; `suppress_until`,
  `cause_eventid` and `severity` are never present, and bitmasks 32 and 64 are rejected by the
  client before any request is sent.
- Companion message: neutral team text + public reference, sent once per operation; the
  policy (owner decision §9 Q2) is that a missing bit 4 in the acceptance evidence leaves
  the operation `uncertain`.
- Late-attributable report protection (006/011/012) and no-replay invariants are unchanged.

## 7. Projection

Attribute family grows from `zabbix_ack_sync_*` to `zabbix_sync_<kind>_*` (r2: plus
`zabbix_sync_<kind>_event_seq` and `_outcome` so operators can see order and no-op/refusal) with the same
fields (`intent`, `reason`, `config_revision`, `local_at`, result). The reader query guard
and serializer filters cover the whole `zabbix_sync_` prefix. The release-one names stay as
aliases for `ack` so existing dashboards keep working.

## 8. Lab and evidence

Deployment-lab cases for 0.2.0: human unack; close with manual close enabled → accepted, then
resolved on a later read; close with manual close disabled → `refused: manual_close_disabled`;
same-second ack/unack/ack with watermark claims; pre-existing matching history → uncertain, no
resend; delayed close task completion observed by a later read (the operation stays `accepted`
and blocks successors until then); close accepted then ack → ack waits; failed predecessor then
valid successor → successor delivered; every captured shelve, unshelve and timeout_unshelve →
`skipped: suppression_fence_unavailable` with no Zabbix call, for each risk-policy value
(absent, false, true, unbound, invalid; the `true` case is the required negative case);
client-level rejection of bitmasks 32 and 64; housekeeping timeout on an alert shelved from
open → open in Alerta, no Zabbix write; from ack → ack in Alerta, no Zabbix write; repeat shelve
→ Alerta rejection, nothing captured; ack/unack/ack sequence → three ordered operations; an
uncertain earlier operation blocks later ones and suspends the instance; concurrent workers on
one event → single admission; bulk status change → recorded skipped, no Zabbix call; model reopen with
`mirror_model_transitions` at its default (true) → unack with history provenance; with the
cohort opt-out (false) → `skipped: mirror_disabled`; the same pair for
`mirror_system_transitions` on an ack-timeout; mutated actor/kind/mask/params → verifier refusal.
Native-lab SQL cases for migration 013 and the kind-aware fences. Coexistence profile:
a Voalack-owned alert receiving each kind → Zabalack makes no write and raises nothing.

## 9. Owner decisions (12 October 2026, recorded in the Zabalack session)

| # | Question | Decision |
|---|---|---|
| Q1 | Suppression kinds (shelve, unshelve, timeout_unshelve) | **Record only, never write in 0.2.0.** No per-cohort risk acceptance is available in 0.2.0. |
| Q2 | Companion message missing from the acceptance evidence of ack/close | **Uncertain; never resend.** Instance suspended, incident, read-only reconciliation (A6). |
| Q3 | `expired` | **No Zabbix write**; recorded `skipped: expired_no_zabbix_counterpart`. |
| Q4 | Notes | **Deferred to 0.2.1** with its own database-side committed-capture design and reviews. |
| Q5 | Mirroring `system` (ack-timeout unack) and `model` (severity-rise reopen) transitions | **On by default; per-cohort opt-out.** Chosen by the owner over the recommended opt-in. Provenance and capture rules are unchanged. |

These decisions are design inputs. They do not authorise implementation, release or deployment.

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
   schema. Evidence (T5): (a) a static write-set test over the plugin and SQL; (b) exact-role
   denial tests (the Zabalack roles have no privilege on those columns); (c) a differential
   end-to-end fixture whose observation boundary is the Zabalack hook/statement boundary, not
   the whole upstream transaction: it records each hook's received inputs and returned outputs
   and the SQL statements issued by Zabalack roles, and allows Alerta's own correlate changes
   to `event` and `create_time` (`base.py:245-251`) while proving Zabalack contributed none.
   Foreign hooks must return their received inputs unchanged and raise nothing. These tests are
   proposed, not run.
3. Zabalack never truncates `public.alerts`. Evidence: a static test; no `TRUNCATE` in the
   repository outside the lab's own disposable database setup.
4. The non-ACK paths (housekeeping/system actor, ingestion/model actor) carry their own
   source-path and transaction evidence; they inherit nothing from the ACK-path qualification.
5. Zabalack never creates, alters or drops a trigger, function, grant or other object that
   references a `voalack` or `voalack_guard` name or any `voalack_*` schema. Its only trigger on
   `public.alerts` is `zabalack_capture`, calling a function in the `zabalack` schema; 0.2.0
   keeps that one trigger name, and its exact new definition is sent to the Voalack session
   before implementation. Zabalack DDL on a shared database is not scheduled while a Voalack
   maintenance permit is open. Evidence (Astra 3ebfe880): a static name scan over `sql/` is
   supporting evidence only, because names cannot prove the absence of indirect references.
   The qualifying evidence is the joint coexistence fixture, which resolves the actual trigger
   function, its schema and its dependencies, and records the complete installed inventory on
   `public.alerts` (trigger definitions via `pg_get_triggerdef`, enabled states, and the
   relevant ACLs) for both projects. No native joint evidence exists yet.

"Agreed with the Voalack session" in this section means the bounded design review only. It is
not an implementation, runtime or project-wide approval by either project.

No part of this amendment applies to 0.1.0b1; the tag proposal and frozen artifacts are
unaffected.

## Appendix A. Future suppression write specification (not reachable in 0.2.0)

Nothing in this appendix is implemented, enabled or tested in 0.2.0. It records what a later
release would need if the owner later records, per cohort and in writing, acceptance of the
collateral risk described in §6 (`accept_suppression_collateral_risk`). That acceptance would be
risk acceptance, not a technical fence.

- Writes: shelve → 32 with `suppress_until` derived from the captured history snapshot (§4);
  unshelve and timeout_unshelve → 64. Both are task-executed: `accepted`, then completion on a
  later read, holding the event barrier meanwhile (§5).
- Policy: shelve writes only when the fresh read shows no manual row of any owner (existing
  manual row → `ok_noop: existing_manual_suppression`, recording owner, until and maintenance
  evidence; policy, not a platform skip); maintenance-only suppression → a manual-row write;
  mixed rows → policy no-write; unsuppress writes only when the fresh read shows exactly one
  manual row, ours, with a ledger deadline, else `skipped: foreign_manual_suppression`; every
  write records pre- and post-read snapshots and raises an incident when the post-read is
  inconsistent with sole ownership. No replay.
- Evidence: completion for suppress = our manual row with the derived deadline; for unsuppress
  = absence of our row. Maintenance suppression may keep `suppressed == 1` and is not drift.
- Drift fold: expected manual suppression (present/absent, observed `suppress_until`, observed
  owner) from the latest evidenced shelve/unshelve-family outcome, where a maintenance-only
  `ok_noop` records "no manual row" and folds to absent.
- Lab (future): manual, maintenance-only and mixed-row cases; shelve accepted then unshelve;
  unsuppress accepted then shelve; foreign row replacement between read and write.
