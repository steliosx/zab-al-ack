# R4 — Transaction owner and unknown-COMMIT state machine (pre-read draft)

Author: Opus (Zabalack session). Status: **PROPOSED DRAFT, discussion input only.** Codex review: pending
(R4 was raised by Codex in 20261013T005221Z-codex-omarchy-1e79ef92). Not accepted design, DDL, grant or
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
| `update_tags`, `update_attributes`, `set_status`, `set_alert` all go through `_updateone` | `postgres/base.py:330-349`, `:303-310`, `:279-291` |

Consequence: any write on `g.db` commits everything pending on that connection, including a provider's
uncommitted writes made earlier on the same connection.

### 1.2 Action path (`PUT /alert/<id>/action`)

| Step | Call | Commits |
|---|---|---|
| 1 | `views/alerts.py:166` `process_action(...)` (pre-action) → plugin `take_action` loop (`utils/api.py:95-128`) | none by Alerta inside the loop; a provider may commit (Zabalack does: `stage_generation`, `plugins.py:298-321`) |
| 2 | if any plugin returned an update: `alert.update_tags` then `alert.update_attributes` (`utils/api.py:129-131`; `models/alert.py:460-466`) | **two separate commits** (`_updateone`) |
| 3 | `views/alerts.py:168` `alert.from_action(...)` (`models/alert.py:644-697`): `status_change_hook.send` at `:666` → `HookTrigger.process_status_change` (`utils/hooks.py:35-38`) → `process_status` plugin `status_change` loop (`utils/api.py:171-199`) | if any plugin returned an update: `update_tags` + `update_attributes` (`utils/api.py:196-198`) = **two more commits** |
| 4 | `db.set_alert(...)` with the new status and history row (`models/alert.py:686-696`; `postgres/base.py:279-291`) | **commit**; the `AFTER UPDATE` capture triggers of providers fire inside this statement and commit with it |
| 5 | `views/alerts.py:170` `process_action(..., post_action=True)` → plugin `post_action` loop | `update_tags` + `update_attributes` if updated = **two more commits** |
| 6 | exceptions: `RejectException` → 400, `InvalidAction` → 409, `AlertaException` → its code, any other → 500 (`views/alerts.py:171-185`) | earlier commits persist; nothing is rolled back |

### 1.3 Housekeeping path (`GET /management/housekeeping`)

`management/views.py:159` onward: for each expired alert, `process_action(expired)` → `from_expired` →
`process_action(..., post_action=True)` (`:179-183`); for each shelve/ack timeout,
`process_action(timeout)` → `from_timeout` → post-action (`:199-203`). Same commit pattern as 1.2, per alert,
in one loop on one `g.db`. A per-alert exception is caught (`:204-212`), and the loop continues.

### 1.4 Ingestion path (`POST /alert`)

`utils/api.py:31-92` `process_alert`: `pre_receive` loop → `deduplicate` / `update` (correlate) / `create`
(`utils/api.py:53-63`). Dedupe and correlate send `status_change_hook` **before** their SQL
(`models/alert.py:301` then `:335`; `:359` then `:379`). The SQL statement commits (`dedup_alert` and
`correlate_alert` via `_updateone`, `postgres/base.py:216-262`; `create_alert` via `_insert`, `:264-277`).
Then the `post_receive` loop runs, and `update_tags` + `update_attributes` commit if any plugin returned an
alert (`utils/api.py:66-90`).

### 1.5 Zabalack today

`stage_generation()` commits explicitly on `g.db` before Alerta's status commit (`plugins.py:298-321`). That
is the only way to keep its staged intent from riding Alerta's `update_tags` commit in step 2. Its capture
trigger writes the capture and outbox rows inside step 4's statement. They therefore commit atomically with
the history row. A failure between step 1's staging commit and step 4 leaves a staged intent with no
committed capture. The existing retirement sweep removes it, and the worker never sees it (DESIGN 5.3).

## 2. Proposed default (accepted in principle on both sides; details open)

One **core-owned request transaction** per enlisted request, with exactly one provider enlisted (the
resolved owner, R5), no intermediate commit by Alerta or by any provider, and one final commit.

1. ack-core opens the request scope (WSGI wrapper or `before_request`) and marks `g` as enlisted.
2. While enlisted, the Alerta write helpers (`_insert`, `_updateone`, `_updateall`) **execute without
   committing**. This needs a reviewed core shim of those three helpers. Patching providers alone is not
   enough (Codex R4: "removing only the plugin's commit would be wrong").
3. The owner's `take_action` stages (writes intent and fence) **without** committing. Non-owner hooks must
   return unchanged before touching private state, and must leave no pending write on `g.db`.
4. Alerta's `update_tags` / `update_attributes` / `set_alert` run as today, uncommitted. The capture trigger
   fires in `set_alert`'s statement and sees the staged intent and fence row in the same transaction.
5. `post_action` runs.
6. The core performs the **single final commit**. Any exception before it rolls back everything: staging,
   tags, attributes, status, history and capture are all-or-nothing.
7. The response is sent only after the commit outcome is known (state machine below).

The same shape applies to housekeeping, with **one transaction per alert** inside the loop (not one for the
whole loop). It applies to ingestion too: the epoch stamp (R2), the provider bind and the ingestion SQL
commit together. That makes the ingestion path atomic, which is what removes the F1 freshness residual
(F1 design §3.1) and what R3 needs.

## 3. Unknown local COMMIT — state machine (proposed)

This is distinct from uncertain **remote** writes (Zabalack worker `uncertain`, never resent; instance
suspension; DESIGN §5/§6). Here the uncertainty is whether the **local** Alerta transaction committed.

| State | Entered when | Exit |
|---|---|---|
| `open` | enlisted scope begins | `committing` or `rolled_back` |
| `committing` | core calls `COMMIT` | `committed` (commit returned); `rolled_back` (commit raised a definite error, for example a serialization failure before commit); `commit_unknown` (connection lost or an ambiguous error during commit) |
| `committed` | — | response 2xx |
| `rolled_back` | — | response 4xx/5xx; nothing persisted; no external effect is possible, because the outbox row never became visible |
| `commit_unknown` | ambiguous commit | the response is 503 with the stable `action_id` (generated before staging and returned in the error body). Reconciliation reads, on a **new** connection, the capture and outbox rows by `action_id`. Present means committed; absent after the connection is gone means not committed (atomicity). No blind replay. The client may resend the request only with the same `action_id`, so a duplicate is detected (`ON CONFLICT` on the action id) |

External effect only after committed admission: the worker reads only committed outbox rows, so nothing can
be sent on preparation alone.

## 4. Explicit unknowns

1. How ack-core enforces "no commit" on helpers that other installed plugins may call directly (a shim on
   `_insert/_updateone/_updateall` covers Alerta; direct `g.db.commit()` by a foreign plugin is not
   covered). Proposal: enlisted scope + a commit guard that raises if anything else commits while enlisted.
2. Lock order between the per-alert row lock (taken by the first UPDATE), provider fence locks and core
   ownership/epoch locks. This must be one documented order; a proposal is in R2 §3.
3. Housekeeping: how a per-alert transaction interacts with Alerta's caught exceptions (`:204-212`), which
   today continue the loop after partial commits.
4. Response timing for `RejectException`/`InvalidAction` raised after staging. These become plain
   rollbacks under the default; confirm no client relies on partial state.
5. An independently durable preparation transaction (non-dispatchable provisional state + reconciliation)
   remains a separate, unaccepted design. Zabalack does not need it (handoff v3 §5.2).
