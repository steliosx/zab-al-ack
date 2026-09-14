---
name: zabalack-migration-review
description: Create or review Zab-al-ack PostgreSQL migrations and their rollout. Use for migration changes or installation and rollback reviews.
---

Read [design](../../../docs/DESIGN.md), its
[final interpretations](../../../docs/final_review_notes.md),
[source amendments](../../../docs/SPEC_AMENDMENTS.md),
[deployment](../../../docs/deployment.md) and [rollback](../../../docs/rollback.md)
for the affected boundary. Ordinary SQL callers do not require a migration.

Inspect pinned upstream schema and current migration checksums. Shipped/applied
migrations are immutable; add a forward migration for a new SQL correction.
Never adjust a stored checksum to make an old migration run again.

Keep zabalack_owner separate from the alerts installation principal. Preserve
explicit function grants, controlled search_path and PUBLIC revocation. A JSONB
column grant does not restrict JSON keys. Test actual caller/owner denials in
PostgreSQL; a privileged session or SQLite cannot prove them.

Exercise upgrade and fresh install paths in the isolated lab, including capture,
lease fencing and projection regressions affected by the change. Record narrow
source incompatibilities as amendments with affected tests. Retain pending work
on rollback, stop outbound writes first, and never erase the ledger or send a
compensating Zabbix unacknowledgement. Deployment requires its own G1–G8 evidence.
