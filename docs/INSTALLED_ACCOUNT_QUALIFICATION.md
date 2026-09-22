# Installed-account A4/G5 qualification

This is a prepared procedure, not an execution record or deployment authority.
The approved installation, account, cohort and test authorization have not been
supplied. A4/G5 remain pending for that installation. Disposable-fixture passes,
including the current ARM64 candidate's real-Zabbix cases, do not close them.

The qualification baseline is version `0.1.0a16`, PR #24 at
`7ae972e8488ab9602c60a4895a98e1204d534207`, based on
`013fbc6f99c171cc484cbbaafcec4de418b4dfe7`. Its qualified wheel SHA-256 is
`1f072f0443a2499cecb27d45ffcee7401c3d5c22b5dd7c3ae5be627ff8463774`.
Refresh the actual candidate before execution and record the installed artifact;
do not rebuild or silently substitute it. A changed implementation, dependency,
SQL or executable harness needs a documented affected-evidence revalidation
decision. This procedure changes no version or artifact.

Read [A4](SPEC_AMENDMENTS.md#a4--zabbix-role-defaults-do-not-revoke-omitted-existing-action-rules),
[the input register](INPUTS_REQUIRED.md), [deployment procedures](deployment_acceptance.md)
and [G1-G8](DESIGN.md#15-deployment-gates) together. A4 addresses effective Zabbix
role restrictions. G5 additionally covers customer assignment, human authority,
tenant boundaries and approved administrative exceptions.

## Required inputs and owners

Record these in the protected installation packet, using opaque references in
shared summaries. Supply secret-manager/file references, never credentials,
private inventories or raw alert histories in GitHub or committed evidence.
Unassigned fields are missing inputs, not permission to choose a production
target or invent an account.

| Input | Required value or evidence | Accountable owner |
|---|---|---|
| Environment | Named approved beta/staging installation; logical database and Zabbix instance identities; servicing nodes; installed Alerta server/UI, Zabbix, PostgreSQL and PgBouncer revisions; exact candidate and wheel hash | Platform and installation owner |
| Transport | Reviewed HTTPS endpoint and CA reference; routing and source-version inventory; disposition of applicable A2 controls | Platform and security owner |
| Delivery identity | Actual Zabbix user ID, role ID and user-group IDs; effective host/group rights and inheritance; protected token reference, expiry and custody owner | Monitoring and security owner |
| Inventory identity | Separate approved identity able to inspect the installed delivery user, groups and effective role; protected credential reference | Monitoring owner |
| Witness identity | Separate read-only identity able to establish both in-scope and outside-scope synthetic objects; no enabled actions | Monitoring and security owner |
| Alerta actors | Approved authenticated human and customer scope; outside-customer human; ingestion/service identity references; actual authentication/customer settings; administrative-exception policy, including an explicit no-exceptions decision if applicable | Identity and customer owner |
| Cohort | Approved `(customer, logical_instance, trigger_id)` entries; complete host-ID and group-ID sets; mapping revision; one-to-one assertion, correlation/exclusion rules and comment policy | Monitoring and customer owner |
| Fixtures | Explicit fresh original problem-event and alert IDs inside the cohort; separate outside-scope event/host IDs whose existence the witness can establish; approved synthetic canaries for each negative mutation attempt | Test and monitoring owner |
| Notification containment | Complete selected matching action/update-operation inventory; isolated recipients/sinks; approved handling of each canary and unintended side effect | Monitoring and operations owner |
| Test authority | Approved environment and change/test-window reference; named executor; exact permitted positive and negative mutations; stop conditions; authorized recovery/cleanup principal and actions | Accountable installation/change owner |
| Evidence and review | Protected destination and durable backup; access/retention policy; evidence reviewer and scope; any independently required approval | Security, records and release owner |

The delivery token must not gain configuration-read methods merely to perform
inventory. The separate inventory identity performs `role.get`. An API version
response alone does not prove token rights. Existing SQL role aliases and any
site-specific mapping remain subject to the exact-role requirements in the
[input register](INPUTS_REQUIRED.md); this procedure grants no SQL privileges.

## Ordered procedure

1. **Confirm scope, identity and containment.** Refresh the candidate and active
   claims, acquire applicable worker/checkout/lab locks, and verify the approved
   installation and artifact. Record G1-G4 inputs, actor/cohort mappings and
   effective notification containment. A new environment, unexpected version,
   ambiguous target or missing mutation authority stops only the dependent
   installed-account operation. Continue independent authorized lab work.

2. **Read the effective installed role before any canary write.** Inspect the
   actual delivery user's role and group memberships through the inventory
   identity. Retain the requested and returned rule sets. The current inspected
   User-type shape is constructed and verified by
   [`lab/zabbix_roles.py`](../lab/zabbix_roles.py). The existing request shape is:

   ```python
   from lab.zabbix_roles import role_rules, verify_role

   expected = role_rules()
   params = {
       "roleids": [approved_role_id],
       "output": ["roleid", "type"],
       "selectRules": list(expected),
   }
   # Execute role.get through the approved inventory identity; retain result.
   verify_role(result, approved_role_id, expected)
   ```

   This fragment does not connect to an endpoint or authorize a role update.
   Check the API allowlist and default/access flags, every enumerated action/UI
   rule, role type, and separately the effective host/group permissions. The
   expected source methods are `event.get`, `event.acknowledge` and `host.get`;
   only acknowledgement/comment actions are enabled. Missing, duplicate,
   broadened, ambiguous or previously uninspected rules require a failed or
   blocked disposition and review, not a relaxed comparison. A different
   installed-version rule catalogue needs an explicit source/policy assessment.
   Setting `actions.default_access` to zero alone is insufficient. Do not
   automatically rewrite the installed role to make the check pass.

3. **Establish positive source visibility.** Using the actual delivery token,
   read the exact approved original event and its complete host set with
   `event.get` and `host.get`. Confirm the frozen event/trigger/host/group and
   customer/instance mapping. No latest-event substitution is permitted.

4. **Establish negative visibility independently.** The read-only witness first
   proves that each outside-scope event/host exists and lies outside the approved
   group set, while also being able to see the in-scope fixtures. The restricted
   delivery identity must then receive the expected valid empty visibility
   result for those same outside objects. An empty lookup without a witness,
   generic request failure or malformed request is not permission proof. Record
   the examined object pairs and all untested tenant/cohort combinations.

5. **Exercise the permitted acknowledgement through the installed path.** On a
   fresh approved synthetic occurrence, use the authorized human's normal
   single-alert `/action` route. Retain the history head, capture/action/source
   identities, verified authority, source write outcome and final projection.
   Confirm precisely the intended event and source effect. Exercise action 2
   or action 6 according to the approved comment policy; do not broaden that
   policy for a test. The independent witness is never used for writes.

6. **Exercise authorized negative boundaries.** Use distinct identified
   synthetic canaries and the approved mutation matrix. Check outside-scope
   acknowledgement refusal and explicit excluded-action role denials, including
   suppression, close and severity changes where the inspected installed
   contract applies. For A4, a generic host-rights rejection does not establish
   the action-role restriction. The supplied regression specifically requires
   the upstream suppression-permission denial. Record the actual structured
   error and unchanged source state. On an unexpected side effect, stop further
   writes, retain the failure and use only the pre-approved recovery procedure;
   do not change grants or silently reset and rerun.

7. **Verify the Alerta side of G5.** Exercise permitted human/customer scope,
   outside-customer refusal, ingestion/service identities that must not become
   human actors, and every approved administrative exception. Verify rejected
   actions produce no unintended capture, delivery or source effect. Record
   both positive and negative coverage for the actual authentication/customer
   settings. A passing A4 source-role test alone does not close G5.

8. **Inspect cleanup and retain the result.** Record final source, alert and
   operational state, authorized cleanup and any remaining effects. Retain
   original failed/interrupted attempts separately from successful replays.
   Verify the private archive and its durable backup before discarding scratch
   records. Obtain the appropriate technical review of findings and installed
   permission conclusions; separately record any required formal approval.

Zabbix shares acknowledgement and unacknowledgement capability. The product
client refuses action 16; the upstream token is not inherently restricted to
action 2. Inventory and protect it accordingly. Never claim an upstream
unacknowledgement denial from the application restriction or unacknowledge
source events merely to reset a test. Generate fresh synthetic occurrences.

## Evidence record and execution limits

For each clause, retain the exact source/base/tested revisions and installed
artifact hash; environment/instance identities; actor and credential references;
approved change; command or UI sequence; start/end times and exit status;
expected/observed outcome; structured redacted observations; cleanup; evidence
hashes, storage and backup receipt; reviewer, reviewed SHA and scope. Use the
deployment procedure statuses `PASS`, `FAIL`, `NOT_RUN`, `BLOCKED_ENVIRONMENT`
or `MANUAL_GATE_PENDING`. A partial result must identify its untested clauses.

The existing `tests/real_zabbix` runner deliberately accepts only its disposable
literal-loopback fixture and synthetic credentials; see
[real-Zabbix lab boundaries](../lab/real_zabbix.md). Do not direct it to an
installed account or weaken its endpoint guard. Likewise, the existing
[read-only collector](../collectors/README.md) does not perform `role.get` or
acknowledgement writes. Use this reviewed manual installed-environment procedure
until any separate tooling and its scope are explicitly authorized.

Successful results establish only the examined installation, account, cohort
and clauses. Keep A2 transport negatives, T2 all-node/site coverage, T27 actual
update-operation effects, T30 deployment measurements, A5 and the other G1-G8
requirements open until their own evidence is accepted. This document does not
change `deployment_gates.csv` or authorize production execution.

Reviewers are assigned per task ([agent workflows](agent-workflows.md#review)).
Consume substantive findings from all reviewers; an unanswered optional request
alone does not block other authorized work. Implementation validation and
same-account technical review are distinct from independent approval. Native x64
qualification of this procedure has not been performed and is never `PASS` by
default; ARM64 evidence does not establish x64 compatibility. Publication,
merging, release and deployment each retain their separate requirements and
authorization under [beta readiness](BETA_READINESS.md) and
[release preparation](RELEASING.md).

Resume installed-account execution when the input table is complete for an
approved target, containment and resource ownership are confirmed, the exact
candidate is fixed, and the specified canary operations are authorized. Until
then, report the missing named input and continue eligible independent work.
