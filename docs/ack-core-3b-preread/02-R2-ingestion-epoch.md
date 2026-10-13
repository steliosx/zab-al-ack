# R2 — Ingestion epoch, lock identity and current pointer (pre-read draft)

Author: Opus (Zabalack session). Status: **PROPOSED DRAFT, discussion input only.** Codex review: pending
(R2 raised in 20261013T005221Z-codex-omarchy-1e79ef92). Not accepted DDL, grant or implementation. Pinned
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

**Proposed event definition:** an *ingestion event* is exactly one execution of one of those three
statements. It is detected by a core trigger
`AFTER INSERT OR UPDATE OF last_receive_id, last_receive_time ON public.alerts FOR EACH ROW`. PostgreSQL
fires a column-list `UPDATE OF` trigger whenever a listed column appears in the statement's `SET` list,
**even if its value does not change**. So a replayed receive ID with a recurring timestamp still produces a
new event. This closes the clock-collision case of F1 r4.2 §3.1 by construction, without trusting
application clocks. "No-event" ingestion (an ingestion that changes nothing visible) is still an event.

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

Plus append-only `ack_core.epoch_events` (alert_id, lifetime, epoch, statement kind, txid) for provenance.
Rows are written only by the core trigger function (`SECURITY DEFINER`, owner a non-provider core role).

## 3. Lock identity and ordering

- The ingestion `UPDATE` (dedupe/correlate) already holds the `public.alerts` row lock until commit. The
  core trigger then locks the `ack_core.alert_epoch` row (`SELECT ... FOR UPDATE`) and increments `epoch`.
  For `INSERT`, the new row is invisible until commit, and a unique `(alert_id, lifetime)` key serializes
  concurrent creates.
- **Order = commit order per alert lifetime**, because the next ingestion of the same row waits on the row
  lock until the previous transaction ends. Epoch values are assigned under that lock, so a later epoch
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

## 5. Use at action time

Under the action lock (row lock taken by the action's first write, or an explicit `FOR UPDATE` by the core
at enrolment), re-read: current lifetime and epoch, customer, `bound_provider`, `binding_digest` and the
ownership decision (R5). Then:

| Condition | Result |
|---|---|
| current epoch bound by this owner, digest matches the action's source | admit |
| current epoch unbound (owned-unbound) | refuse the managed action (owner-private reason `source_binding_missing`); the alert stays owned, never becomes foreign |
| stale (the action names an epoch that is not current) | refuse (`source_binding_stale`) |
| lifetime mismatch (ID reuse) | refuse |
| ownership `conflict` (R5) | refuse the managed action for both providers without disclosing membership |

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
