# Zab-al-ack development contract

Work in development and explicitly isolated synthetic labs. Local fixture tests,
repairs and reruns needed for the requested task are authorised without repeated
confirmation. Complete the requested implementation, verification and handoff.
Production access, publication, remote pushes, notifications and credential
changes require explicit user authorization; repository guidance grants none.

Preserve release one: authorised human single-alert `/action`, atomic PostgreSQL
composite-history capture, signature verification before target access, separate
verification/delivery leases, bounded delivery, read-only reconciliation and
restricted projection. No broader grants, bulk/chatops routes or SQLite substitute.
Production examples stay disabled with empty cohorts and endpoint maps. Never
export private inventories, signing proofs, secrets or raw operational logs.

Read what the task needs:

- Runtime/trust/SQL changes: `docs/DESIGN.md`, `docs/final_review_notes.md` and
  `docs/SPEC_AMENDMENTS.md`. Name the affected invariant and test wrong behaviour.
- Migration or rollout review: `.agents/skills/zabalack-migration-review/SKILL.md`.
- Test/lab execution or recovery: `docs/agent-workflows.md`; use the isolated-lab
  skill for native services. Keep source, harness and dependency pins reviewable.
- Candidate export: `.agents/skills/zabalack-release-review/SKILL.md` and
  `docs/RELEASING.md`. Regenerate acceptance only after the candidate's final runs.
- Ordinary documentation edits: read affected pages and verify links/examples;
  broader tests are needed only if the change affects executable behaviour.

Use focused regression tests for fixes. Required candidate checks remain in the
release guide. Do not weaken assertions, broaden Any or add suppressions to pass.
Keep unit/mock, PostgreSQL WASM, native services and deployment evidence distinct.
Timeouts/import errors/skips are not mutation kills. G1–G8 and amendment A2 remain
deployment gates. Never replace an older run's digests with the current tree's.
Retain failed/interrupted attempts; a later success is a new run. Focused reviews
report their own scope and leave unrelated acceptance/evidence records intact.
