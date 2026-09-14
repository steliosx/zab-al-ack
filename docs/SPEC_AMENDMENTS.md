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
