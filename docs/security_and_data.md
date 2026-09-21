# Security boundaries and records handling

This implementation's trusted boundary includes the authenticated Alerta operator within approved customer/cohort scope, the application/signing host, the verifying worker, HMAC key custodians, and restricted database function owners. It does not create a tamper-proof historical ingestion-lineage store. T11a rejects out-of-scope event/trigger substitutions; T11b evaluates a valid in-scope selection under the expressly accepted trusted-operator policy. Top-level customer and authenticated customer scope are authority, not `attributes.customer` or an API key's display owner.

## Cryptographic and effect boundary

Request-local intent is staged before final save; only the signed marker on the newly committed Ack history plus its captured intent can authorize work. The HMAC canonical schema, size limits, key IDs and constant-time comparison are implemented in `crypto.py`. Keys live in protected external files, never PostgreSQL. Signing/proof text must stay out of provisional notifications, ordinary logs and exception responses; the required application adapter and source-backed A1 are part of deployment, not optional hardening. Postcommit internal history legitimately contains the proof under existing access controls.

Invalid/malformed/forged candidates receive verification leases only. They may not resolve endpoints or credentials or occupy delivery slots. Action/effect uniqueness is admitted only after verification. Delivery serializes each exact source event and aggregates per-instance capacity in PostgreSQL. HTTP timeouts, lease expiry and cancellation do not prove remote cancellation; unknown outcomes require read-only reconciliation. The system reduces duplicate comments but does not promise exactly-once remote effects. An already-acknowledged pre-read is observation, not attribution.

## Privilege boundary

| Principal | Authorized interface | Explicitly excluded |
|---|---|---|
| Alerta runtime / actual `alerts` owner | Existing application alert persistence; trigger execution | Direct ledger writes via new broad grants |
| `zabalack_owner` (NOLOGIN) | Own Zab-al-ack schema/functions/tables; minimum underlying Alerta SELECT columns plus UPDATE(attributes) | Alerts ownership, direct update of status/severity/history/customer, trigger administration |
| Trigger installation principal | Separately approved CREATE TRIGGER rights; physical enable/disable only with actual table-owner authority | Worker/probe inheritance of installer authority |
| `zabalack_worker` | Restricted claim/verify/complete/reconcile/projection/status function facade | Direct Alerta table writes, signing-key DB storage, migration, bypass/trigger controls |
| `zabalack_probe` | CONNECT, schema USAGE, capture_health EXECUTE | Inherited role privileges, general table reads/writes, controls |
| `zabalack_operator` | Audited selected pause/resume/retry/repair/policy/bypass functions | Arbitrary SQL/self-grants/physical trigger administration |

`UPDATE(attributes)` cannot restrict JSON keys for the trusted owner. The callable projection function enforces its approved namespace and preserves source keys; T38 proves that behavior separately from grants. No database function stores HMAC material. Controlled search paths and PUBLIC EXECUTE revocation must be checked under exact installed roles; a superuser-only test does not establish the boundary. Refer to `final_review_notes.md` for the complete privilege/bypass interpretation.

## Data inventory

| Layer | Contents / access | Deployment policy still required |
|---|---|---|
| Restricted capture | Frozen IDs/customer/source, minimal new-history metadata/login, intent/marker proof context; no full alert/history/note | Classify cryptographic/person references, restricted readership, capture retention and legal hold |
| Verified ledger / attempts / audit / dead letters | Opaque action/effect/actor references, target and policy IDs, bounded reason/outcome evidence | Approved operators, minimum residence/retention, immutable audit policy |
| Actor reference mapping | Restricted person association where retained by approved identity owner | Named owner, access audit, lawful retention/deletion |
| Alerta history | Operator note plus committed internal proof according to upstream history semantics | Existing customer/operator access review; no public export of proofs |
| Alerta projection | `zabbix_ack_sync*`, reason, action/source generation; no public attribution | Existing UI permission review and source-key preservation |
| Logs / monitoring / inventory export | Allowlisted identifiers and reason classes; no raw credentials, note, proof, entire intent or person directory | Approved redaction, log access/retention and incident recipients |
| Backups / replicas | Includes Zab-al-ack schema/functions/grants and sensitive captures | site-A/site-B residency, backup access, restore tests, legal hold and deletion mapping |

The mandatory adapter's log filter holds the logging row of that inventory: besides redacting records that carry restricted capture evidence, it blanks Alerta's `ContextFilter` extras `data` (the raw request body, which carries the operator's note) and `reqargs` on every record and drops the query string from `url` (a reader search filtering on the staged intent attribute carries the intent value there, and Alerta's default `LOG_METHODS` logs GET requests), so `LOG_FORMAT=json` cannot emit them; the request path is kept.

No retention cleanup or destructive rollback is enabled. G8 must map each layer to a ratified classification, access, residency, retention, legal-hold and deletion policy before enabling any cleanup. A row omitted from an export is not deleted from authoritative evidence. The collection envelope sets `deployment_gate_passed=false`; summaries cannot replace restricted full export review when proving update-side effects or effective routing.

## Deployment safety review checklist

Use `docs/INDEPENDENT_A8_REVIEW.md` (historical) and `docs/RENAME.md` (current namespace changes) for the performed independent review and its disposition; this document describes required boundaries. Check source pins/amendments, adapters and log handlers, strict config/endpoint allowlists, CA verification, secret-file ownership, no credentials in command lines, absence of production endpoints/cohorts, migration checksums, exact-role grants, atomic Ack/capture rollback, generation-safe projection, abandoned-send reconciliation, maintenance controls and empty-queue behavior. All real tenant permissions, macro expansion, canaries, HA and fleet consistency remain deployment evidence until explicitly executed and recorded. Do not transform a mock into a passed gate.
