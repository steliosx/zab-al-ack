# R2 — Ingestion epoch, lock identity and current pointer (pre-read draft)

Author: Opus (Zabalack session). Status: **PROPOSED DRAFT r2, discussion input only.** Codex review: r1
CHANGES_REQUIRED (20261013T163920Z-codex-omarchy-750d397d; P1, P3 and P6 answered here); r2 pending.
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

**One-target invariant (proposed).** The pinned dedupe and correlate `UPDATE`s select by natural identity
and customer, not by the resolved alert ID, so they could in principle match more than one row. For an
enlisted ingestion, the core requires **exactly one affected row**. A statement-level AFTER trigger with a
transition table counts the rows, and zero or more than one aborts the enlisted transaction. It also
requires that row's ID to equal the alert the core resolved for the ingestion (R3 §2).

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
Rows are written only by the core trigger function (`SECURITY DEFINER`, owner a non-provider core role).

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
  is consumed by the stamp, and cleared on success, exception, suppression, rerouting, re-entrant calls and
  teardown. Epoch values are assigned under that lock, so a later epoch
  always commits later. Rolled-back events release the row and their increment rolls back too: no gap is
  visible, and gaps would be harmless anyway (an epoch is never inferred from a number).
- Proposed global lock order for any transaction that needs several of them: `public.alerts` row →
  `ack_core.alert_epoch` row → `ack_core.alert_ownership` row → provider-private fence or binding rows.

## 4. Bind and enrolment atomicity

Under R4's core-owned ingestion transaction, the stamp, the provider's bind and the ingestion SQL commit
together:
1. The owner's `pre_receive` validates the authenticated source (Zabalack's existing ingestion checks).
2. The ingestion SQL runs and the core trigger stamps epoch E.
3. Within the same transaction, the provider binds **epoch E** through a core function, which reads E under
   the epoch-row lock. A bind for any epoch other than the current one is refused.
4. One final commit makes the stamp and bind visible together. An ingestion with no authenticated hand-off
   leaves E unbound. A failed bind rolls back the whole ingestion, so the alert never shows a committed
   epoch whose bind failed silently.

Under the current (non-atomic) Alerta path, step 3 cannot name E reliably, because `post_receive` runs
after the ingestion commit. That is why F1 r4.2 ranks by recording order and why the residual exists.

## 4a. Classification versus binding (P1)

Classification answers "whose alert is this?". Binding answers "which source does this exact ingestion
carry?". The two are kept apart:
- **Established** by the first authenticated, bound ingestion of a lifetime: that provider's bind sets
  `alert_class.class_provider` in the same transaction.
- **Carried** across later ingestions, including undeclared, suppressed or routed-away ones. Those stamp an
  unbound epoch, but the class does not change. So the alert is **owned-unbound**, never foreign.
- **Conflicted** when a different provider presents authenticated class evidence for the same lifetime
  (Zabalack's bind or Voalack's r8 class record): `class_provider` becomes `conflict`, with provenance.
- **Retired** only by a lifetime change (delete, then re-create with the same ID).
- Mutable `source_system` is never a fallback.

**Bootstrap.** POST /alert is not enlisted "to an owner" in advance. The core runs the ingestion
transaction, and each registered provider's `pre_receive` may return an authenticated claim:
- exactly one claim: that provider binds the stamped epoch, and the class is established or confirmed;
- two claims: the class becomes `conflict`, nobody binds, and the ingestion itself still commits (it is
  Alerta's alert);
- zero claims: the epoch is unbound and the class is unchanged.

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
