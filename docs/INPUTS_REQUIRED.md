# Consolidated deployment-input register

Nothing in this register was required to build synthetic reference artifacts. All entries remain **required before deployment enablement** unless an accountable reviewer records narrower approved scope. G1–G8 are `planned/NOT_VERIFIED` in `deployment_gates.csv`; no lab fixture satisfies them. Provide protected secret references only. Do not paste tokens, passwords, HMAC keys or raw private alert histories into chat.

| Input | Gate | Expected redacted format | Acquisition / owner | Why needed |
|---|---|---|---|---|
| Alerta server/UI full revisions, Python/dependencies, installed alarm model | G1 | Package versions, UI SHA, model module/revision, supported-route traces | `collectors/collect.py local`; engineer supplies UI artifact hash and runtime route tests | Match inspected hooks, save order and repeat-Ack 409 |
| Authentication/customer views and ingestion identities | G1/G5 | Booleans; opaque key reference → top-level customer references and scope list; no keys | Static collector plus restricted auth/config inspection and customer negative tests | Reject privileged/mismapped ingestion and nonhuman actors |
| Human customer authority and administrative exceptions | G1/G5 | Opaque actor/customer scope examples; approved exception policy revision | Identity/security owner; actual authenticated tests | Verify customer access without treating key-owner labels as people |
| Effective plugin routing/order and observers | G1/G4 | Per-cohort effective sequences; plugin versions; precommit side-effect/logging review | Runtime routing trace and reviewer inspection | Signer must be final effective status callback; prevent proof exposure |
| Database identity/routing/ownership | G1 | PostgreSQL version, logical deployment ID, database alias, app/probe routing map; role/grant/inheritance catalog | PostgreSQL collector; DBA observes each application/probe connection path | Single writable primary, separate roles, no forced-user collapse |
| PgBouncer / Patroni topology and probe capacity | G1/G6 | Release, pool mode and per-user min/max/reserve; API process counts; aggregated pool/client/server stats; failover path | Platform engineer, no config imports or secrets in export | Bound total connections; test 500ms statement timeout and clean reuse |
| Zabbix logical-instance and endpoint registry | G1/G5 | Instance ID → reviewed API version, HTTPS endpoint, CA file reference, token reference/expiry; no token | Per-instance owner; selected read-only collector | Distinguish database identity from site; TLS and approved version |
| Webhook transport authentication and redirect containment | G1/G2 | Installed source/build hash, reviewed remediation/control, negative certificate/hostname and redirect tests; no secrets | Zabbix/platform/security owners; SPEC_AMENDMENTS A2 | Reference 7.0.10 and current artifact on inspected 7.0.30 default to disabled TLS peer/host verification and follow redirects; HTTPS syntax is insufficient |
| Current media-type export and real source facts | G2 | Restricted export + redacted problem/update/recovery/symptom payloads and hashes; top-level customer mapping | Current Zabbix export locally reviewed; payload collector | Prove original problem IDs, cause/recovery handling and legacy behavior |
| Matching action/update-operation inventory | G3 | Selected actions and all matching recipient/media/script/email/command branches; documented exceptions | Zabbix collector summaries plus restricted complete export review | Prevent unintended update notifications; preserve genuine human updates; cover action=2 separately |
| Pilot cohort approval | G4/G5 | `(customer, logical_instance, trigger_id)` → complete host-ID and group-ID sets, revision, one-to-one assertion, comment policy | Monitoring/customer owner; selected trigger/host export | Exact full host-set validation; no automatic host approval |
| Trigger generation and Alerta correlation | G4 | Single/multiple problem mode; correlation/fan-in rules; exclusions; stable event/resource mapping | Monitoring and Alerta owners; trigger collector plus rule review | One original event per eligible alert; stable family identity |
| Secret/deployment references and staging targets | G1/G7 | Private inventory; exact DB role names, approved service identities, approved secret-manager references, protected file paths, CA chain, HMAC key IDs | Platform/secret owners | Deploy safely with variable targets and no secrets committed |
| Migration and trigger installation identities | G1/G6 | Actual alerts owner; Zab-al-ack migration membership; installer authority; operator/probe/worker rights; change record | DBA inventory + exact-role T35/T38/T39 | Separate Zab-al-ack-object migrations, bypass flag and physical trigger control |
| Token permissions / authentication canary | G5/G6 | Effective role.get rules, explicit excluded-action denials, positive/negative tenant tests for event.get, event.acknowledge and host.get; known synthetic event record | Monitoring/security owner in approved staging; SPEC_AMENDMENTS A4 | Default-access settings alone do not revoke existing rules; acknowledgement and unacknowledgement share an upstream role capability |
| Measured fleet / failure / batch acceptance | G6 | T1–T39/T11a/T11b results; 50/200 per-alert batches with queue, PHP-FPM, DB and fairness data; HA fencing and pool traces | Platform/integration test lead; deployment_acceptance.md | Prove performance, all-node consistency, unknown-outcome recovery |
| Operational owners and responders | G7 | Named primary/backup per role, approved incident destinations, escalation, expiry owner, maintenance/bypass principal and change workflow | Operations management | One incident per instance/failure class; no unowned queue or emergency bypass |
| Records policy | G8 | Classification/access/residency/retention/legal-hold/deletion matrix for captures, actor mapping, ledger, diagnostics, logs and backups | Data/security/records owner | Enable approved retention; capture includes proof/login context and crosses replication boundaries |
| Approved production enablement and source conflict resolution | G5/G6/G7 | Signed release/change record, resolved SPEC_AMENDMENTS entries, reviewed configuration hashes, fleet checks | Accountable release owner | This package build does not authorise production changes or prove gates |

Role aliases in this reference build are fixed: `zabalack_owner`, `zabalack_worker`, `zabalack_probe`, `zabalack_operator`. A site with different naming must supply an approved mapping and rerun exact-role tests; do not broadly grant privileges to make deployment work. Deployment inputs can remain explicit placeholders until the relevant gate is exercised.

## First public release inputs

These publication inputs are separate from G1–G8 and do not require live
inventory or production credentials. Track assignments in
`config/publication.pending.json`.

| Input | Current state | Needed to publish |
|---|---|---|
| GitHub organisation/repository and PyPI owner/namespace | Unassigned | Confirm ownership/availability and add real project URLs |
| Primary/backup maintainers and private contacts | Unassigned | Verify security-report recipients and conduct-report contact |
| Rights and public-content review | Pending | Approve project copyright, upstream notices and curated source/history |
| Standalone secret scan and Actions CI | Local scan records remain local; dedicated Linux ARM64 self-hosted workflow routing is not execution proof | Obtain Opus review of the explicit execution-policy change, then retain authentic final-commit Actions allocation and producer-to-package artifacts with no hosted fallback; no external security-review claim |
| Explicit public upload authorization | Not given | Approve the reviewed commit/tag and publication destination |

The stable-release roadmap additionally requires independent external security
review and native/real-source integration evidence. No named owner, public
repository, vulnerability reporting channel or deployment sign-off is invented.

## Additional a9 transition input

If Ackline was ever installed, supply the retained release/database inventory,
backup/restore evidence, old-writer and in-flight fencing plan, records-policy
references, fresh Alerta database identity, new role/config/key references and
an approved exclusion preventing two writers serving one cohort. Do not provide
secrets. Same-database in-place migration is not supplied; see `RENAME.md`.
