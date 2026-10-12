# Design — F1 source binding at ingestion (0.2.0 prerequisite 1)

Status: r4.1. Codex gave AGREEMENT_DESIGN_ONLY on the exact r4 bytes (blob `59b07603`, message
`20261012T190625Z-codex-omarchy-57446de1`); r4.1 applies only its nonblocking wording notes
(§5, §6 item 4). Design agreement is not implementation, qualification or release approval,
and does not accept any risk: the freshness residual (§6 item 4) and the no-binding window
(§6 item 3) are OPEN owner/security decisions, and F1 is not closed. Voalack-side reviewer:
Astra. Design only; no code.
r4 answers Codex CHANGES_REQUIRED_DESIGN_ONLY on r3 (`05d4ded7`, message
`20261012T184943Z-codex-omarchy-11d057f9`): the freshness contract (F1-R1, still open) and the
SQL history field name; the parent editorial notes are applied (parent r10 continues). Codex
closed F1-R3 (on r2) and F1-R2-r2 (on r3) at design level. r3 answered r2 (`49425c07`, message
`20261012T182349Z-codex-omarchy-7beacad5`); r2 answered r1 (`ba3ad9d8`, message
`20261012T172209Z-codex-omarchy-877affd9`).
Parent design: `docs/DESIGN_0.2.0_ACTION_SYNC.md` §10, prerequisite 1. Owner direction of
12 October 2026: 0.2.0 work may proceed before the 0.1.0b1 tag; the frozen 0.1.0b1 candidate
(`f85212e2`) is not affected by this design.

## 1. Problem

Internal adversarial review of 0.1.0b1 (finding F1) and the coexistence review both found that
Zabalack takes the Zabbix source of an acknowledgement from the alert's **mutable attributes** at
`take_action` time (`plugins.py`, `make_snapshot(alert.attributes)`). Ingestion is authenticated
(the normaliser requires a mapped ingestion key, matching customer and instance), but any later
principal with write access to the alert can change `zabbix_event_id` / `zabbix_trigger_id`
through `PUT /alert/<id>/attributes` and then acknowledge. Zabalack would then deliver a validly
signed acknowledgement to a Zabbix event the alert never represented, within the cohort's hosts.

## 2. Facts relied on (pinned Alerta 9.1.0, upstream `f4312727`)

- **The receive id is caller-chosen (r1 was wrong to rely on it alone).** `Alert.parse` accepts
  the JSON `id` (`models/alert.py:96-98`), the constructor keeps it (`:49`), and create, dedupe
  and correlate all set `last_receive_id = self.id` (`:297`, `:355`, `:396`). Dedupe and correlate
  select the stored row by natural identity and customer, not by that id
  (`database/backends/postgres/base.py:229-240`, `:252-262`). `lastReceiveId` is serialised to
  every reader (`models/alert.py:146`). A writer can therefore replay a known receive id.
- **The receive time is chosen by the application server, not the caller, and it is not a
  database generation.** `deduplicate`, `update` (correlate) and `create` set
  `last_receive_time = now` with `now = datetime.utcnow()` sampled in the method **before** the
  SQL runs (`models/alert.py:286-299`, `:339-357`, `:383-398`); the SQL stores the supplied
  value (`base.py:226`, `:250`, `:274`). `Alert.parse` accepts no receive time (it parses only
  `createTime` among timestamps). `update_attributes` (`:339-349`) and every action, status and
  note path leave it unchanged. Nothing in this source makes two samples distinct: the same
  value can recur after a clock step or at the clock's resolution.
- `process_alert` (`utils/api.py`) runs `pre_receive`, then commits the create/dedupe/correlate,
  then calls `post_receive(alert)` with the alert object **returned by that statement**. If
  `alert.is_suppressed`, it skips both hook loops (`:35-38`, `:71`).
- `POST /alert` (`views/alerts.py:36-56`, endpoint `api.receive`) calls `process_alert` exactly
  once per request. The custom webhook accepts a list and calls `process_alert` repeatedly within
  one request (`webhooks/custom.py:42-83`); `flask.g` survives that whole loop. The endpoint
  name follows from the blueprint `api = Blueprint('api', __name__)` (`views/__init__.py:7`).
- Zabalack's Zabbix ingestion uses `POST /alert`: the deployment lab's Zabbix producer posts to
  `/alert` (`lab/deployment_e2e_zabbix.py:25`) and the runtime lab posts producer alerts there
  (`lab/deployment_e2e_runtime.py:326`).
- Pre and post plugin sets come from separate effective routing calls (`utils/api.py:33`, `:66`).
- If any `post_receive` returns an alert, `process_alert` writes the whole in-memory attribute map
  back with `update_attributes` (`utils/api.py:88-90`). Zabalack's binding step returns `None`.
  Another plugin's post hook may still cause that write-back; it cannot change the receive id or
  receive time.
- The ingesting principal is constrained by `ZabbixNormaliser.pre_receive` only when the incoming
  alert declares `source_system = "zabbix"`. An undeclared alert passes the normaliser unvalidated
  (`normalizer.py:59-60`) and can still deduplicate or correlate onto an existing Zabbix alert.
- The Zabalack owner role holds `SELECT(id, customer, status, attributes, update_time)` and
  `UPDATE(attributes)` on `public.alerts` (`sql/install/00_roles.sql:20`). This design adds **no**
  `public.alerts` grant and takes **no** read or lock on `public.alerts` outside trigger context.

## 3. Design

### 3.1 Ingestion epoch

An ingestion epoch is the pair (`last_receive_id`, `last_receive_time`) of the stored alert row.
Only ingestion writes it; the caller cannot choose the time. A replayed receive id **normally**
yields a new epoch, but this design does **not** assume that it always does (§2).

**Selection rule (latest recorded binding only).** Each binding row carries `bind_seq`, a value
from a Zabalack-owned sequence assigned inside `bind_source` (3.2). A binding row is usable for
an alert only when all of these hold:
1. it matches the row's current epoch exactly (`alert_id`, `last_receive_id`,
   `last_receive_time`);
2. its `state` is `bound`;
3. its `bind_seq` is greater than that of every other `source_bindings` row for the same alert,
   `bound` or `poisoned` (it is the latest **recorded** binding).

r3 ranked rows by `last_receive_time`. That follows the application clock, not commit order, so
a backward clock step could leave an older source ranked first (Codex trace on r3: X at t20,
then authenticated Y at t10, then a replay of X's epoch). `bind_seq` ranks by the order in which
bindings were recorded instead, so in that trace Y outranks X and the replay fails
`source_binding_not_latest`.

**Guarantee (weaker than "latest authenticated ingestion"; stated exactly).** An action is
delivered only to a source that an authenticated `POST /alert` ingestion of that alert recorded,
only when that binding is the latest **recorded** binding of the alert, and only while the
alert row carries exactly that binding's epoch. Attribute edits alone can never select a source.
Undeclared or unauthenticated ingestions with a fresh epoch match no row
(`source_binding_missing`); a replay of an epoch whose binding is not the latest recorded one
fails (`source_binding_not_latest`).

**Residual (open freshness weakening; needs owner/security admission, not decided here).**
The latest recorded binding is not always the latest authenticated ingestion in commit order.
A historical source X can be selected again when all of the following happen:
1. a later authenticated ingestion of the same alert (source Y) commits, and then either
   (a) Y records **no** binding, because its bind failed or its `post_receive` was routed away,
   or (b) Y records a binding, but a late bind of X's earlier ingestion (a concurrent
   out-of-order pair) is recorded after it and outranks it; and
2. an undeclared ingestion then reproduces X's epoch exactly: X's exposed receive id **and** the
   same microsecond application time value. This needs the application clock to produce a past
   microsecond value again; an application clock that never repeats or goes back excludes this
   step, and therefore the residual, entirely; and
3. the alert's attributes match X (kept by the attribute merge or restored by an edit).
No ingestion record exists for step 1 inside this design's boundary: the binding is written after
the ingestion commits, and this design adds no trigger or grant on `public.alerts`. A complete
contract (every committed ingestion recorded in commit order) needs the atomic
ingestion-and-capture path of ack-core 3b, the same dependency as `model_unack` (3.5), or a
broader trigger/grant boundary, which this design does not take. Until one exists, this
residual is an open owner/security decision (§6 item 4). Design review does not admit it.

Same-epoch conflict from two **authenticated** binds is handled by poison (3.2), never by
overwrite.

### 3.2 Binding records

New tables in migration 013a (forward-only):

`zabalack.source_bindings`, primary key (`alert_id`, `last_receive_id`, `last_receive_time`):

| Column | Meaning |
|---|---|
| `alert_id text NOT NULL` | Alerta alert id |
| `last_receive_id text NOT NULL` | receive id of the bound ingestion |
| `last_receive_time timestamp NOT NULL` | server receive time of the bound ingestion, same type as `public.alerts.last_receive_time` |
| `customer text NOT NULL` | from the ingested alert |
| `zabbix_instance`, `zabbix_event_id`, `zabbix_trigger_id` | validated source keys; `zabbix_event_id` normalised exactly as `capture_ack()` does (`left(..., 20)`) |
| `state text NOT NULL` | `bound` or `poisoned` |
| `bind_seq bigint NOT NULL UNIQUE` | next value of the Zabalack-owned sequence `zabalack.binding_seq`, taken inside `bind_source`; never supplied by a caller |
| `bound_at timestamptz NOT NULL` | `clock_timestamp()` |

A row's key and source values are never changed after insert. A repeat insert with the same key
and the same values is an idempotent no-op. A repeat with the same key and **different** values
keeps the original values and sets `state` to `poisoned`; a poisoned epoch fails every check in
3.4. The single permitted update is `state: bound → poisoned`; nothing ever sets a row back to
`bound` or replaces its values.

`zabalack.binding_events`: alert id, epoch, a reason code from a fixed set (`bound`,
`idempotent_repeat`, `conflict_poisoned`), and time. It holds no source text, no SQL and no
attribute values.

Retention never removes the latest `source_bindings` row of an alert (by `bind_seq`), and never
removes a `poisoned` row whose `bind_seq` is greater than that of any `bound` row it outranks.

Only `zabalack.bind_source(...)` and `zabalack.read_binding(alert_id, last_receive_id,
last_receive_time)` reach these tables. Both are `SECURITY DEFINER`, `search_path = pg_catalog,
zabalack`, with `PUBLIC` revoked, granted to the Alerta application role only (3.6). No role has
direct table grants. `bind_source` does not read `public.alerts`: the epoch it receives is checked
at use (3.4), not at bind time. A bind whose epoch is no longer current creates a row that
never matches the current epoch; if it outranks the current epoch's binding, the current
epoch fails `source_binding_not_latest` until the next authenticated ingestion (fail closed).

### 3.3 Writing the binding (single-use hand-off on `POST /alert` only)

Hand-off slot: one request-scoped `flask.g` entry owned by Zabalack.

1. `ZabbixNormaliser.pre_receive` **clears the slot as its first statement**, on every call and
   every branch (undeclared, rejected, bypassed, invalid or valid).
2. As the **last** step of the successful authenticated Zabbix path, it fills the slot with: the
   request endpoint, the incoming `alert.id` (the future `last_receive_id`), customer, environment,
   resource, event and the validated source snapshot. Any exception before that step leaves the
   slot empty.
3. `ZabbixNormaliser.post_receive` **pops the slot as its first statement** (consume once), then
   binds only when all of these hold:
   - the request endpoint is `api.receive` (`POST /alert`), so the request contains exactly one
     `process_alert` call;
   - the returned alert's `last_receive_id` equals the slot's incoming id;
   - customer, environment, resource and event equal the slot's values;
   - the returned alert's source keys equal the slot's validated snapshot.
   It then calls `bind_source(alert.id, alert.last_receive_id, alert.last_receive_time, ...)` with
   values from the **returned** object, never a re-read, and returns `None`.

Consequences:
- Webhook ingestion (custom, Grafana and any other receiver) never binds. A Zabbix alert ingested
  only through a webhook stays unbound and fails closed.
- Suppressed alerts skip both hooks, so they write no binding.
- If effective routing omits the normaliser from `pre_receive`, the slot is empty and nothing
  binds. If it omits the normaliser from `post_receive`, the slot is discarded with the request.
- A failure between the hooks leaves a slot that the next call clears or the request discards.

Unbound ingestion: an ingestion that moves the epoch without an authenticated hand-off, including
a replay of a known receive id with no source declaration, finds no usable row for its epoch,
except in the residual case of 3.1. In every other case each check
in 3.4 fails closed until the next authenticated `POST /alert` ingestion. This is a fail-closed
denial path only for principals that can already post alerts for that customer.

Failure handling: `post_receive` never raises into ingestion and never writes `public.alerts`.
`bind_source` and its `binding_events` row are one transaction on the request connection. If the
call, connection or commit fails, it rolls back and nothing durable is written. A bounded
application log line records only a reason code from a fixed set (`no_handoff`,
`endpoint_not_receive`, `receipt_mismatch`, `natural_key_mismatch`, `snapshot_mismatch`,
`database_unavailable`) and the alert id, with no SQL text and no source values. A failed or
conflicting call never relabels an existing binding.

### 3.4 Enforcing the binding

Two checks, both fail closed:

1. **Plugin, before staging** (`take_action`, every admitted kind): call `read_binding(alert.id,
   alert.last_receive_id, alert.last_receive_time)`, which applies the selection rule of 3.1 and
   returns one outcome. No row → `skipped: source_binding_missing`; `poisoned` →
   `skipped: source_binding_conflict`; not the latest recorded binding →
   `skipped: source_binding_not_latest`; attribute snapshot differs from the row →
   `skipped: source_binding_mismatch`. Stage nothing in those cases. The intent's `source` is
   taken **from the binding**, never from attributes.
2. **Database, at capture** (body of `capture_ack()`, called by the existing `zabalack_capture`
   trigger): the intent's `source` must equal the row that the selection rule of 3.1 selects for
   (`NEW.id`, `NEW.last_receive_id`, `NEW.last_receive_time`). Otherwise the capture is recorded
   exactly as today's `binding_mismatch` path records it (`failed`, outbox reason
   `failed_intent_invalid:`), with `diagnostic_reason` `source_binding_missing`,
   `source_binding_conflict`, `source_binding_not_latest` or `source_binding_mismatch`, and records no generation. This check does not depend on the plugin.

The `zabalack_capture` trigger **definition** (name, timing, `WHEN` clause) is unchanged by 013a;
only the function body changes. The exact rendered definitions are still sent to the Voalack
session before implementation, as agreed.

### 3.5 Scope: later request actions only; ingestion-time capture stays unadmitted (F1-R2)

The binding for an epoch is written after the ingestion statement commits. A capture that fires
**inside** the ingestion statement therefore never finds a binding for the new epoch, and the
pre-statement alert carries the old source. This affects `model_unack` (parent design §4.3:
severity-rise correlate from `ack` to `open`), whose capture fires in the correlate statement.

This design therefore limits prerequisite 1 to captures that fire after ingestion has
committed: human actions through the action endpoints and housekeeping (`ack`, `unack`, `close`,
ack-timeout `unack`, and the record-only suppression kinds). `model_unack` stays unadmitted
until a separately reviewed atomic ingestion-and-capture path exists, which belongs to the
ack-core 3b transaction-model work. Until then it has exactly one outcome, defined here.

**Diagnostic-only `model_unack` branch (0.2.0).**
- Plugin: on the correlate path, `status_change` stages **nothing** for an `ack` → `open`
  transition: no intent, no signature, no marker and no generation, whatever the value of
  `mirror_model_transitions` (true, false, absent or invalid).
- Recognition: the shape of alarm-model ACK-4 on the stored row: `OLD.status = 'ack'`,
  `NEW.status = 'open'`, and `(NEW.history[1]).type = 'severity'`. In SQL the history composite
  field is `type` (Alerta `sql/schema.sql:5-15`); `change_type` is only the Python `History`
  argument that fills it (`ChangeType.severity`, `models/enums.py:161`; written by correlate,
  `models/alert.py:365-375`). There is no SQL field `history.change_type`. Recognition does not read any binding, attribute-supplied intent,
  marker or provenance element.
- Precedence, in the capture function: (1) the existing capture-bypass branch keeps first
  place, unchanged (`skipped_capture_bypass`); (2) then this branch; (3) only after it, every
  intent, marker, provenance and binding check. A row this branch recognises is therefore never
  `failed` and never evaluates a binding, so accidental same-epoch selection cannot make it
  executable.
- Record: capture `state = 'skipped'`, `diagnostic_reason =
  'source_binding_unavailable_at_ingestion'`, `intent` and `marker` NULL, no generation; outbox
  `state = 'skipped'` with the same reason. Any staged intent attribute present on the row is
  not copied; only a boolean `staged_data_present` is recorded. The bounded source snapshot is
  stored as diagnostic data only, never as an intent.
- No component admits it: the verifier and the worker never take a `skipped` capture, so there
  is no Zabbix call for any mirror value or binding state.
- Component and timing: this branch belongs to the 0.2.0 capture function and trigger of
  migration 013 (parent design §5). Before 013 the existing `zabalack_capture` `WHEN` clause
  (`status = 'ack'`, `ack` history row, marker) never fires on this transition, so nothing is
  captured. 013a itself does not change the trigger definition (3.4); the 013 trigger definition
  is the parent's and goes to the Voalack session before implementation. Owner decision §9 Q5 (mirror model
transitions by default) is unchanged as policy, but cannot take effect for `model_unack` before
that path exists. This is reported to the owner as a consequence, not a new decision. The parent
design is amended in the same commit (parent r10: §2 reopen row, §3 mirror-flag paragraph, §4
table and §4.3, §8 lab expectations, §9 Q5 note, §10 item 1), so the two documents agree.

### 3.6 Migration, capability and grants

013a is forward-only; shipped migration bytes stay immutable. It:
- advances the capability/version row, so a plugin or worker that predates 013a fails closed
  instead of mixing models, and a rollback to a pre-013a component is declared incompatible
  (bindings and pending evidence are preserved, not dropped);
- adds `bind_source` and `read_binding` to the exact routine ACL allowlists, to `capture_health`,
  to the application-grant and reseal effective-right checks (`12_application_grant.sql`,
  `13_reseal.sql`) and to the expected routine-body digests; the A5 full grant-map assertions
  are updated, not left as they are (today `12_application_grant.sql` grants only
  `stage_generation` and refuses other reachable functions);
- keeps no direct table grants, controlled `search_path` and `PUBLIC` revocation on every new
  routine; installer, worker, reader and application grants stay separate.

### 3.7 Ownership

With 3.4, ownership of an alert for Zabalack purposes is a `bound` row that matches the alert's
exact current epoch and is the alert's latest recorded binding (3.1), written from an
authenticated `POST /alert` ingestion, not the editable
`source_system` attribute. This is the server-controlled per-alert class the coexistence review
asked for. The single-owner rule across providers remains the ack-core 3b design; this binding is
Zabalack's input to it.

## 4. Behaviour changes and migration

- Applies to the existing `ack` kind too. It closes the attribute-edit path of F1 for
  release-one behaviour; the freshness residual of 3.1 stays an open owner/security decision
  (§6 item 4), so this design does not claim F1 complete.
- Operator requirement: Zabbix must ingest through `POST /alert` with the mapped ingestion key,
  as both labs do. Ingestion through any webhook receiver yields no binding.
- Alerts ingested before 013a, and alerts ingested only through webhooks, have no binding. An
  acknowledgement is projected `skipped: source_binding_missing` and nothing is delivered until
  the next authenticated `POST /alert` ingestion. A Zabbix problem that fired once and stays open
  may never be re-sent, so its alert stays un-acknowledgeable through Zabalack until it resolves
  and recurs. This design proposes **no backfill**, because a backfill would read the same
  mutable attributes F1 distrusts. Accepting this window is an **open owner decision** (§6 item 3);
  design review is not acceptance of it.

## 5. Evidence plan (proposed, NOT_RUN)

Unit: slot cleared on every `pre_receive` branch; slot filled only on success; slot popped once;
no bind on a non-`api.receive` endpoint; receipt, natural-key and snapshot mismatch; webhook list
with repeated key (valid then undeclared, valid then invalid, valid then suppressed); failure
between hooks; rerouting that omits either hook; a second post hook returning an alert; intent
source from the binding; `post_receive` returns `None`.

SQL (native lab): insert; idempotent repeat; same-epoch conflict → `poisoned` and every check
fails; selection rule: latest recorded bound row usable, earlier bound row → `not_latest`, a
later poisoned row blocks an earlier bound row; `bind_seq` taken only inside `bind_source`;
retention keeps the latest row; forced equal timestamp with a reused receive id on undeclared
dedupe and on undeclared correlate, with the same and with different attributes; monotonic
X → Y → replay of X's epoch → `not_latest`; backward or skewed clock t20 → t10 → t20 with
different authenticated sources → `not_latest`; the residual trace (Y commits with a failed
bind, or with `post_receive` routed away, then an exact replay of X's epoch) demonstrated and
recorded as the open residual, not as a pass; both plugin and database checks; the chosen
contract is shown, collision probability is not estimated; an ordinary replay with a distinct
timestamp → `missing`; a noncurrent late bind is unusable unless its exact epoch recurs (the residual of 3.1);
concurrent late bind of an earlier ingestion X after a later Y, then an exact replay of X's
epoch, recorded as the open residual alongside the failed and routed-away Y cases; exact round trip of `last_receive_time`
(database → returned Python object → `bind_source` parameter → stored row → `read_binding`
parameter and `NEW.last_receive_time` in the trigger) with microsecond equality and no time-zone
conversion; `bind_source` reads and locks nothing
in `public.alerts`; grant denial for every other role; capability, ACL allowlist, reseal and
digest checks updated; capture-function rejection for missing, poisoned and mismatched bindings.

Deployment lab: ingest, edit attributes via `PUT /attributes`, acknowledge → skipped mismatch, no
Zabbix call; replay a known receive id with no source declaration, then restore the old
attributes, acknowledge → skipped missing, no Zabbix call; replay after an intervening
legitimate source change → skipped; ingest, re-ingest with a new event id, acknowledge →
delivered to the new event; webhook-only Zabbix alert → skipped missing; pre-013a alert →
skipped missing, then bound by the next `POST /alert` ingestion; severity-rise correlate from
`ack` → diagnostic capture `skipped: source_binding_unavailable_at_ingestion` for
`mirror_model_transitions` true, false, absent and invalid, and for missing, poisoned and
equal-old bindings and malformed staged data: exact capture and outbox outcome, zero executable
generations, zero verifier or worker admission, zero Zabbix calls.

Coexistence: a Voalack-class alert never receives a Zabalack binding and is passed through.

## 6. Open points

1. `bind_source` commits on Alerta's request connection after the ingestion commit, as
   `stage_generation` does today. Codex (r1 review, question c): not inherently unsafe for later
   actions when missing bindings skip safely. It remains part of the ack-core 3b transaction-model
   discussion, which also owns the atomic ingestion path that `model_unack` needs (3.5).
2. The plugin-side check (3.4.1) is kept alongside the database check, so the operator sees the
   reason without a capture row. The database check is the authority.
3. **Owner decision (open, not yet asked):** accept the no-binding window for existing open
   alerts and webhook-only alerts (§4), or ask for a bounded one-time backfill with its own trust
   argument.
4. **Owner/security decision (open, not yet asked): the freshness residual of 3.1.** A
   historical source can be selected again only after a later authenticated ingestion records no
   binding (failed or routed-away bind) or records one that a late bind of an earlier
   ingestion outranks (out-of-order concurrent binds), followed by an exact
   replay of the older epoch. That replay needs the application clock to produce a past
   microsecond value again; a clock that never repeats or goes back excludes it entirely. Every
   kind is affected, including release-one `ack` once 013a is installed. Options: admit this
   weaker freshness for 0.2.0 as a whole, or hold 0.2.0 implementation until ack-core 3b
   provides the atomic ingestion path. F1 is not complete for release purposes until this is
   decided.
