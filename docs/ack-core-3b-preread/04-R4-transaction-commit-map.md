# R4 — Transaction owner and unknown-COMMIT state machine (pre-read draft)

Author: Opus (Zabalack session). Status: **PROPOSED DRAFT r2, discussion input only.** Codex review: r1
CHANGES_REQUIRED (20261013T163920Z-codex-omarchy-750d397d, P4 and P7 answered here); r2 pending. R4 was
raised by Codex in 20261013T005221Z-codex-omarchy-1e79ef92. Not accepted design, DDL, grant or
implementation. Source facts below are read from the pinned Alerta 9.1.0 package (upstream commit
`f4312727783dad64198b62ed778bbca98a5144a3`, PyPI `alerta-server==9.1.0`) and zab-al-ack `main`
`d06a363259633918f3df2eedf58c99ecc5468414`. Line numbers refer to those exact files. Nothing was run.

## 1. Existing behaviour (reviewed source, not proposal)

### 1.1 Connection and commit primitives

| Fact | Location |
|---|---|
| One connection per Flask app context: `get_db()` stores it in `g.db`; `teardown_db` closes it at context end | `alerta/database/base.py:96-104` (registered at `:70`) |
| psycopg2 connection with default transaction semantics (not autocommit); `SET search_path` then commit at connect | `alerta/database/backends/postgres/base.py:95-120` |
| Every write helper commits immediately: `_insert` | `postgres/base.py:1538-1545` |
| `_updateone` executes, then `self.get_db().commit()` | `postgres/base.py:1568-1576` |
| `_updateall` commits | `postgres/base.py:1578-1586` |
| `_upsert` delegates to `_insert` (commits) | `postgres/base.py:1588-1592` |
| `_deleteone` and `_deleteall` commit | `postgres/base.py:1594-1611` (commits at `:1601`, `:1611`) |
| `update_tags`, `update_attributes`, `set_status`, `set_alert` all go through `_updateone` | `postgres/base.py:330-349`, `:303-310`, `:279-291` |

Consequence: any write on `g.db` commits everything pending on that connection, including a provider's
uncommitted writes made earlier on the same connection. Work still pending when the app context ends is
discarded when `teardown_db` closes the connection.

### 1.2 Action path (`PUT /alert/<id>/action`)

| Step | Call | Commits |
|---|---|---|
| 1 | `views/alerts.py:166` `process_action(...)` (pre-action) → plugin `take_action` loop (`utils/api.py:95-128`) | none by Alerta inside the loop; a provider may commit (Zabalack does: `stage_generation`, `plugins.py:298-321`) |
| 2 | if any plugin returned an update: `alert.update_tags` then `alert.update_attributes` (`utils/api.py:129-131`; `models/alert.py:460-466`) | **two separate commits** (`_updateone`) |
| 3 | `views/alerts.py:168` `alert.from_action(...)` (`models/alert.py:644-697`): `status_change_hook.send` at `:666` → `HookTrigger.process_status_change` (`utils/hooks.py:35-38`) → `process_status` plugin `status_change` loop (`utils/api.py:171-199`) | if any plugin returned an update: `update_tags` + `update_attributes` (`utils/api.py:196-198`) = **two more commits** |
| 4 | `db.set_alert(...)` with the new status and history row (`models/alert.py:686-696`; `postgres/base.py:279-291`) | **commit**; the `AFTER UPDATE` capture triggers of providers fire inside this statement and commit with it |
| 5 | `views/alerts.py:170` `process_action(..., post_action=True)` → plugin `post_action` loop | `update_tags` + `update_attributes` if updated = **two more commits** |
| 6 | exceptions and early responses: `RejectException` → 400, `InvalidAction` → 409, `ForwardingLoop` → **202 success response** (`views/alerts.py:178-179`), `AlertaException` → its code, any other → 500 (`views/alerts.py:171-185`) | writes committed by earlier steps persist; still-pending work is discarded at teardown |

### 1.3 Housekeeping path (`GET /management/housekeeping`)

`management/views.py:159` onward:
- **Preliminary work before any loop:** `Alert.housekeeping` (`:173`; `models/alert.py:609-614`) calls
  `db.get_expired`, which **deletes** old expired and old informational alerts with `_deleteall` (commits),
  then selects (`postgres/base.py:1478-1500`), followed by the `get_unshelve` and `get_unack` selects.
- For each expired alert: `process_action(expired)` → `from_expired` → `process_action(..., post_action=True)`
  (`:176-183`). For each shelve/ack timeout: `process_action(timeout)` → `from_timeout` → post-action
  (`:196-203`). Same commit pattern as 1.2, per alert, on one `g.db`.
- A `RejectException` is caught, recorded and the loop **continues** (`:184-189`, `:204-209`). Any other
  exception raises `ApiError` 500 and **aborts the whole request** (`:190-191`, `:210-211`), leaving earlier
  alerts' commits in place.

### 1.4 Ingestion path (`POST /alert`)

`utils/api.py:31-92` `process_alert`: `pre_receive` loop → `deduplicate` / `update` (correlate) / `create`
(`utils/api.py:53-63`). Dedupe and correlate send `status_change_hook` **before** their SQL
(`models/alert.py:301` then `:335`; `:359` then `:379`). The SQL statement commits (`dedup_alert` and
`correlate_alert` via `_updateone`, `postgres/base.py:216-262`; `create_alert` via `_insert`, `:264-277`).
Then the `post_receive` loop runs, and `update_tags` + `update_attributes` commit if any plugin returned an
alert (`utils/api.py:66-90`).

### 1.5 Zabalack today

`stage_generation()` commits explicitly on `g.db` before Alerta's status commit (`plugins.py:298-321`; the
commit is at `:317`). That is the current staging/retirement design for keeping its staged intent off
Alerta's `update_tags` commit in step 2. It is not the only conceivable mechanism, and it stays distinct from
the single-transaction proposal in §2. Its capture
trigger writes the capture and outbox rows inside step 4's statement. They therefore commit atomically with
the history row. A failure between step 1's staging commit and step 4 leaves a staged intent with no
committed capture. The existing retirement sweep removes it, and the worker never sees it (DESIGN 5.3).

## 2. Proposed default (accepted in principle on both sides; details open)

One **core-owned request transaction** per enlisted request, with exactly one provider enlisted (the
resolved owner, R5), no intermediate commit by Alerta or by any provider, and one final commit.

1. ack-core opens the request scope (WSGI wrapper or `before_request`) and marks `g` as enlisted.
2. While enlisted, **every** Alerta backend write helper (`_insert`, `_upsert`, `_updateone`, `_updateall`,
   `_deleteone`, `_deleteall`) **executes without committing**. This needs a reviewed core shim of all of
   them, plus a commit guard: any direct `commit()`, `rollback()` or replaced connection on `g.db` while
   enlisted raises and forces rollback. Patching providers alone is not enough (Codex R4: "removing only the
   plugin's commit would be wrong").
   Enlisted paths are only the action endpoint, POST /alert (single alert) and housekeeping per alert. Any
   other path that touches an owned alert (bulk routes, status route, webhook batches) is not enlisted, and
   managed actions there are refused, as today.
3. The owner's `take_action` stages (writes intent and fence) **without** committing. Non-owner hooks must
   return unchanged before touching private state, and must leave no pending write on `g.db`.
4. Alerta's `update_tags` / `update_attributes` / `set_alert` run as today, uncommitted. The capture trigger
   fires in `set_alert`'s statement and sees the staged intent and fence row in the same transaction.
5. `post_action` runs.
6. The core performs the **single final commit**. Any exception before it rolls back everything: staging,
   tags, attributes, status, history and capture are all-or-nothing.
7. The response is sent only after the commit outcome is known (state machine below). Handled
   exceptions and early responses (`RejectException` 400, `InvalidAction` 409, `ForwardingLoop` 202) pass
   through the same core decision: they roll back unless the core explicitly commits, and none bypasses
   it.

The same shape applies to housekeeping, with **one transaction per alert** inside the loop (not one for the
whole loop). The preliminary `Alert.housekeeping` deletes and selects run before the loop, **outside** any
enlisted transaction, exactly as today. They are not managed actions. A per-alert generic exception rolls
back that alert's transaction. Whether the request then aborts (Alerta today) or continues is open
question 3. It applies to ingestion too: the epoch stamp (R2), the provider bind and the ingestion SQL
commit together. That makes the ingestion path atomic, which is what removes the F1 freshness residual
(F1 design §3.1) and what R3 needs.

## 3. Unknown local COMMIT — state machine (proposed)

This is distinct from uncertain **remote** writes (Zabalack worker `uncertain`, never resent; instance
suspension; DESIGN §5/§6). Here the uncertainty is whether the **local** Alerta transaction committed.

**Durable outcome witness.** Each enlisted transaction writes one core request record in the same
transaction: `ack_core.requests` (request_id, customer, alert_id, lifetime, kind, request_digest over the
immutable request payload, xid = `pg_current_xact_id()`, outcome = `committed_capture` /
`committed_no_capture` / `committed_refused` / `local_only`). It is retained for the reconciliation and
retry window. Absence of a capture or outbox row is **not** evidence of rollback, because committed
no-capture and refusal branches have neither.

| State | Entered when | Exit |
|---|---|---|
| `open` | enlisted scope begins; request_id fixed | `committing` or `rolled_back` |
| `committing` | core calls `COMMIT` | `committed` (commit returned); `rolled_back` (a definite error before commit, for example a serialization failure); `commit_unknown` (connection lost or an ambiguous error during commit) |
| `committed` | — | 2xx (or the handled response code) |
| `rolled_back` | — | 4xx/5xx; nothing persisted; no external effect is possible, because no outbox row ever became visible |
| `commit_unknown` | ambiguous commit | 503 with request_id and xid. Reconciliation runs **on the authoritative primary only** (never a replica): `pg_xact_status(xid)` gives `committed`, `aborted` or `in progress`. While `in progress`, the state stays `commit_unknown`. `committed` is confirmed by reading the request record by request_id. `aborted` means rolled back. NULL (xid too old) keeps `commit_unknown`, and replay is refused. |

**Retry identity.**
- Clients supply the request_id as an idempotency key (header), so a lost response leaves the client
  holding the ID.
- A retry with the same request_id and the **same** request_digest returns the recorded outcome.
- A retry with the same ID and a **different** digest is refused (409). It is never silently accepted.
- Ingestion: each POST /alert is its own request. A retry is a new ingestion (new epoch, R2) and is
  authenticated afresh.
- Housekeeping: the request_id is server-generated per alert and recorded. A housekeeping run is
  idempotent by design: a timeout already applied is not reapplied.

Determining a local commit does not cancel or replay anything. Committed work may already have been
dispatched by a worker. External effect happens only after committed admission, because the worker reads
only committed outbox rows.

## 4. Explicit unknowns

1. The exact interception point for the commit guard (wrapping the psycopg2 connection object versus
   the backend helpers), so a direct `g.db.commit()`, `rollback()` or connection swap by any installed
   plugin is detected while enlisted.
2. Lock order between the per-alert row lock (taken by the first UPDATE), provider fence locks and core
   ownership/epoch locks. This must be one documented order; a proposal is in R2 §3.
3. Housekeeping: after a generic per-alert exception, Alerta today aborts the request (`:190-191`,
   `:210-211`), while `RejectException` continues. Under per-alert transactions, should a generic failure
   still abort the remaining alerts?
4. Response timing for `RejectException`/`InvalidAction` raised after staging. These become plain
   rollbacks under the default; confirm no client relies on partial state.
5. An independently durable preparation transaction (non-dispatchable provisional state + reconciliation)
   remains a separate, unaccepted design. Zabalack does not need it (handoff v3 §5.2).
