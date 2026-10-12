# Design — F1 source binding at ingestion (0.2.0 prerequisite 1)

Status: DRAFT r2 for review (Codex; Voalack-side reviewer Astra). Design only; no code.
r2 answers Codex CHANGES_REQUIRED_DESIGN_ONLY on r1 (`ba3ad9d8`, message
`20261012T172209Z-codex-omarchy-877affd9`): F1-R1 (caller-chosen receive id), F1-R2 (ingestion
capture ordering), F1-R3 (hand-off lifecycle), and the implementation-review clarifications.
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
- **The receive time is server-assigned.** `deduplicate`, `update` (correlate) and `create` set
  `last_receive_time = now` with `now = datetime.utcnow()` taken in the method
  (`models/alert.py:286`, `:339`, `:383`; assignments at `:298`, `:356`, `:397`). `Alert.parse`
  accepts no receive time (it parses only `createTime` among timestamps). Only the three
  ingestion statements write `last_receive_time` (`base.py:226`, `:250`, `:274`);
  `update_attributes` (`:339-349`) and every action, status and note path leave it unchanged.
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
The receive time is set by the server inside the ingestion statement and is not writable by any
other path, so a replayed receive id always produces a **new** epoch. A binding names one epoch
and applies only while the row still carries exactly that epoch.

Residual assumption, stated for review: two ingestions of the same alert could share an epoch
only if they carried the same receive id and the same microsecond `utcnow()` value on the
application servers. Concurrent ingestions of one alert are serialised by the row update; a
collision needs equal clocks to the microsecond on two servers and a caller-chosen id equal to
the earlier one. The design treats a same-epoch conflict as poison (3.2), never as an overwrite.

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
| `bound_at timestamptz NOT NULL` | `clock_timestamp()` |

A row's key and source values are never changed after insert. A repeat insert with the same key
and the same values is an idempotent no-op. A repeat with the same key and **different** values
keeps the original values and sets `state` to `poisoned`; a poisoned epoch fails every check in
3.4. The single permitted update is `state: bound → poisoned`; nothing ever sets a row back to
`bound` or replaces its values.

`zabalack.binding_events`: alert id, epoch, a reason code from a fixed set (`bound`,
`idempotent_repeat`, `conflict_poisoned`), and time. It holds no source text, no SQL and no
attribute values.

Only `zabalack.bind_source(...)` and `zabalack.read_binding(alert_id, last_receive_id,
last_receive_time)` reach these tables. Both are `SECURITY DEFINER`, `search_path = pg_catalog,
zabalack`, with `PUBLIC` revoked, granted to the Alerta application role only (3.6). No role has
direct table grants. `bind_source` does not read `public.alerts`: the epoch it receives is checked
at use (3.4), not at bind time, so a late or out-of-order bind creates a row that no current
check selects.

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
a replay of a known receive id with no source declaration, leaves no row for the new epoch.
Every check in 3.4 then fails closed until the next authenticated `POST /alert` ingestion. This
is a fail-closed denial path only for principals that can already post alerts for that customer.

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
   alert.last_receive_id, alert.last_receive_time)`. No row → `skipped: source_binding_missing`;
   `poisoned` → `skipped: source_binding_conflict`; attribute snapshot differs from the row →
   `skipped: source_binding_mismatch`. Stage nothing in those cases. The intent's `source` is
   taken **from the binding**, never from attributes.
2. **Database, at capture** (body of `capture_ack()`, called by the existing `zabalack_capture`
   trigger): the intent's `source` must equal a `bound` row for (`NEW.id`,
   `NEW.last_receive_id`, `NEW.last_receive_time`). Otherwise the capture is recorded exactly as
   today's `binding_mismatch` path records it (`failed`, outbox reason `failed_intent_invalid:`),
   with `diagnostic_reason` `source_binding_missing`, `source_binding_conflict` or
   `source_binding_mismatch`, and records no generation. This check does not depend on the plugin.

The `zabalack_capture` trigger **definition** (name, timing, `WHEN` clause) is unchanged by 013a;
only the function body changes. The exact rendered definitions are still sent to the Voalack
session before implementation, as agreed.

### 3.5 Scope: later request actions only; ingestion-time capture stays unadmitted (F1-R2)

The binding for an epoch is written after the ingestion statement commits. A capture that fires
**inside** the ingestion statement therefore never finds a binding for the new epoch, and the
pre-statement alert carries the old source. This affects `model_unack` (parent design §4.3:
severity-rise correlate from `ack` to `open`), whose capture fires in the correlate statement.

r2 therefore limits prerequisite 1 to captures that fire after ingestion has committed: human
actions through the action endpoints and housekeeping (`ack`, `unack`, `close`, ack-timeout
`unack`, and the record-only suppression kinds). For `model_unack`, 0.2.0 records the capture
and settles it `skipped: source_binding_unavailable_at_ingestion`; it is never written to Zabbix.
It stays unadmitted until a separately reviewed atomic ingestion-and-capture path exists, which
belongs to the ack-core 3b transaction-model work. Owner decision §9 Q5 (mirror model
transitions by default) is unchanged as policy, but cannot take effect for `model_unack` before
that path exists. This is reported to the owner as a consequence, not a new decision. The parent
design's `model_unack` rows (§2 table and §4.3) will be amended in the pending parent docs change
(the one that also fixes the "DRAFT r8" header and the Appendix A section-4 pointer), so the two
documents agree once F1 is agreed.

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

With 3.4, ownership of an alert for Zabalack purposes is a current `bound` row for the alert's
exact epoch, written from an authenticated `POST /alert` ingestion, not the editable
`source_system` attribute. This is the server-controlled per-alert class the coexistence review
asked for. The single-owner rule across providers remains the ack-core 3b design; this binding is
Zabalack's input to it.

## 4. Behaviour changes and migration

- Applies to the existing `ack` kind too, closing F1 for release-one behaviour.
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
fails; out-of-order binds never match the current epoch; exact round trip of `last_receive_time`
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
`ack` → `model_unack` settles `skipped: source_binding_unavailable_at_ingestion`, no Zabbix call.

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
