# R2 — Ingestion epoch, lock identity and current pointer (pre-read draft)

Author: Opus (Zabalack session). Status: **PROPOSED DRAFT r4, discussion input only.** Codex review: r1
CHANGES_REQUIRED (750d397d: P1, P3, P6); r2 CHANGES_REQUIRED (20261013T170941Z-codex-omarchy-d7391d2a:
Q1, Q2; §4 is now the single ingestion state machine that R3, R4 and R5 reference); r3 CHANGES_REQUIRED
(20261013T174021Z-codex-omarchy-d7d59f1e: S3, S4 answered in r4); r4 review
(20261013T180356Z-codex-omarchy-604951f7): those items closed as draft corrections; no open item in this file.
R2 was raised in 20261013T005221Z-codex-omarchy-1e79ef92. Not accepted DDL, grant or implementation. Pinned
sources as in R4. Astra's constraints carried: epoch allocation is not commit order; keep Voalack's r8
final-row AFTER / lifetime / reservation / delete / restore protections; the obsolete BEFORE-column draft
is not used.

## 1. Trusted ingestion events (existing source facts)

Alerta writes `last_receive_id` and `last_receive_time` **only** in its three ingestion statements:

| Path | Statement | Location |
|---|---|---|
| create | `INSERT` with `last_receive_id`, `last_receive_time` | `postgres/base.py:264-277` |
| dedupe (same event, no event change) | `UPDATE ... SET ..., last_receive_id, last_receive_time` | `postgres/base.py:216-240` (columns at `:226`) |
| correlate (event change within the correlate list, including severity change) | `UPDATE ... SET event, ..., last_receive_id, last_receive_time` | `postgres/base.py:242-262` (columns at `:249-250`) |

No other statement in the backend sets either column (repository-wide search of
`alerta/database/`). `set_alert`, `set_status`, `update_tags`, `update_attributes`, notes and deletes
never list them. Resource changes are not a dedupe or correlate key change in Alerta (dedupe and correlate
match on environment, resource, event or correlate list, and customer). A different resource is a create.

**Proposed event definition:** an *ingestion event* is one **affected row** of one of those three
statements. It is detected by a core trigger
`AFTER INSERT OR UPDATE OF last_receive_id, last_receive_time ON public.alerts FOR EACH ROW`. PostgreSQL
fires a column-list `UPDATE OF` trigger whenever a listed column appears in the statement's `SET` list,
**even if its value does not change** (PostgreSQL 18 `CREATE TRIGGER` documentation; a documentation and
source reading, not a native witness). A `FOR EACH ROW` trigger fires once per affected row: zero matched
rows produce no event, and several matched rows produce several events.

**One-target invariant and count mechanism (proposed, r3).** The pinned dedupe and correlate `UPDATE`s
select by natural identity and customer, not by the resolved alert ID, so they could in principle match
more than one row. PostgreSQL does not allow transition relations on a trigger with an `UPDATE OF` column
list or with several events, so r2's statement-level transition-table count is withdrawn. Instead:
- the row-level stamp trigger increments a counter in the open ingestion context (§4) for each affected row
  and records the affected row's ID;
- the core **finalize** step (§4, step F), which runs after the statement and all its triggers and before
  commit, requires the counter to be exactly 1 and the recorded row to be the reserved target. Zero (no row
  trigger fired) or two or more aborts the enlisted transaction;
- tag, attribute, status, note and delete statements never fire the column-list trigger, so they can never
  touch the counter. Only an ingestion statement is counted;
- outside an enlisted ingestion (for example webhook batches), the trigger stamps an unbound epoch per
  affected row and touches no context.

Detecting a `SET` clause is **not** authentication. Every event, including an undeclared or
unauthenticated ingestion, stamps a new epoch that is **unbound** unless an authenticated provider bind
happens in the same transaction (§4). A replayed receive ID with a recurring timestamp therefore still
produces a new, unbound epoch. This closes the clock-collision case of F1 r4.2 §3.1 by construction,
without trusting application clocks. An authenticated ingestion that happens to reuse a receive ID is a new
authenticated event and binds its own new epoch. Reuse of a receive ID is not an authentication verdict
either way.

## 2. Epoch record and current pointer (proposed)

`ack_core.alert_epoch` (one row per alert lifetime):

| Column | Meaning |
|---|---|
| `alert_id text`, `lifetime bigint` | identity; `lifetime` increments when the same `alert_id` is inserted again after a delete (ID reuse) |
| `epoch bigint` | current epoch, starting at 1 on insert, +1 per ingestion event |
| `customer text` | customer at that epoch (from `NEW.customer`) |
| `stamped_receive_id`, `stamped_receive_time` | values carried by the event (diagnostic only; never used for ordering) |
| `bound_provider`, `binding_digest` | set only by an atomic bind in the same transaction (§4); NULL = unbound |
| `stamped_at timestamptz` | `clock_timestamp()` (diagnostic only) |

Plus:
- append-only `ack_core.epoch_events` (alert_id, lifetime, epoch, statement kind, xid) for provenance;
- append-only `ack_core.epoch_bindings` (alert_id, lifetime, epoch, provider, binding_digest, xid). This is
  the **historical** binding store that admission of already-captured operations needs (R3 §2b). The
  `alert_epoch` row holds only the current pointer;
- `ack_core.alert_class` (alert_id, lifetime, class_provider or `conflict`, established_epoch, provenance,
  retired_at). This is durable, authenticated classification, kept separate from the per-epoch action-source
  binding (§4a);
- a tombstone (alert_id, last lifetime, deleted_at, xid) retained across delete and restore, so that a
  reused `alert_id` always gets a higher lifetime.
Writers (r3): `alert_epoch`, `epoch_events` and tombstones are written only by the stamp trigger function.
`ingestion_context`, `ingestion_claims`, `epoch_bindings` and `alert_class` are written only by core
routines in the §4 sequence. All are `SECURITY DEFINER` and owned by a non-provider core role.

## 3. Lock identity and ordering

- The ingestion `UPDATE` (dedupe/correlate) already holds the `public.alerts` row lock until commit. The
  core trigger then locks the `ack_core.alert_epoch` row (`SELECT ... FOR UPDATE`) and increments `epoch`.
  For `INSERT`, the new row is invisible until commit, and a unique `(alert_id, lifetime)` key serializes
  concurrent creates.
- **Order = commit order per alert lifetime, across different transactions.** The next ingestion of the
  same row waits on the row lock until the previous transaction ends.
- **Single ingestion per enlisted transaction.** Two ingestions of one alert inside one transaction would
  commit together, so "a later epoch commits later" would not hold. The enlisted scope therefore admits at
  most one ingestion event per alert: a second stamp for the same alert in the same transaction aborts it.
  Webhook batches and other multi-alert paths are not enlisted. The epoch reservation (R3) is single-use: it
  is consumed by finalize, and cleared on success, exception, suppression, rerouting, re-entrant calls and
  teardown.
- Epoch values are assigned under that lock, so across transactions a later epoch always commits later.
  Rolled-back events release the row, and their increment rolls back too: no gap is visible. Gaps would be
  harmless anyway, because an epoch is never inferred from a number.
- Proposed global lock order for any transaction that needs several of them: request reservation row
  (R4 §3) → `public.alerts` row → `ack_core.alert_epoch` row → class or ownership row → provider-private
  fence or binding rows.

## 4. Ingestion state machine (single source of truth, r3)

POST /alert (single alert) runs as an **unowned enlisted ingestion transaction**. This is the one
exception to R4's rule that an enlisted request has one pre-resolved owner: at ingestion, the owner is an
output, not an input. Step names are used by R3, R4 and R5.

| Step | Operation | Writer | Allowed epoch | Context state after |
|---|---|---|---|---|
| R | **Reserve.** Before any hook, the core resolves the target by Alerta's dedupe/correlate identity, locks the `public.alerts` row (if it exists) and its `alert_epoch` row, and writes `ingestion_context` (request_id, alert_id or `new`, lifetime, reserved `E = current + 1` or 1, nonce, counter 0) | core | reserves E | `reserved` |
| C | **Claim.** During the `pre_receive` loop, each registered provider that authenticates the ingestion calls the core claim entry point with its registration capability. It writes `ingestion_claims` (provider, E, source digest). | core claim routine, on behalf of the capability holder | E only | `reserved` |
| K | **Decide (close claims).** After the `pre_receive` loop and before the statement, under the epoch-row lock, the core evaluates the claims **together with the existing protected class and provenance** and every conflict predicate: two or more claims; a sole claim from a provider other than the current class holder; an existing `conflict` class; or a disagreeing Voalack r8 class record → decision `conflict`: no provisional owner, no pending bind, no signing allowed. A sole claim from the current class holder, or from the first claimant when no class exists → provisional owner and a **pending bind** for E. Zero claims → no owner, no pending bind | core | E only | `claims_closed` |
| S | **Sign (correlate only).** In `status_change` (`models/alert.py:359`), only the provisional owner may sign, over the R3 canonical bytes (E, lifetime, nonce, owner-decision digest) | provider | E | `claims_closed` |
| T | **Statement and triggers.** The ingestion SQL runs. The stamp trigger increments the counter, records the row and stamps E. Provider capture triggers record **candidate** captures that reference the context **nonce**, not the stamp, so they do not depend on trigger order (Q2) | trigger functions | E | `stamped` |
| F | **Finalize** (after the statement and `post_receive`, before commit), in this order: (1) counter = 1, recorded row = target, stamp = E, lifetime matches, or else **abort**; (2) re-evaluate the K decision under the lock; if it is now `conflict`, drop the pending bind; (3) only for a non-conflict decision with a pending bind, write `epoch_bindings`; (4) record the class transition (establish, confirm, carry, or `conflict` **without** any binding); (5) persist the context as committed evidence (nonce → alert, lifetime, E, final owner decision). A conflict decision leaves no admissible binding, no owner-decision digest naming a provider, and any provisional signature or candidate stays inert and is refused at admission | core | E | `finalized` |
| X | **Commit**; any mismatch at F, or any exception, rolls back R-T entirely | core | — | consumed |

Consequences:
- A failed or mismatched finalize aborts atomically. A committed epoch never has a silently failed bind.
- Zero claims: E is unbound, and the class carries (§4a).
- Two claims: the class becomes `conflict` at F, without a binding. The Alerta ingestion itself commits,
  because the alert belongs to Alerta.
- Candidate captures become admissible only through the committed context mapping nonce → E plus the
  committed binding (R3 §2, R5 §2c).
- Mutable `source_system` is never authority at any step.

## 4a. Classification versus binding (P1)

Classification answers "whose alert is this?". Binding answers "which source does this exact ingestion
carry?". They are kept apart, and both change only at §4 step F:
- **Established** by the first finalized ingestion of a lifetime with exactly one claim: that provider's
  binding for E, plus `alert_class.class_provider` (bootstrap).
- **Carried** across later ingestions with zero claims (undeclared, suppressed or routed-away). E is
  unbound and the class is unchanged, so the alert is **owned-unbound**, never foreign.
- **Conflicted** when the K/F decision sees two claims, a sole claim from a provider other than the current
  class holder, an existing conflict, or a disagreeing Voalack r8 class record. `class_provider` becomes
  `conflict`, with provenance and without a binding. Conflict is **sticky**: a later single claim does not
  repair it. Only a lifetime change clears it. There is no silent transfer between providers.
- **Retired** only by a lifetime change (delete, then re-create with the same ID).
- Mutable `source_system` is never a fallback.

## 5. Use at action time

Under the action lock (row lock taken by the action's first write, or an explicit `FOR UPDATE` by the core
at enrolment), re-read: current lifetime and epoch, customer, `bound_provider`, `binding_digest` and the
ownership decision (R5). Then:

| Condition | Result |
|---|---|
| current epoch bound by this owner, digest matches the action's source | admit |
| class = provider, current epoch unbound (owned-unbound, for example after an undeclared ingestion) | refuse the managed action (owner-private reason `source_binding_missing`); the alert stays owned, never becomes foreign |
| stale at signing or capture (the action names an epoch that is not current) | refuse (`source_binding_stale`) |
| lifetime mismatch (ID reuse) | refuse |
| class `conflict` (R5) | refuse the managed action for both providers without disclosing membership |

## 6. Explicit unknowns

1. Whether `UPDATE OF` column-list firing holds for every Alerta deployment variant (for example, a
   different backend or a future Alerta version that rewrites the SQL). It is a pinned-source fact for
   9.1.0 only, and is proven by a native witness, not by reading.
2. How the core trigger coexists with Voalack's r8 frozen AFTER UPDATE OR DELETE / AFTER INSERT triggers:
   firing order (alphabetical by trigger name in PostgreSQL) and whether either reads the other's state.
   Merging responsibilities would be a joint migration change (Astra).
3. The delete/restore lifecycle of `lifetime` under Voalack's no-TRUNCATE and restore protections.
4. The F1 r4.2 bindings (`zabalack.source_bindings` with `bind_seq`) would be superseded by core epoch
   binds. The migration path, and whether 013a ships before 3b, is open.
