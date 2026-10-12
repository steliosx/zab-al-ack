# Design amendment — 0.2.0 action synchronisation (Alerta → Zabbix)

Status: DRAFT for review (Codex, Astra, Voalack session). Implementation starts after the
0.1.0b1 tag (owner decision). Owner scope decisions of 11 October 2026 are in the owner-local
record `OWNER-DECISIONS-ZABALACK-ACTION-EXPANSION-20261011.md`; this document turns them into
a design. Nothing here changes the frozen 0.1.0b1 candidate.

## 1. Scope

| Decision | Value |
|---|---|
| Direction | Alerta → Zabbix only. No import of Zabbix operations. |
| Alerta actions in scope | `ack` (existing), `unack`, `close`, `shelve`, `unshelve`, note (`take_note`), and Alerta's automatic transitions `timeout` (ack-timeout unack, shelve-timeout unshelve) and `expired`; severity-change reopen (dedupe/correlate path). |
| Zabbix operations | acknowledge (2), unacknowledge (16), add message (4), close (1), suppress (32, `suppress_until`), unsuppress (64). Not in scope: severity (8), cause/symptom rank (128/256). |
| Entry paths | `PUT /alert/<id>/action`, `PUT /alert/<id>/note`, and Alerta housekeeping (`GET /management/housekeeping`, which runs `process_action` with `timeout`/`expired`). Out of scope, recorded as skipped: direct `PUT /alert/<id>/status`, every `/_bulk/*` route, tag/attribute edits, `DELETE /alert/<id>`. |
| Failure semantics | Asynchronous with projection for every kind, exactly as release one: Alerta applies the action; the worker delivers; the outcome (`ok`, `refused` with reason, `uncertain`) is projected into alert attributes. No synchronous Zabbix pre-checks. |
| Sources | Zabbix-sourced alerts only (`source_system == "zabbix"`, one-to-one cohorts). |

## 2. Action-kind table

Facts: Alerta 9.1.0 alarm model `alerta` (`models/alarms/alerta.py`), hooks in `utils/api.py`,
housekeeping in `management/views.py:159-224`; Zabbix 7.0 `event.acknowledge`.

| Alerta activity | Hook that sees it | Alerta transition | Zabbix write (`action`, params) | Precondition on the Zabbix event | Idempotency / "ok" evidence |
|---|---|---|---|---|---|
| Human `ack` | `take_action`, `status_change` | open → ack | 2 or 6 (+message) | problem event, not acknowledged | existing release-one path |
| Human `unack` | `take_action`, `status_change` | ack → previous status | 16 | acknowledged | Zabbix skips if not acknowledged → treat skip as `ok_noop` |
| Human `shelve` | `take_action`, `status_change` | open/ack → shelved | 32 + `suppress_until` = now + `alert.timeout` (default `SHELVE_TIMEOUT` 7200 s; request value if given) | not suppressed | re-shelve = new `suppress_until` → write again |
| Human `unshelve` | `take_action`, `status_change` | shelved → previous | 64 | suppressed | skip if not suppressed → `ok_noop` |
| Human `close` | `take_action`, `status_change` | * → closed | 1 (+4 with message) | problem event not resolved; trigger allows manual close; RW rights | already resolved → `ok_noop`; manual close disabled → `refused: manual_close_disabled` |
| Note added/updated | `take_note` | none | 4 with message = neutral team text + reference (+ no user content by default; see §6) | problem event | each note is a new message; no dedup across notes |
| Housekeeping `timeout` on shelved | `take_action(action="timeout")` + `post_action` | shelved → open | 64 | suppressed | as unshelve |
| Housekeeping `timeout` on ack | `take_action(action="timeout")` + `post_action` | ack → open | 16 | acknowledged | as unack |
| Housekeeping `expired` | `take_action(action="expired")` | * → expired | none (owner decision pending §9 Q1) | — | recorded `skipped: expired_no_zabbix_counterpart` unless Q1 says close |
| Severity-change reopen (ACK-4) | `status_change` only (dedupe/correlate; no `take_action`) | ack → open | 16 | acknowledged | needs the status_change-only capture path (§4.3) |
| `open`, `assign`, unknown action strings | `take_action` | per model | none | — | `skipped: no_zabbix_counterpart` |
| Direct `/status`, bulk routes, delete | `status_change` / none | varies | none | — | `skipped: entry_path_out_of_scope` (bulk tag/attributes/delete fire no hooks at all; nothing to record) |

Zabbix facts that shape the table: only problem events of triggers can be updated; repeating an
action (ack on acknowledged, suppress on suppressed, close on resolved) is skipped by Zabbix
without error; close and severity need trigger read/write rights; close needs manual close
enabled on the trigger (`event.acknowledge` documentation, 7.0).

## 3. Actor model

Release one has one actor type: an authenticated human on the `/action` route. 0.2.0 adds:

| Actor type | Source of identity | Where it appears |
|---|---|---|
| `human` | `g.user_id`, `g.login`, scopes, customers (as today) | `/action`, `/note` |
| `system` | the housekeeping caller: Alerta housekeeping runs in a request context whose principal is the API key or user that invoked `GET /management/housekeeping`; the plugin records `actor.type = "system"`, `actor.trigger = "timeout" | "expired"`, and the invoking principal's login as `actor.invoked_by` | housekeeping transitions |
| `model` | no request principal; Alerta's own alarm model changed the status during ingestion | severity-change reopen (ACK-4) via `status_change` on dedupe/correlate |

Rules: `system` and `model` actions never use the human comment text; their Zabbix message
(when action 4 is included) is the neutral team text with `trigger` named. Cohort policy gets
two new booleans: `mirror_system_transitions` and `mirror_model_transitions`, default false, so
a customer can enable human-only sync first.

## 4. Capture paths

### 4.1 Request path (`/action`, `/note`)
Unchanged mechanics: stage an unsigned intent in `take_action`/`take_note`, sign the binding
in the final `status_change` (for `/note`, which has no status change, sign in `take_note`
itself after the note is persisted; the note id is part of the binding). The intent gains
`kind` (ack|unack|close|shelve|unshelve|note|timeout_unshelve|timeout_unack|model_unack),
`zabbix_action` (bitmask), `params` (`message`, `suppress_until`), `actor.type`.

### 4.2 Housekeeping path
`take_action(action="timeout"|"expired")` fires inside the housekeeping request. The plugin
derives the kind from the alert's status before the transition (shelved → `timeout_unshelve`,
ack → `timeout_unack`) and stages exactly as the request path does. The coexistence contract
applies unchanged: the chain for the alert must satisfy R26.

### 4.3 Status-change-only path (model actions)
Severity-change reopen reaches plugins only through `status_change` during `POST /alert`
ingestion, with no `take_action`. A new pre-step in `status_change`: when the route is
`POST /alert` (ingestion), the previous status was `ack`, the new status is `open`, the
alert is Zabbix-owned and `mirror_model_transitions` is true, stage a `model_unack` intent
and sign it in the same hook (there is no later hook). This path must not raise: an
ingestion failure caused by the plugin would block monitoring. Any staging error is
recorded as `skipped: model_capture_failed` and the alert proceeds.

## 5. Ledger changes (migration 013)

- `actions` gains `kind text NOT NULL DEFAULT 'ack'`, `zabbix_action integer NOT NULL
  DEFAULT 2`, `params jsonb NOT NULL DEFAULT '{}'`, `actor_type text NOT NULL DEFAULT
  'human'`. Existing rows are valid under the defaults.
- Every check of the form `i->>'action'<>'ack'` in 003, 006, 008, 009, 010, 011, 012 is
  replaced by a kind-aware predicate `i->>'kind' NOT IN (SELECT kind FROM action_kinds)`
  through a new `action_kinds` reference table (kind, zabbix_action, requires_message,
  requires_suppress_until, human_only).
- Effect coalescing: today effects coalesce per (instance, event). With several kinds, the
  coalescing key becomes (instance, event, kind), and ordering within an event is by
  `issued_at`; a later `unack` does not cancel an earlier undelivered `ack`, both deliver in
  order (Zabbix applies them in sequence; the ledger records both outcomes).
- Outcome states gain `ok_noop` (Zabbix skipped a repeat) and `refused` with a reason enum
  (`manual_close_disabled`, `insufficient_rights`, `event_resolved`, `not_problem_event`).
- Reconciler: per kind, the drift sweep compares the event's `acknowledged`, `suppressed`
  and `r_eventid` fields with the ledger's last settled outcome.

## 6. RPC changes

- `ZabbixClient.call` write scope: allow `action` in the set of bitmasks derived from
  `action_kinds` (2, 6, 16, 1, 5, 32, 64, 4), still exactly one `eventids` entry, and
  `message`/`suppress_until` present exactly when the kind requires them.
- `verify_event` per kind: `unack` requires `acknowledged == 1`; `unshelve` requires
  `suppressed == 1`; `close` requires `r_eventid == 0`; all require the event to belong to the
  bound trigger and cohort hosts (unchanged).
- Messages: neutral team text + public reference as today. Notes: by default the message
  contains the reference and "Note added in Alerta" only; a cohort option
  `forward_note_text` (default false) includes the note text, because note text is operator
  input and may contain customer data.
- Every non-200 write response remains uncertain (F2 fix, 0.1.0b1).

## 7. Projection

Attribute family grows from `zabbix_ack_sync_*` to `zabbix_sync_<kind>_*` with the same
fields (`intent`, `reason`, `config_revision`, `local_at`, result). The reader query guard
and serializer filters cover the whole `zabbix_sync_` prefix. The release-one names stay as
aliases for `ack` so existing dashboards keep working.

## 8. Lab and evidence

Deployment-lab cases per kind: human unack; note → message visible in `event.get
acknowledges[]`; close with manual close enabled → resolved; close with manual close disabled
→ `refused: manual_close_disabled`; shelve → `suppressed == 1` with the expected
`suppress_until`; unshelve → `suppressed == 0`; housekeeping timeout on a shelved alert →
unsuppress; repeat ack → `ok_noop`; bulk status change → recorded skipped, no Zabbix call.
Native-lab SQL cases for migration 013 and the kind-aware fences. Coexistence profile:
a Voalack-owned alert receiving each kind → Zabalack makes no write and raises nothing.

## 9. Open questions for the owner (asked after review)

1. `expired`: no Zabbix write (default) or close the problem if manual close is enabled?
2. `forward_note_text` default: false (reference only) or true (operator text to Zabbix)?
3. `mirror_system_transitions` / `mirror_model_transitions` defaults per customer.

## 10. Prerequisites and ordering

1. Ownership binding at ingestion (coexistence F1 / Astra): required before adding kinds,
   because every new kind widens the attack surface of attribute-based ownership.
2. alerta-ack-core shared install (3b) for the hooks this design adds (`take_note`,
   housekeeping-path `take_action`, ingestion-path `status_change`).
3. Then migration 013, RPC, plugin, projection, lab cases, release 0.2.0.

No part of this amendment applies to 0.1.0b1; the tag proposal and frozen artifacts are
unaffected.
