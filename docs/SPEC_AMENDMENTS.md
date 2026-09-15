# Narrow source-backed amendments

The supplied baseline remains unchanged. An amendment blocks only its affected
path until its compensating implementation and installed-environment gates pass.

## A1 — Reference Alerta logs provisional SQL and returns database error detail

**Evidence:** Alerta server v9.1.0, commit
`f4312727783dad64198b62ed778bbca98a5144a3`,
`alerta/database/backends/postgres/base.py`, `_log()` calls
`cursor.mogrify(query, vars)` and logs the result before `cursor.execute()` in
`_updateone()`. Therefore DEBUG SQL logging can contain the proof before the
acknowledgement transaction commits. `alerta/views/alerts.py`, `action_alert()`,
converts a generic database exception to `ApiError(str(e), 500)`;
`alerta/exceptions.py`, `handle_api_error()`, logs that exception and returns its
message/errors. PostgreSQL DETAIL could expose the rejected row, including proof.

**Affected clauses:** baseline §5.1/§5.2 and T17/T19 require the provisional proof
to stay out of ordinary logs, notifications and error diagnostics. Last-in-chain
plugin ordering alone does not meet those requirements on this reference source.

**Smallest correction:** require the supplied
`zabalack.plugins.configure_app(app)` adapter after the existing Alerta app
factory and before serving. The provided `create_app()` wrapper performs exactly
that. It installs log filters on the application logger and existing ancestor
handlers, suppressing proof/intent-bearing records including exception and stack
text. Its error-response filter redacts proof/intent-bearing JSON error strings
without changing HTTP status. Successful history responses retain the committed
proof. Signing raises propagated `AlertaException` if the privacy adapter has not
been installed. No database persistence method or transaction is replaced.

**Safety consequence:** eligible source writes cannot originate from this plugin
without the adapter. Existing logging configuration changes, newly installed
handlers, tracing backends and notification/signal subscribers must be audited
under G1/G4 before enablement. Unknown additional status signal receivers prevent
activation. This adapter does not claim to control a privileged instrumentation
agent that extracts local variables or arbitrary application memory.

**Tests:**
`tests/unit/test_plugins_dispatcher.py::test_T19_logging_and_error_detail_redacted_without_altering_history`,
`test_T19_missing_privacy_adapter_rejects_eligible_ack`,
`test_T17_unknown_status_signal_receiver_blocks_activation`;
`tests/alerta/test_http_mocked_storage.py` exercises the real HTTP exception path
with explicit mocked storage. Real PostgreSQL failed-save evidence belongs to
the separately labelled lab suite; unexecuted lab cases are not passed.

## A2 — Reference Zabbix webhook transport does not authenticate TLS peers

**Evidence:** inspected Zabbix 7.0.10, commit
`237358b56b2584b94b1f8358f479ade669693ba2`,
[`src/libs/zbxembed/httprequest.c`](https://github.com/zabbix/zabbix/blob/237358b56b2584b94b1f8358f479ade669693ba2/src/libs/zbxembed/httprequest.c),
SHA-256 `28edf681adf5594ec60ba86277d47f65b41875f88d996845ee532e725bb321e8`.
The `HttpRequest` constructor sets `CURLOPT_SSL_VERIFYPEER` and
`CURLOPT_SSL_VERIFYHOST` to zero and enables `CURLOPT_FOLLOWLOCATION`.
The initial HTTPS scheme check in the supplied webhook does not override those
settings or constrain later redirect destinations.

**Affected boundary:** Zabbix webhook → Alerta ingestion, including protection
of its scoped ingestion credential. An HTTPS URL and a configured CA file alone
cannot establish authenticated transport for this reference client. A passing
internal lab webhook test cannot demonstrate that property. This is independent
of Zab-al-ack worker → Zabbix API transport, whose HTTPX client verifies TLS and
does not follow redirects.

**Required deployment resolution:** keep production enablement blocked at G1/G2
until the installed webhook transport has an inspected, approved remediation or
an independently authenticated transport control enclosing the complete path.
Record exact source/build/configuration evidence and tests rejecting an untrusted
certificate, a wrong hostname and redirects outside the approved destination.
An upgrade, an upstream patch or a transport control needs its own compatibility
and security review; this release does not silently choose or install one.
Do not disable worker TLS verification to imitate the reference webhook.

**Implementation and evidence:** the existing media artifact remains unchanged.
The new normal-process lab exercises only synthetic internal traffic and labels
this limitation explicitly. Its topology and offline safety checks are not
TLS-authentication evidence. No production endpoint or credential was accessed,
and no G1–G8 gate is closed by the source inspection or the lab fixture.

## A3 — Reference Zabbix renders an absent cause event as `*UNKNOWN*`

**Evidence:** inspected Zabbix 7.0.10, commit
`237358b56b2584b94b1f8358f479ade669693ba2`,
`src/libs/zbxexpression/expression.c` (SHA-256 of the built tarball copy
`b6328ea52e5785bc91976c39ccb125d6d630307511980554a6aa773dc33db0d2`). `get_event_cause_value()` returns `FAIL` when the
event has no cause event (`0 == (*cause_event)->eventid`), and the macro
substitution loop then replaces the token with `STR_UNKNOWN_VARIABLE`, the
literal `*UNKNOWN*`. The first real webhook delivery on the deployment-path lab
carried `event_cause_id = "*UNKNOWN*"` for an ordinary problem and failed with
`Invalid numeric identifier: cause`. The earlier reading of 7.0.0 — that the
resolver leaves `{EVENT.CAUSE.ID}` unexpanded for an absent cause — was wrong;
the synthetic webhook test had enforced that wrong contract by asserting that
`*UNKNOWN*` is rejected.

**Affected boundary:** Zabbix webhook → Alerta ingestion. With the previous
script every cause-less problem, recovery and update notification failed
before any HTTP request, so no source fact reached Alerta from a real 7.0.x
server. No write-back or acknowledgement boundary was affected.

**Resolution in this release:** `zabbix/v1/webhook.js` treats `*UNKNOWN*`, the
unexpanded macro and `0`/empty as "no cause" (`zabbix_cause_event_id: null`) and
still rejects any other non-numeric text; the media artifact is rebuilt from
the script; the synthetic contract adds the `*UNKNOWN*` fixture and keeps the
rejection of arbitrary strings. The real media/update/recovery flow now passes
on the lab (`evidence/deployment-e2e-run.json`). Symptom events with a real
cause continue to carry the numeric cause identifier.

## A4 — Zabbix role defaults do not revoke omitted existing action rules

**Evidence:** source tarballs Zabbix 7.0.10 and 7.0.30,
`ui/include/classes/api/services/CRole.php`, `updateRules()`,
`compileActionsRules()` and `compileUiRules()`. Creation initializes the old
defaults to ENABLED. For an omitted individual action or UI rule, the compiler
keeps the old/default value even if the new `*.default_access` is zero. Setting
the new default to zero alone therefore does not disable the available rules.
The independent review records hashes of both inspected sources and helpers.

**Observed consequence:** the supplemental real 7.0.30 test created a role with
the same default-only pattern as the original deployment helper. Its scoped
worker could suppress a synthetic problem with action 32. Close and severity
changes were denied by the host read-only boundary, with JSON-RPC -32500;
those responses did not establish the intended action-role denials. The first
supplemental attempt failed five of ten tests and is retained in the handoff.
The corrected regression also fails against the original a12 lab provisioner.

**Affected boundary:** least-privilege synthetic source-role provisioning and
the permission claims of that fixture. The production RPC client continues to
allow only acknowledgement action 2 or acknowledgement-plus-comment action 6;
its implementation and the SQL are unchanged.

**Correction:** `lab/zabbix_roles.py` enumerates the inspected User-type action
and UI rules with explicit zero/one statuses. The deployment helper reads the
effective role back through `role.get` and requires exact action/UI permissions,
the API method allowlist and its default/access settings before creating users
or tokens. Duplicate, missing, ambiguous and uninspected rules fail closed.
The independent real-source fixture uses the same constructor and verifier.
Only acknowledge/comment are enabled for its worker; the independent witness
has read APIs and no enabled actions. The tests still require actual, specific
action-permission denials; they were not relaxed to accept generic errors.

Zabbix also shares one role capability between acknowledge and unacknowledge.
The client blocks action 16, but a token with the upstream acknowledge capability
is not inherently incapable of unacknowledging. Protect and inventory that token
accordingly; do not describe its upstream role as an action-2-only capability.

**0.1.0a13 addendum — independently reproduced, and extended to the operator
fixture.** The finding was reproduced on a second, separately built Zabbix
7.0.10 instance: a role created with the original a12 rule set read back with
all eleven User-type actions enabled, and `event.acknowledge` action 32 was
**accepted**; close and severity were refused with JSON-RPC -32500 "No
permissions to referred object", the host-rights boundary. With the corrected
enumerated rules the same three requests were refused as -32602 "no permissions
to suppress/close problems / change problem severity", and `role.get` matched
the request exactly. A token whose role enables `acknowledge_problems` was also
observed to accept action 16, confirming the shared capability.

The review corrected the programmatic deployment provisioner. The
operator-provisioned real-Zabbix fixture (`lab/real_zabbix.md`) carried the same
instruction — "grant only the required actions" — and the fixture used for this
candidate's own runs was found to carry `execute_scripts`, `manage_api_tokens`,
`invoke_execute_now`, `edit_dashboards`, `edit_maps` and `change_problem_ranking`
on both the worker and witness roles (contained in practice only by the API
method allowlist). a13 therefore documents the enumerate-and-read-back
requirement for the fixture, points the operator at the same `role_rules`
constructor for the worker and witness shapes, and adds
`tests/real_zabbix/test_contract.py::test_A4_restricted_token_excluded_actions_denied_by_role_permission`,
which requires the real server's specific suppression-permission denial for the
supplied token and explicitly refuses to accept a host-rights rejection as
evidence. It is amendment evidence, not a new T-catalogue case.

**Deployment gate:** G5 still requires effective-role read-back and positive and
negative tests on the approved installed account. A passing disposable fixture
does not close it. No production account, credential, role, trigger or action was
changed. Existing a12 lab tokens must be discarded with their disposable lab;
do not treat the earlier fixture's default-setting assertion as permission proof.

## A5 — Reference Alerta commits staged plugin attributes and serves them to every reader of the alert

**Evidence (reference source):** Alerta server v9.1.0, commit
`f4312727783dad64198b62ed778bbca98a5144a3`. `alerta/utils/api.py`,
`process_action()`, closes its plugin loop with

```python
if alert_was_updated:
    alert.update_tags(alert.tags)
    alert.attributes = alert.update_attributes(alert.attributes)
```

`alert_was_updated` is set by any plugin returning a truthy value. The signer
returns a tuple on every path on which it stages an intent, so the commit always
follows a staged intent. `Alert.update_attributes()`
(`alerta/models/alert.py`) calls `db.update_attributes()`; the PostgreSQL
implementation (`alerta/database/backends/postgres/base.py`) issues
`UPDATE alerts SET attributes=…` through `_updateone()`, which calls
`self.get_db().commit()`. The commit does not depend on the rest of the request.
`action_alert()` (`alerta/views/alerts.py`) runs `process_action()` before
`from_action()`, which fires the status-change hook and only then writes history
through a single `db.set_alert()`. A staged attribute is therefore committed
before signing is attempted and survives any later failure of that request.

Alerta returns `attributes` in ordinary alert responses. `alerta/utils/response.py`,
`jsonp()`, additionally wraps any request carrying `?callback=` as
`application/javascript` — 22 `@jsonp` view decorators in `alerta/views/alerts.py`
alone — and that wrapping happens in the view decorator, before `after_request`.
A response filter keyed on `response.is_json` does not engage for those requests.

Responses are not the only reader-facing surface.
`alerta/database/backends/postgres/queryparser.py` compiles an attribute term
`attributes.<key>:<term>` into `"attributes"::jsonb ->>%(key)s ILIKE %(term)s`
with the term wrapped in `%…%`. `->>` on a JSON object returns that object's
whole serialised text, so the comparison matches a substring occurring anywhere
inside the intent, including an element of `actor.customers`. The authenticated
`/alerts/count` and `/alerts` endpoints (`alerta/views/alerts.py`, both
`@permission(Scope.read_alerts)`) scope *which alerts* are searched to the
reader's own customers but place no constraint on the attribute contents the
predicate inspects. A reader entitled to an alert can therefore use it as an
oracle: submit a candidate string, read the returned count, and learn whether it
occurs in the intent — without the intent appearing in any response body. The
default search field is `text`, so this requires the explicit attribute syntax
rather than a bare term. Reproduced in
`tests/alerta/test_postgres_search_confidentiality.py` on **PostgreSQL 18.3 in
PGlite (WASM)**, supplemental single-backend evidence: a probe for a string in no
intent returns zero, while probes for the undisclosed customer and for the
acknowledging login both match. Native execution of that module is a separate
layer and is not established by it.

Two further surfaces reach the same value. `/alerts/history`
(`@permission(Scope.read_alerts)`, also `@jsonp`) serialises through
`RichHistory`, a class distinct from `Alert`, whose rows come from
`SELECT … attributes … FROM alerts, unnest(history[…])` — so every entry carries
the alert row's *current* attributes rather than a historical copy. A filter
applied to `Alert.serialize` alone leaves the intent fully readable there. And
`get_topn_count` interpolates the grouping expression into both `SELECT` and
`GROUP BY` and returns it under that key, so `group-by=attributes` on
`/alerts/topn/count` — and its `top10`, flapping and standing siblings — hands
the reader the whole attributes object as a plain dictionary, bypassing every
serialiser. That query reads `FROM alerts, UNNEST(service) svc`, so an alert with
an empty `service` array is dropped and a regression without one passes
vacuously.

**A response filter therefore cannot establish this invariant on its own.** It
closes bodies, not the predicate.

**Nor does retirement alone establish it.** Alerta commits the staged attributes
in `process_action()` before `from_action()` signs and writes history, through an
independent commit. Between those two commits the value is committed and visible
to every other session, so even a retirement performed atomically inside the
acknowledgement transaction leaves that interval open; a request that fails or is
abandoned leaves it open indefinitely. Two designs can close it — restricting
what reader queries may inspect, or never committing readable plaintext there —
and a cleanup-only design closes neither. **Completing T41 does not by itself
establish T40**, which depends on demonstrated confidentiality across the
interval as well as after it.

**Chosen design: query restriction.** Restricting what reader queries may inspect
protects the interval without altering the signing and capture protocol, which
retirement cannot do. The guard installs at the shared alert query-builder
boundary — `qb.alerts.from_params`, the single point that sees `q`, `q.df`,
direct filters, `sort-by` and `group-by` — so the list, history, count and
aggregate endpoints inherit one control instead of each view being patched. It
validates *resolved* fields and refuses unsupported selector syntax rather than
normalising it. The query string is parsed and the produced SQL and bound
parameters are inspected, because a substring blacklist over raw text cannot see
through the `_.key` alias, sub-expressions or `_exists_`.

Three spellings defeat a narrower guard, and each is covered. `QueryParser.parse`
substitutes `q.df` into the *unquoted* token inside `"__default_field__"`, so a
crafted default field such as `text" || attributes::text || "text` becomes a raw
concatenation naming the column with no quotes at all; `q.df` is therefore
validated against the builder's supported field names before compilation, and the
whole-column check is quote-insensitive. `sort_by.split('.')[1]` keeps only the
second component and discards the rest, so `attributes.<restricted>.ignored`
would reach the restricted key; selectors with extra components are refused.
`VALID_PARAMS['attributes']` is both filterable and sortable, so a bare
`attributes=` or `attributes!=` direct filter is a whole-object operation and is
refused alongside the keyed ones.

Two boundaries need explicit allowlists rather than syntactic tests. A grouping
token that is merely a plain identifier is not safe: PostgreSQL reads a bare
table-name reference as the whole row, so `group-by=alerts` selects every column
of the row — attributes included — and the aggregate returns it under that key.
Reproduced on PGlite: that request returned HTTP 200 with the intent, the actor's
full customer list and the committed proof marker serialised into the row value.
Only explicitly supported alert fields are therefore permitted. And `filter_query` handles `!`
in two ways, neither of them the naive one: its column lookup strips every `!`,
while its negation test `field.endswith('!')` is reached only for non-attribute
columns. `severity!` compiles to real negation, but a trailing `!` on an
attribute selector does not — `attributes.<key>!` takes the earlier
`startswith('attributes.')` branch and compiles to containment on the literal
JSON key `<key>!`, which does not exist and matches nothing. A marker *before*
the dot is different again: `attributes!.<key>` fails that `startswith` test and
compiles as a whole-object comparison on `attributes`, so stripping the marker
globally would make the guard read the same input as a permitted keyed selector.
Unsupported placements are refused rather than normalised; a trailing marker is
stripped before the key comparison, which conservatively refuses
`attributes.<restricted>!` even though upstream would match nothing. Upstream
behaviour is preserved as it is — this amendment does not change it.

Column tokens are resolved canonically rather than compared as spelled.
PostgreSQL folds an unquoted identifier and honours a quoted one, so
`ATTRIBUTES`, `Attributes`, `"attributes"` and a padded spelling all reach the
same column; comparing raw text refuses only one of them. Grouping tokens are
additionally required to be plain identifiers, because the aggregate query
interpolates them into `SELECT` and `GROUP BY` with no upstream validation, and
attribute keys are required to be plain names, because `sort_by_columns`
interpolates the key straight into `attributes->'<key>'` where a quote escapes
the literal. Those forms are unsupported syntax and are refused whether or not
they name the restricted key. Upstream's own `VALID_PARAMS` check already
rejects a mis-cased `sort-by` or direct filter with its own 400; the guard's
contribution on those surfaces is the restricted key, the whole-object form and
the unsafe key syntax. Restricted selectors and whole-`attributes` operations are refused with a
stable `400` that does not depend on whether any alert or intent exists, so the
refusal is not itself an oracle; ordinary keyed attribute searches and approved
sorting and grouping are preserved. It applies to every API reader including
administrators, and **it stays in place after retirement ships** — it is what
protects the interval, which cleanup cannot reach.

**Evidence (this implementation):** `src/zabalack/plugins.py`, `_human_actor()`,
returns `{"login", "customers": sorted(set(g.customers)), "admin", "auth_type"}`.
`customers` is the operator's complete authorised customer scope, bounded at 100
entries (`src/zabalack/crypto.py`, `sql/migrations/002_capture.sql`), not the
customer of the alert being acknowledged. `plugins.py` deep-copies the whole
intent, actor included, into `alert.attributes["zabbix_ack_sync_intent"]`. The
only removal in the tree is `normalizer.project_skip()`, reached from skip
branches; `zabalack.project()` merges with `||` and removes nothing.

**Affected clauses:** §5.2 defines the intent payload as a reserved-namespace
attribute including the authenticated actor, without constraining who may read
it. §12's classification table has no row for the Alerta attribute layer;
`docs/security_and_data.md` supplies one reading "no public attribution", and
places the intent payload and login in the restricted-capture layer, whose
required policy is restricted readership. §12's own capture row limits identity
data to "the authenticated login"; an authorisation-scope list exceeds that.
§3.3 requires `CUSTOMER_VIEWS`, so every supported deployment is tenant-scoped,
and §4.2 makes Alerta's authenticated customer scoping the authority for operator
access — which this disclosure exports to the readers of a single customer's
alert. T24 covers the authorisation direction (an operator lacking customer B
authority obtains no write-back); no catalogue case covers the disclosure
direction, and the reference suite currently asserts the attribute's persistence.

§5.1 already records that "Attributes modified by plugins are persisted before
the acknowledgement history write, so an intent attribute is not proof that the
acknowledgement committed", and interpretation 2 of `final_review_notes.md`
repeats it. The baseline established the persistence as an evidentiary caveat
and did not evaluate the confidentiality of the persisted content. This
amendment does not dispute either statement; it adds the omitted consequence.

**Invariant:** a reader must not be able to learn, from any Alerta layer that
reader can read, an actor's customer memberships outside the reader's own
authorised scope. The reader's scope is not assumed to be a subset of the
actor's: for an actor serving customers A and B read by a reader authorised for
A and C, the disclosure of concern is B. Actor authorisation scope is
restricted-capture data. The intent payload is excluded from reader-facing
projections in its entirety, not filtered field by field.

**Exposure is not confined to the success path.** `claim_verification` selects
only `state IN ('captured_unverified','verifier_hold')`, and `project()` is
reachable only with a verified `action_id`:

| Path | Trace in `zabalack` | Reaches `project()` |
|---|---|---|
| Staged, then signing or binding failure at `status_change` | none | never |
| Cohort enabled in configuration, absent from `zabalack.cohorts` | none | never |
| Capture-side evidence failure (outbox `failed`) | capture + outbox | never claimed |
| `capture_bypass` (outbox `skipped`) | capture + outbox | never claimed |
| Worker-side `reject_capture` | capture + outbox | no action row |
| `verifier_hold`, key unavailable | capture + outbox | re-claimable, unbounded |
| Worker stopped or backlogged | capture + outbox | only if processing resumes and verification succeeds |
| `verify_complete` | full | yes |

The first two rows leave a committed attribute with no capture record, for
different reasons, and must be treated as distinct cases. In the first the
request stops before the final history write: status never becomes `ack`, no
marker is written, and the trigger predicate — which requires `NEW.status='ack'`
and a marker in the head history entry — is never satisfied, so `capture_ack()`
is not invoked at all. In the second the acknowledgement commits normally and
the trigger does fire, but `capture_ack()` returns at its eligibility check
because no enabled row exists in `zabalack.cohorts` for the
customer/instance/trigger tuple, inserting neither a capture nor an outbox row.
Cleanup placed only in `project()` therefore cannot reach the majority of these
paths, and staging that never produced a capture needs its own retirement path
rather than a variant of the post-capture one.

**Correction, stage one (no schema change).** Exclude the intent key from
reader-facing serialisation at the point of serialisation, covering the JSONP
response mode explicitly rather than relying on `is_json`. Removal is targeted to
the single reserved key: the A1 evidence scrubber must not be reused, because
`_contains_evidence()` also matches `[zabalack:`, the committed history proof that
A1 deliberately retains in successful history responses. Signing, staging and
capture behaviour are unchanged — the attribute is still written and the trigger
still reads it. Both serialising properties are filtered: `Alert.serialize` and
`RichHistory.serialize`, the latter because `/alerts/history` carries the alert
row's current attributes. `RichHistory`'s `user` and `text` are left intact, as
the permitted attribution and the committed proof A1 retains. This is reader
protection only, and partial: it closes response bodies and leaves both the
search predicate and the pre-signing interval open. Stored copies remain in
`public.alerts.attributes`, backups, replicas and any existing export, and stay
reachable through `attributes.<key>:<term>` queries until stage two removes them.
Stage one must never be described as closing A5 or T40.

**Correction, stage two (forward migration, shipped as `008_generation_fence.sql`).**
Cover both retirement shapes: removal of the stored attribute after capture, and
guarded retirement of staging that never produced a capture. Apply this across
every lifecycle path above, not only projection, preserving
`zabalack.captures.intent` byte-for-byte as restricted signed evidence. Bounded
backfill covers existing and abandoned attributes **on live `public.alerts` rows
only**. It does not reach historical backups, replicas or previously produced
exports, which continue under G8's access and retention policy; backfill must
not be described as erasing those copies. Schema version moves 3→4 with the two
runtime literals that pin it, and the readiness manifest is re-sealed.

*Cleanup eligibility is not the 60-second capture window as first proposed.*
`capture_ack()` performs no lifetime check of its own: it inserts a capture row
whenever the trigger predicate is satisfied, however late. The lifetime test
lives in `verify_complete` (`003_worker.sql:88`, and the fenced form at
`006_late_response_fencing.sql:102`), which rejects a capture whose `captured_at`
falls outside `[issued_at, capture_expires_at]`. Two consequences follow. A
staged intent can still produce a capture after its window closes, so "the value
can no longer produce a capture" is not a correct justification for sweeping it.
And a capture recorded *inside* the window stays valid while it waits for
verification — which may be indefinite under `verifier_hold` — so the sweep must
leave `zabalack.captures.intent` intact and must not prevent that capture being
verified, authorised and delivered afterwards. The sweep therefore operates on
`public.alerts.attributes` alone, retires the intent once its window has closed
by a grace margin (or at once when its timestamps fail the signer's own bounds,
since the attribute is API-writable), and is demonstrably independent of whether
verification or delivery ever succeeds.

**The generation guard.** `project()` compared
`attrs#>>'{zabbix_ack_sync_intent,action_id}'` against its own action, and that
check covered a window nothing else sees: between a new staging and its capture,
`take_action` has popped `zabbix_ack_sync_local_at` and no newer `actions` row
exists yet, so neither the `local_at` test nor the `capture_order` test can fire.
Retiring the intent removes the comparison, and nothing carried in
`public.alerts.attributes` can replace it. `PUT /alert/<id>/attributes` runs no
plugin hook, and `db.update_attributes` (`postgres/base.py:339-349`) merges the
caller's dictionary into the one its own request loaded, drops every key whose
value is `None`, and writes the result back with `SET attributes=%(attrs)s` — a
whole-object replace, not a `||` merge. Executed on PostgreSQL 18.3 in PGlite: a
`write:alerts` holder scoped to the alert's customer sent
`{"zabbix_ack_sync_action_id": null}` and received HTTP 200 with the key
deleted, and sent `{"zabbix_ack_sync_forged": "client-supplied"}` and had it
stored verbatim, because `strip_reserved()` never sees that path. The same
whole-object replace is how a stale attribute dictionary reinstates an older
generation. An attribute-borne marker can therefore be erased, forged or rolled
back, and a record fed only by *observation* — capture, sweep, verification —
cannot protect a staging that was replaced before anything observed it: the
loss of that acknowledgement's generation is then permanent, not bounded by any
sweep interval, and a later replay of the older dictionary restores a marker such
a guard accepts. (A first implementation of stage two, kept unmerged as the
record in its own pull request, had exactly that shape and was withdrawn.)

**Requirement.** Every committed staging synchronously establishes protected
generation state. Attribute replacement, cleanup, repair and delayed
verification cannot erase it or move it backwards. Projection checks that state
atomically with its update. Ordering is assigned by the database, not read from
a client-supplied timestamp.

**The generation fence.** `zabalack.alert_generations` is append-only, keyed by
an identity sequence the database assigns, in the schema the Alerta application
holds no table privilege on. Nothing updates or deletes a row — not the sweep,
not projection, not migration — and there is no repair because there is nothing
to repair. Every staging is a protected database operation:
`zabalack.stage_generation(alert_id, action_id, intent)` records the generation
and writes the reserved `zabbix_ack_sync*` staging keys in one statement, under
the per-alert generation lock and then the alert row. The signer calls it on the
request's own Alerta connection — so it runs as the role Alerta connects as, the
only role granted it — and commits it explicitly before returning, because
`process_action()` commits `update_tags()` before `update_attributes()` and a
write left pending would ride the wrong commit. Alerta's later whole-object write
of the same dictionary leaves the reserved keys as the operation wrote them; as
on every attribute write, it drops keys whose value is null — the nullable
source-contract keys among them — which is upstream behaviour the fence neither
changes nor depends on. If the operation fails for any reason the transaction is
rolled back and the acknowledgement is refused with
`zabalack_generation_unavailable` (503): no staging exists without its generation
recorded, and the plugin fails closed when deployed ahead of the grant.

A staging is made only for an acknowledgement Alerta will record as one.
`from_action()` decides the alarm-model transition from the history after the
hook returns — after the protected commit — and for a shelved, closed or expired
alert raises (409), while for one in `assign` it keeps the status (200); no
signed status write follows either. The signer takes the same decision first,
from the same history, and such a request stages nothing: nothing to capture,
nothing to supersede. What remains is a staging whose status write fails after
the commit — a refused signing, a capture fault. Its fence row is permanent and
the sweep retires its intent; it is superseded only by a later acknowledgement
through the protected operation, and until then the earlier action's
re-projection is refused with `projection_action_generation_mismatch`. That is
the designed answer — the newest attempt on the alert is the one whose staging
committed last, and the refused older projection is reported, never applied —
and the operator's remedy is to acknowledge again.

The newest generation for an alert is the highest-sequence row recorded at
staging; rows recorded by observation — `capture_ack()` as a fallback for
acknowledgements staged before the fence existed or by a plugin that predates
it, and the migration's backfill — rank below every staging-recorded row
whatever their sequence, so a capture arriving late can never promote an older
acknowledgement above a newer staging. A replayed older `action_id` already
exists and is a no-op; a never-seen forged id can only be recorded at the
observation tier, and only through a capture that bound — marker, intent and
row in agreement — which `PUT /alert/<id>/attributes` cannot produce. `project()` takes its action row, the generation lock, then the
alert row — the order staging takes the last two — and refuses with
`projection_action_generation_mismatch` unless its action is the newest
generation, or with `projection_generation_unknown` when none is recorded. The
guard takes no expiry: `execution_expires_at` bounds delivery, while
`projection_due_at` carries no equivalent deadline. The `local_at`, customer and
source-identity checks from 002 stay in front of it. 002's `capture_order`
comparison is not kept: the fence is the one ordering. Every action row follows
a capture whose intent validated, and that capture (or the backfill) recorded
its generation, so the fence sees everything `capture_order` saw — and under
two concurrent acknowledgements the two orders can disagree, B staged after A
but captured first, and refuse both actions forever. The fallback records only
a capture that bound — an unbound, malformed or bypassed one can never be
verified into an action, and recorded it would be the newest generation on an
alert without a staging-recorded row — and every cast of an API-writable
timestamp in the sweep and in `project()` treats a value the cast rejects as
unvalidatable — retired now, or refused — rather than raising out of a sweep
that serves every other row.

**Privilege.** `12_application_grant.sql`, run as `zabalack_owner` with the
application role named, grants that role `USAGE` on the schema and `EXECUTE` on
`stage_generation()` and nothing else; it first revokes the function from every
other non-owner grantee, so a change of application role never leaves the
former role's credentials able to stage, and records the grantee in
`zabalack.installation.application_role`. `USAGE` confers nothing on its own only
while no function in the schema is executable by `PUBLIC`, the fence admits no
grantee but its owner, and the entry point's only non-owner grantee is the
recorded role, which holds `USAGE` on the schema (without it the qualified call
fails at schema access behind a passing probe), has no non-superuser member of
its own, and is, and can assume, none of the `zabalack_*` roles. A member of it
inherits its privileges or reaches them with `SET ROLE` and stages through the
entry point; an application role that can assume `zabalack_worker` reaches
`worker_call()` and verifies, holds and rejects captures without the verifier's
signature check. Neither membership moves an ACL, so the grantee list and the
sealed digests read the same with it as without; the grant script refuses a role
in either position, having no `ADMIN OPTION` to revoke a membership with.
Its effective rights here are the entry point and nothing more: as itself or as
any role it can assume it executes no other function in the schema and holds no
privilege on a table there, the alerts owner's own `capture_ack()` and
`seal_installation()` grants excepted where that role is the application role.
A stray grant does move the digests and reads unhealthy through them — until a
re-seal, which every upgrade performs, agrees with it; this assertion is what
still holds afterwards. `capture_health()` asserts all of it under
`functions_ok`, so a regression there marks every worker unhealthy, and
`13_reseal.sql` refuses to seal a tree that breaks any of it. The same two
assert the schema's whole grant map, not only the application's corner of it:
the grantees of every routine in `zabalack` — procedures as well as functions,
a `SECURITY DEFINER` procedure running as its owner exactly as a function does,
which `catalog_fingerprints()` now hashes too — are exactly the ones the
installation makes, read both ways — a revoked `EXECUTE` leaves no unexpected entry for a
negative rule to find while the role it answers to gets `permission denied`, and
the migrations do not re-make a grant they already made —
`worker_call(text,jsonb)` and `project(uuid)` to `zabalack_worker`,
`operator_call(text,jsonb)` to `zabalack_operator`, `capture_health()` to
`zabalack_probe` and `zabalack_operator`, `capture_ack()` and
`seal_installation()` to the alerts owner, `stage_generation()` to the recorded
application role, every other function to no one — no relation here admits a
grantee at all — the fence's identity sequence included, where `UPDATE` is
`setval()` and a sequence moved backwards inverts the ordering the fence rests
on — the schema and every function and relation in it remain owned by
`zabalack_owner`, a `SECURITY DEFINER` function running as its owner; schema
`zabalack` itself admits only the grants the installation makes — `USAGE` and no
other privilege, to the three runtime roles, the alerts owner and the recorded
application role, and to nobody else, `CREATE` going to the owner alone and
`PUBLIC` getting nothing — and each of the three runtime roles still holds that
`USAGE`, without which its fully qualified call fails at schema access while
every function grant below still reads exactly right, so verification and
delivery stop behind a probe reporting healthy — as does the alerts owner,
a separate role wherever the deployment splits the application from it, whose
005 `capture_ack()` and `seal_installation()` grants and whose `10_trigger.sql`
run both fail at schema access without it; `zabalack_owner` still holds exactly the `public.alerts` grants
`00_roles.sql` makes it — `SELECT` on `id`, `customer`, `status`, `attributes`
and `update_time`, `UPDATE` on `attributes`, and nothing else — every other rule
about that table being a prohibition, and a prohibition not seeing a grant that
is missing, while `stage_generation()` runs as this owner and without
`UPDATE(attributes)` refuses every acknowledgement, `public.alerts` lying outside
the schema `catalog_fingerprints()` hashes so that no digest moves either;
none of the four `zabalack_*` roles is a member of any role at all, every
other membership rule here reading inward and this one outward — the alerts
owner granted to `zabalack_worker` lets it `SET ROLE` to that owner and write
`public.alerts` directly, past the projection boundary, while
`has_table_privilege()` stays false, resolving inherited privilege where a
`NOINHERIT` role inherits none of what `SET ROLE` still hands it; no role
outside the recorded installer topology can act as any of the four, whichever it
is and whoever the role — reach rather than the direct edge, `SET ROLE`
following a chain and `pg_has_role(..., 'MEMBER')` following it too, which is
what refuses a member of a recorded installer rather than exempting it along
with the installer — a `NOINHERIT` member resolving no privilege
through any ACL while `SET ROLE` hands it the role whole, and
`catalog_fingerprints()` hashing a membership only where the *member* is itself
one of the four, so an outside one moves no digest at all; each of the four
keeps the attributes `00_roles.sql` declares, an attribute being neither an ACL
nor an ownership and `SUPERUSER` or `BYPASSRLS` walking past every rule here.
The installer topology is named rather than assumed: `sql/install/09_installer_roles.sql`
records in `zabalack.installation.installer_roles` the principals that may hold
`zabalack_owner`, and only `zabalack_owner` — a recorded installer granted
`zabalack_worker` is refused like any other role. Recording does not make an
installer less powerful, one that can act as the owner being able to rewrite the
list; it makes the set of principals that can an explicit, reviewable part of
the installation instead of an exemption for every role outside those four. The
column starts `NULL` and the script is the act that sets it, empty where the
migrations run as a superuser and no role holds `zabalack_owner` at all; an
installation that never recorded its installers is not healthy and is not
sealed. Whoever reaches `worker_call()` claims
verification leases and calls `verify_complete`, so the acknowledgement is
recorded as verified with no signature check on the way; whoever reaches
`operator_call()` holds the controls. Every one of these ACLs is hashed, so a
stray grant reads unhealthy through the digests; what these assertions add is
that it stays unhealthy after the re-seal an upgrade performs. The function is `SECURITY
DEFINER` with a pinned `search_path`, binds the intent to the staging (its
`action_id`, `alert_id` and the row's customer) and refuses an `action_id`
already recorded rather than re-staging it.

**Migration policy.** 008 records a generation for every capture that produced
an action or may still produce one — verified, or awaiting verification in the
outbox (`captured_unverified`, `verifier_hold`) — in capture order, assigned by
the database at capture, the best pre-fence order available; then one for the
intent currently on each live row; all at the observation tier. A capture
awaiting verification whose live intent has already left its row has no other
record, and left out it would leave the older verified acknowledgement as the
newest generation while the newer one, once verified, is refused; so its place
is taken at migration, in capture order — never appended when verification
finishes, which would be a second ordering. A capture that failed, was rejected
or expired never becomes an action and records nothing. A row with no record
acquires its generation at its first staging and refuses projection until then.
Acknowledgement paths are fenced across the upgrade, as the deployment guide
requires, so no staging is in flight between the backfill and the plugin that
records generations synchronously. A
shipped migration is immutable: `sql/migrate.py` records each file's digest and
refuses with `Migration drift` when a recorded file's bytes change, so a
correction is always a further migration.

**Reader-visible surface.** `zabbix_ack_sync_action_id` remains the only
generation-related key a reader sees; it is informational and consulted by
nothing. The stage-one query guard stays: it protects the pre-signing interval,
which retirement cannot close.

**Safety consequence:** stage one reduces reader exposure and does not close the
finding. Until stage two completes, an operator acknowledging an alert continues
to write their full authorisation scope to durable storage that is included in
backups and replication, and any consumer reading `public.alerts.attributes`
directly is unaffected by the response filter. Deployments that have already
acknowledged alerts carry existing copies until backfill runs.

**Acceptance (T40).** Reader-facing confidentiality across lifecycle states,
response modes, every reader-facing surface, and the pre-signing interval. An
engineer serving lab-a and lab-b acknowledges a lab-a alert; a reader serving
lab-a and lab-c — neither scope containing the other — must learn nothing about
lab-b. Asserted before worker processing, after worker processing, with the
worker unavailable and after a failed acknowledgement; each in both the JSON and
JSONP response modes; across the single-alert response, the collection response
and `/alerts/history`; through every restricted selector — keyed term, `_.key` alias, phrase,
sub-expression, `_exists_`, `q.df=attributes`, the `attributes.<key>` direct
filter, restricted and whole-column `sort-by`, and `group-by=attributes` on the
aggregate endpoints — against `/alerts/count`, `/alerts` and `/alerts/history`;
and, on native PostgreSQL, from a second session while an acknowledgement is
paused between its staged-attribute commit and its history write, plus the failed
and never-captured paths, with both HTTP sessions bound to the application role.

Restricted selectors are asserted to be **refused with a stable 400**, not to
return zero: a zero count is satisfied by an empty database. The refusals for a
present and an absent term are compared to each other, which is what
demonstrates the absence of an oracle.

Controls, because refusal and zero assertions are both cheap to satisfy wrongly.
Each query is scoped to its own alert by conjoining the source event id. A
permitted keyed attribute search, a permitted `sort-by` and a permitted
`group-by` are positive controls: a mitigation that disabled attribute querying
wholesale must fail them rather than pass everything else. The aggregate cases
require the fixture alert to carry a nonempty `service` array, or the
`UNNEST(service)` join drops it and the case passes on an empty result. The
precondition for a meaningful run is restricted capture evidence in
`zabalack.captures`, never the continued presence of the alert attribute, which
a correct stage-two retirement removes. Every case also asserts the read left the
capture input, unrelated operator attributes, source identity and the committed
history proof intact, and that `/alerts/history` still carries its permitted
`user` attribution, so a sanitiser that stripped fields from the response or
mutated the shared attribute dictionary cannot satisfy it.

**T40 and T41 are independent.** T40 is the response filter plus the query guard,
and is established by its own evidence; its native interval cases must execute
natively, which the single-backend WASM layer cannot do. T41 is the retirement
migration. Neither implies the other: retirement alone leaves the interval open,
and the guard alone leaves the value stored. A5 remains open until both have
passed their installed-environment checks.

**Acceptance, stage two (T41).** Executed against real PostgreSQL, through
the real entry points: the HTTP action, status and attribute handlers under a
customer-scoped `write:alerts` token, `worker_call('verify_complete')` as
`zabalack_worker`, `sql/migrate.py` on a populated database, and `project()` as
`zabalack_worker`. Every refusal asserts its exact reason, so removing a guard
branch changes the result rather than the route.

*Lifecycle coverage is the exposure table above, case by case, not a summary of
it.* Each of its eight rows is a separate case, and the sweep must be shown to
retire the staged attribute in each without depending on verification or
delivery having succeeded: staging that failed at `status_change`; a cohort
enabled in configuration but absent from `zabalack.cohorts`; capture-side
evidence failure; `capture_bypass`; worker-side `reject_capture`; `verifier_hold`
with the verification key unavailable; a worker stopped or backlogged with the
capture still unclaimed; and `verify_complete`. `project_skip` and the
`project()` refusal branches are *additional* coverage on top of those.

*The fence and the staging are one transaction.* After a real acknowledgement the
fence row carries `recorded_by='staging'` — the capture fallback writes
`'capture'`, so a missing call cannot be masked. A fault on the fence insert
refuses the acknowledgement and leaves no staging; a fault on the staging write
rolls the fence insert back with it. Two stagings of one alert from two sessions
serialise on the generation lock and receive sequences in commit order; a
staging and a projection serialise in either order without deadlock.

*Preservation and refusal.* B staged and uncaptured, its reserved keys deleted
through the API, and separately A's whole dictionary replayed through the API:
A stays refused and the newest generation stays B. B observed and retired by the
sweep, then A's proof replayed and verified again as a replay: the same. A late
capture of an acknowledgement staged before the fence never outranks a staged
generation. Forged markers and intents written through the API leave the fence
untouched and the current action projects; a later legitimate acknowledgement on
the same alert becomes the newest generation. A row with no generation recorded
refuses.

*Upgrade.* A schema-3 database with A verified and projected and B staged as an
attribute alone, upgraded by `sql/migrate.py`, the grant and the re-seal: the
fence holds A then B at the migration tier, A's projection is refused, B
captures, verifies and projects, the first staging through the protected
operation outranks both, and `capture_health()` reports healthy.

*The decisive native race.* Stage A, then stage B on the same alert; retire B's
intent without ever creating a B action row; resume A's projection six hours
later, past `execution_expires_at`; assert A cannot overwrite B's state, in both
lock orders and with a stale attribute dictionary replayed against the row.
Native only: it needs two concurrent sessions and a committed row observed from
the other.

Where these overlap existing requirements, cross-reference T7 (replayed proof,
stale attribute dictionary reintroduced) and T38 (projection under the deployed
owner and caller roles). T38's preservation requirement is read as preserving
source-identity keys, unrelated attributes and the protected columns; it does
not require preserving the intent after a valid cleanup, which occurs inside the
`zabbix_ack_sync*` namespace the projection function already owns. The existing
dispatcher assertions that the intent is unchanged between staging and signing,
and that a duplicate callback reuses one identity, are anti-tamper coverage for
§5.1 and are retained unchanged; a further one asserts the generation is
recorded before any attribute write and that a refused recording stages nothing.

**Deployment gate:** G4 and G8. T41 reaches PASS when retirement executes on the
installed environment. T40 additionally requires the search path and the
pre-signing interval to be closed, which retirement alone does not achieve, and
its native concurrency cases must execute natively — the WASM layer cannot
establish them. A5 remains open until then. Stage one is a partial
mitigation of the response boundary and must be described as such.
