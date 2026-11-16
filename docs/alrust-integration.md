# Zabalack on alrust: Zabalack's review (Z1, Z3, signing vectors)

Status: **review, 16 November 2026. Read only; nothing here ran against
alrust.** This page answers the three items that alrust's integration hook
contract assigns to Zabalack: `spec/integration-hooks.md`, sections 7 and 8, in
[`steliosx/alrust`](https://github.com/steliosx/alrust) at `main`
`b3df6928f29e1ca9958ea480c34008c461af54c6`. It is not a qualification. Zabalack
and alrust do not work together yet: alrust registers no Zabalack adapter, and
alrust has no human operators (owner question A1), so every acknowledgement
through alrust would be skipped today.

Sources: Zabalack at the commit that carries this page (parent `7f08123d`);
alrust at `b3df6928`. alrust's spec cites Zabalack at `eaf2acf0`. Four later
commits changed `src/zabalack/plugins.py`, so the current line numbers are
given below.

## 1. Can alrust start on a database with Zabalack installed?

**Probably yes (read, not run).**
- At start, alrust compares the `history` type and ten reference tables (columns,
  constraints, indexes, row security and policies, rules, inheritance) with
  the pinned reference (`crates/alrust-store-pg/src/catalog.rs`, `startup.rs`).
  Triggers on those tables are allowed and only logged. Grants and other
  schemas are not compared.
- On the reference tables, Zabalack adds only the `zabalack_capture` trigger on
  `public.alerts` (`sql/install/10_trigger.sql`) and column grants
  (`13_reseal.sql`). Everything else is in the `zabalack` schema.
- Both projects pin Alerta v9.1.0 (`f4312727`). Its `alerta/sql/schema.sql` is
  byte-identical in both (SHA-256 `ee1372e2…c702`; Zabalack's copy is
  `lab/upstream/alerta-9.1.0-schema.sql`).

## 2. Z3: the server identity check

- Zabalack has one server check: `src/zabalack/plugins.py:466-471` requires
  `alerta-server == 9.1.0` and `ALARM_MODEL == alerta`, otherwise it records
  `skipped_reference_version`. (The spec cites it as `plugins.py:421-428`.)
- That check is in the Python plugin that runs inside Alerta. With alrust's
  option A (a hook that calls Zabalack's SQL), this Python code does not run.
  No Zabalack SQL function and no Zabalack process checks the server version,
  and no Zabalack process calls the Alerta HTTP API.
- **So Z3 needs no Zabalack code change.** The identity gate moves into
  alrust's adapter: it accepts only an alrust build that the adapter was
  reviewed against, and otherwise records the same `skipped_reference_version`
  skip. The Alerta path keeps the existing check unchanged.
- Zabalack's real compatibility checks are structural and stay in force on
  alrust: the readiness seal and `zabalack.capture_health()`.

## 3. Z1: staging inside alrust's acknowledgement transaction

Under Alerta, the plugin calls `zabalack.stage_generation()` on Alerta's
connection and commits it at once (`plugins.py:314-333`). Under alrust, the same
call runs inside the acknowledgement's transaction, after alrust has locked
the alert row (`take_action`), and the capture trigger fires in alrust's own
`UPDATE`.

### F1. Lock order: a deadlock is possible (blocks the adapter)

- `zabalack.stage_generation()` takes the per-alert generation lock
  (`pg_advisory_xact_lock(hashtextextended('zabalack.generation:'||alert,0))`),
  then the alert row `FOR UPDATE` (`sql/migrations/008_generation_fence.sql:149-150`).
- The projection, `zabalack.project()`, locks its `zabalack.actions` row, then the
  generation lock, then the alert row (`008_generation_fence.sql:425-431`). Its
  comment states the rule: generation lock first, then the alert row, "so a
  staging and a projection of one alert serialise rather than deadlock".
- alrust locks the alert row first and calls `stage` after that. Its order is
  alert row, then generation lock: the reverse. Confirmed in alrust's code, not
  only its spec: `take_action_with` locks the row (`lock_alert`, `FOR UPDATE`,
  `crates/alrust-store-pg/src/lib.rs:548`) at `lib.rs:1264`, then calls `claims`
  (`1267`), `gate` (`1286`) and `stage` (`1293`).
- Both Zabalack bodies are current: no later migration redefines
  `stage_generation()` or `project()`.
- Result: an acknowledgement and a projection of the same alert can deadlock.
  This can happen, for example, when an alert is reopened and acknowledged
  again while the previous action's result is still being projected.
  PostgreSQL then cancels one of the two transactions: the acknowledgement
  fails (fail closed), or the projection fails and is retried. No data is
  corrupted, but the failure is visible to the operator.
- No other Zabalack path takes the generation lock. `retire_intents` locks only
  alert rows. The capture trigger (`015_source_binding.sql`) and
  `capture_health()` take no row or advisory locks.

**Recommended fix (Zabalack side):** a forward migration in which
`stage_generation()` and `project()` take the alert row before the generation
lock. Then:
- alrust: alert row, then generation lock;
- the Alerta path (staging in its own transaction): the same order;
- the projection: `actions` row, alert row, generation lock. The
  acknowledgement path never locks an `actions` row, so this cannot cycle.

The fix needs native PostgreSQL tests with two concurrent sessions. The
alternative is that alrust takes the generation lock before it locks the row,
through a Zabalack function. That ties alrust to a Zabalack lock detail, so
this page does not recommend it. **The adapter must not be registered before
F1 is fixed on one side.**

### F2. Health probe: run it before the row lock

The plugin's health check (`plugins.py:435`, called at `527`) uses a separate
autocommit connection with a 500 ms statement timeout (`src/zabalack/health.py`).
`capture_health()` takes no row locks, so this cannot deadlock. But in alrust's
phase order the gate runs after the row lock, so the probe would hold the alert
row for up to its timeout and use a second connection. Under Alerta the probe
runs before any lock. Recommendation: alrust runs the Zabalack health probe
(or reads its cached result) before it locks the alert row.

### F3. Rollback

Inside one transaction a failed acknowledgement rolls back the staged
generation, the attribute writes, the audit row and the capture together. Under
Alerta a staged generation could commit without its acknowledgement and wait
for `retire_intents`; under alrust that window does not exist. The only effect
that survives a rollback is the generation sequence value (`nextval`). A gap is
allowed: `generation_order_intact()` requires only that the sequence is at or
above the highest recorded value. Zabalack sends no `NOTIFY` and uses no
session-level advisory locks.

### F4. One `UPDATE` can carry the capture

`stage_generation()` writes only these attribute keys: it sets
`zabbix_ack_sync`, `zabbix_ack_sync_reason`, `zabbix_ack_sync_action_id` and
`zabbix_ack_sync_intent`, and it removes `zabbix_ack_sync_local_at`
(`008_generation_fence.sql:160-163`). It writes no history, tags or text. So
alrust's rule ("re-read the row's attributes after `stage`, then apply its own
changes") is enough. alrust's single `UPDATE` then carries the staged intent,
status `ack`, and a newest history entry of type `ack` whose text ends with the
marker. That meets the trigger's conditions and `capture_ack()`'s binding
checks in the same statement. The trigger also records the generation, but
with `ON CONFLICT (action_id) DO NOTHING` inside an exception block
(`015_source_binding.sql:294-299`), so the row that `stage_generation()` wrote
earlier in the same transaction does not make it fail.

### F5. Database roles

`sql/install/12_application_grant.sql` grants `EXECUTE` on `stage_generation()`,
`bind_source()` and `read_binding()` to one role, `:alert_app_role`, and
`capture_health()` requires that this role is the only non-owner grantee and
that no role is a member of it. On alrust that role is the role alrust connects
as. If alrust created the schema, that role also owns `public.alerts`, and the
alerts owner already holds other Zabalack grants. `docs/deployment.md`
(section 2) therefore prefers a separate login role; an alrust deployment
should use one too.

### What Z1 needs before it is complete

The F1 fix and its native tests; then, once alrust registers the adapter and
has human operators, Zabalack's capture tests run against alrust (spec
section 7, A1). That is Zabalack's CI work. It does not run before then.

## 4. Signing test vectors (X1)

`docs/signing-vectors-v1.json` holds seven signing vectors and six refusals,
generated from `src/zabalack/crypto.py` by `tools/signing_vectors.py`, with
public test keys. `tests/unit/test_signing_vectors.py` regenerates the file and
recomputes every HMAC. The cases target known differences between JSON
encoders: raw UTF-8 instead of `\u` escapes, characters outside the Basic
Multilingual Plane, escaping only `"` and `\`, sorting by code point, and the
exact 16,384-byte payload limit.

While generating them, Zabalack found and fixed one defect: an unpaired
surrogate in a signed string raised a raw `UnicodeEncodeError` instead of the
stable `invalid_string` reason. The plugin's HTTP result was already a 503.

Where the signing keys live on alrust (X1) is an owner decision. The vectors
do not depend on it.

## 5. Not covered here

- Owner decisions A1 to A3 (human operators, customer membership, API-key
  acknowledgements) and X1 (key placement).
- alrust's adapter code. It is alrust's work, from its spec.
- Any execution against alrust: NOT_RUN.
