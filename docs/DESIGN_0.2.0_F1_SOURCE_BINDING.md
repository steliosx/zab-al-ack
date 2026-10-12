# Design — F1 source binding at ingestion (0.2.0 prerequisite 1)

Status: DRAFT r1 for review (Codex; Voalack-side reviewer GPT-6.1-Sol). Design only; no code.
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

## 2. Facts relied on (pinned Alerta 9.1.0)

- `last_receive_id` and `last_receive_time` are written only by ingestion: `create_alert`
  (insert), `dedup_alert` and `correlate_alert` (`database/backends/postgres/base.py:226, 249,
  268-273`). `update_attributes` (`:339-349`) and every action/status/note path leave them
  unchanged.
- `process_alert` (`utils/api.py`) runs `pre_receive`, then commits the create/dedupe/correlate,
  then calls `post_receive(alert)` with the alert object **returned by that statement**
  (`Alert.from_db(... RETURNING ...)`). `process_alert` is called by `POST /alert`
  (`views/alerts.py`) and by the webhook receivers (`webhooks/custom.py`, `webhooks/grafana.py`).
  Every such ingestion goes through the same `pre_receive` gate, so this design applies wherever
  `process_alert` runs. It never runs on attribute edits, actions or notes.
- The Zabalack owner role holds `SELECT(id, customer, status, attributes, update_time)` and
  `UPDATE(attributes)` on `public.alerts` (`sql/install/00_roles.sql:20`), not `last_receive_id`.
  This design adds **no** `public.alerts` grant and takes **no** lock on `public.alerts`.
- The ingesting principal is constrained by `ZabbixNormaliser.pre_receive` only when the incoming
  alert declares `source_system = "zabbix"` (mapped ingestion key, `user_id is None`, no admin
  scopes, customer and instance match, all source keys present). An incoming alert without that
  declaration passes the normaliser unvalidated (`normalizer.py:59-60`), and Alerta may still
  deduplicate or correlate it onto an existing Zabbix alert, which moves `last_receive_id`.
- If any `post_receive` returns an alert, `process_alert` writes the whole in-memory attribute
  map back with `update_attributes` (`utils/api.py:88-90`). A binding step must therefore return
  `None` and write nothing to `public.alerts`.

## 3. Design

### 3.1 Binding record

New tables `zabalack.source_bindings` and `zabalack.binding_events` (migration 013a,
forward-only). `source_bindings`:

| Column | Meaning |
|---|---|
| `alert_id text NOT NULL` | Alerta alert id |
| `last_receive_id text NOT NULL` | the ingestion that produced this binding; `PRIMARY KEY (alert_id, last_receive_id)` |
| `customer text NOT NULL` | from the ingested alert |
| `zabbix_instance`, `zabbix_event_id`, `zabbix_trigger_id` | normalised source keys from the ingested alert |
| `bound_at timestamptz NOT NULL` | `clock_timestamp()` |

No runtime role can write it directly. Only `zabalack.bind_source(...)`, `SECURITY DEFINER`,
`search_path = pg_catalog, zabalack`, granted to the Alerta application role exactly as
`stage_generation` is today (`sql/install/12_application_grant.sql` pattern), can insert into it.
Rows are **insert-only**: each authenticated ingestion inserts its own row
(`ON CONFLICT DO NOTHING`), and no row is ever updated. Retention: rows whose
`last_receive_id` no longer matches any capture-eligible alert state may be removed by the
existing retirement job after the capture grace period; the exact rule is an implementation
review item.

### 3.2 Writing the binding (`post_receive`)

Hand-off from `pre_receive`: after a successful authenticated Zabbix ingestion check,
`ZabbixNormaliser.pre_receive` stores the validated snapshot and customer in a request-scoped
`flask.g` entry keyed by (customer, environment, resource, event). Nothing else writes that
entry, and an ingestion that did not pass the authenticated Zabbix path leaves no entry.

`ZabbixNormaliser.post_receive` (today a no-op) looks up that entry with the returned alert's
key. It calls `bind_source(alert.id, alert.last_receive_id, alert.customer, instance, event_id,
trigger_id)` only when the entry exists **and** the source keys on the returned alert object equal
the validated snapshot. It uses the returned object, never a re-read, so an attribute edit
committed after ingestion cannot influence the binding. It always returns `None`.

`bind_source` does not read `public.alerts`. Because every check in 3.3 looks a binding up by the
exact pair (`alert id`, current `last_receive_id`), only the row for the alert's current
ingestion can ever match. An out-of-order or late `post_receive` inserts a row for an older
`last_receive_id`, which no later check selects. No stale logic and no lock are needed.

An ingestion that moves `last_receive_id` without an authenticated Zabbix hand-off (for example a
non-Zabbix-declared alert that deduplicates onto a Zabbix alert) writes no binding. No row then
exists for the alert's current `last_receive_id`, so every check in 3.3 fails closed
until the next authenticated ingestion. This is a fail-closed denial path only for principals that
can already post alerts for that customer.

Failure handling: `post_receive` never raises into ingestion and writes nothing to
`public.alerts`. A failed, stale or unhanded bind is recorded in a Zabalack-owned
`zabalack.binding_events` row (alert id, `last_receive_id`, reason, time) through the same
function, and the alert simply has no current binding.

### 3.3 Enforcing the binding

Two checks, both fail closed:

1. **Plugin, before staging** (`take_action`, every kind): read the binding for the alert
   (read-only function `zabalack.read_binding(alert_id, last_receive_id)`, `SECURITY DEFINER`, granted to the
   application role). If there
   is no binding row for (`alert.id`, `alert.last_receive_id`), project
   `skipped: source_binding_missing`; if the attribute snapshot differs from that row, project
   `skipped: source_binding_mismatch`. Either way stage nothing. The intent's `source`
   is taken **from the binding**, never from attributes.
2. **Database, at capture** (the capture function called by the existing `zabalack_capture`
   trigger): the intent's `source` must equal the `source_bindings` row whose `last_receive_id`
   equals `NEW.last_receive_id` for `NEW.id`. Otherwise the capture is recorded exactly as today's
   `binding_mismatch` path records it (`failed`, outbox reason `failed_intent_invalid:`), with
   `diagnostic_reason = 'source_binding_missing'` or `'source_binding_mismatch'`, and records no
   generation. This makes the check
   independent of the plugin.

The `zabalack_capture` trigger **definition** (name, timing, `WHEN` clause) is unchanged by 013a;
only the function body it calls changes. The exact rendered definitions are still sent to the
Voalack session before implementation, as agreed.

### 3.4 Ownership

With 3.3, ownership of an alert for Zabalack purposes is the existence of a current binding
written by an authenticated ingestion, not the editable `source_system` attribute. This is the
server-controlled per-alert class the coexistence review asked for. The single-owner rule across
providers (core decision before enrolment, §10 of the parent design) remains the ack-core 3b
design; this binding is Zabalack's input to it.

## 4. Behaviour changes and migration

- Applies to the existing `ack` kind too, closing F1 for release-one behaviour.
- Alerts ingested before 013a have no binding. They are bound by their next ingestion (dedupe or
  correlate). Until then an acknowledgement is projected `skipped: source_binding_missing` and
  nothing is delivered. This is an owner-visible behaviour change, not only a release-notes line:
  a Zabbix problem that fired once and stays open may never be re-sent, so its alert stays
  un-acknowledgeable through Zabalack until it resolves and recurs. This design proposes **no
  backfill**, because a backfill would read the same mutable attributes F1 distrusts. Whether to
  accept this window is owner question 4 in §6.
- Migration 013a is forward-only; shipped migration bytes stay immutable; rollback preserves
  `source_bindings` rows and pending evidence; installer, worker, reader and application grants
  stay separate. A capability row gates the new capture check so a worker or plugin that predates
  013a fails closed rather than mixing models.

## 5. Evidence plan (proposed, not run)

Unit: `post_receive` binds from the returned object; failed and unhanded binds; `take_action` skip
reasons for missing and mismatched bindings; intent source comes from the binding.
SQL (native lab): `bind_source` insert, duplicate insert no-op, out-of-order calls never matching
the current ingestion, no `public.alerts` read or lock by `bind_source`,
grant denial for every other role; capture-function rejection of a mismatched intent.
Deployment lab: ingest, edit attributes via `PUT /attributes`, acknowledge → skipped mismatch and no
Zabbix call; ingest, re-ingest with a new event id, acknowledge → delivered to the new event;
pre-013a alert acknowledged → skipped missing, then bound by the next ingestion.
Coexistence: a Voalack-class alert never receives a Zabalack binding and is passed through.

## 6. Open points for review

1. `post_receive` commits on Alerta's request connection after the ingestion commit. This sits
   inside the ack-core 3b transaction-model question raised by the Voalack-side review; this
   design does not resolve it. `bind_source` commits on that connection exactly as
   `stage_generation` does today.
3. The `zabbix_event_id` length rule: capture truncates the event id to 20 characters
   (`left(..., 20)`); `bind_source` must apply the same normalisation so the comparison in 3.3.2
   is exact.
2. Whether the plugin-side check (3.3.1) should be advisory only, with the database check as the
   sole authority. Proposed: keep both, so the operator sees the reason without a capture row.
4. **Owner question (queued, not yet asked):** after 013a, existing open Zabbix alerts cannot be
   acknowledged through Zabalack until Zabbix re-sends them. Accept that window, or ask for a
   bounded one-time backfill that needs its own trust argument (for example, binding only alerts
   whose attributes are unchanged since their last ingestion, which needs evidence Zabalack does
   not keep today)?
