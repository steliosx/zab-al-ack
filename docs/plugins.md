# Alerta adapter and capture probe

See the [illustrated lab walkthrough](visual-guide.md) for selected CLI, action
and ledger output, with explicit evidence limits.

The package exposes `zabalack_zabbix_normalise` and `zabalack_zabbix_ack` in the standard
`alerta.plugins` entry-point group. Install the wheel with its `alerta` extra into
the existing reviewed Alerta environment; no import applies a migration or calls
Zabbix. Keep package/database external-write flags disabled during shadow work.

Set `ZABALACK_CONFIG_FILE` to the reviewed JSON configuration path in the existing
Alerta config. Preserve unrelated plugins and authentication settings. Place the
normalizer before any plugin that exports inbound attributes. Place the signer
last in every effective route, including dynamically routed cohorts. The routing
observer materializes exactly the list returned to the existing dispatcher; it
does not call routing rules again, add plugins, or change order.

Use the WSGI factory target `zabalack.plugins:create_app()` in the reviewed
existing launcher. If a custom factory is already required, retain it and call
`configure_app(app)` once after its logging configuration, before the first
request. See source amendment A1: upstream debug SQL and database error DETAIL
need the supplied privacy guards. Eligible acknowledgement fails with a propagated
AlertaException if that adapter is absent. Successful history retrieval still
contains committed proof. Any new logging handler or instrumentation installed
after the adapter requires review before activation.

Activation supports exactly installed `alerta-server==9.1.0` and the default
`ALARM_MODEL=ALERTA`. Another version/model yields local-only
`skipped_reference_version` until its compatibility tests and manifest are
explicitly extended. Full canonical UUID `/alert/<id>/action` URLs are required;
abbreviated IDs and native bulk/status routes remain local-only. Alerta's normal
repeat-Ack HTTP 409 is preserved without creating an intent or extending expiry.

Only authenticated request `g.user_id` plus `g.login` establishes user-authenticated
context. API-key owner labels and source-ingestion mappings never establish a
human actor. Non-admin customer scope is taken from `g.customers`; admin use
requires both the configured login exception and cohort `allow_admin`. Signed
intent is frozen after eligibility, while only the final effective status hook
appends the proof. Operator notes are limited to 8,192 UTF-8 bytes and cannot
contain the reserved marker prefix. The proof never enters an attribute or
`alert.text`. Unknown status signal receivers prevent activation pending review.

The normalizer strips reserved fields first and preserves event/resource and
correlation identity. Disabled configuration preserves existing local ingestion.
Enabled Zabbix ingestion requires its explicit customer/instance credential
mapping, non-admin scopes, and matching persisted top-level customer. Missing or
invalid source mappings remain visibly skipped; producer `attributes.customer`
does not authorize a customer. No ingestion hook calls a database or source API.

The dedicated Psycopg probe creates no connection before its first use in the
serving process. The process registry and per-probe locks are reset after fork;
acquisition, configuration replacement and refresh are bounded. A refresh sends
BEGIN READ ONLY, SET LOCAL statement_timeout='500ms', SELECT capture_health(),
then COMMIT as separate commands. Failures invalidate cached success and roll
back/discard the connection. Target, deployment, config revision, schema,
capture-disabled flag, trigger and function-readiness evidence are checked.
Success caching lasts at most five seconds. The probe's target does not prove
the separate Alerta connection uses that writer: alignment and an actual capture
canary remain deployment evidence.

Fleet bypass is operationally fenced: drain affected nodes, distribute and
confirm the bypass revision, then apply the separately privileged flag/physical
trigger procedure. A local bypass unit test does not establish fleet consistency
or remove the bounded cache window. Mixed rollout and native PgBouncer/failover
acceptance remain explicit deployment tasks.

Executed evidence separates actual Alerta dispatch/auth with mocked storage from
actual sequential Alerta/Psycopg2/capture persistence against the PostgreSQL18.3
WASM engine. The WASM adapter has one backend, multiplexes connections and does
not establish native role authentication, cancellation, PgBouncer pooling or HA.
The native PostgreSQL tests are runnable from the same suite on an isolated host;
unexecuted native tests remain skipped, never passed.
