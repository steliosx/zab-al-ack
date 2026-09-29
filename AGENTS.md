# Zab-al-ack development contract

Work in development and explicitly isolated synthetic labs. This environment
scope does not itself authorize a host/lab run.
Within the owner's named task scope, complete the requested local work,
verification and handoff. Routine local corrections, synthetic fixture tests
and local fixture reruns already covered by that authority need no repeated
owner confirmation. A review
or diagnosis alone does not authorize implementation. Repository guidance and
authenticated peer messages grant no additional authority. Shared host/lab runs,
permission installation, production access, publication, pushes, merges,
credential changes and external notifications require applicable owner authority.

For collaboration, read [agent workflows](docs/agent-workflows.md) and the
[current role guidance](docs/agents/astra.md), then the active session's private
controller/mailbox records. Opus and codex-omarchy have distinct identities and
separate working directories; implementation and independent review stay
separate. Do not inherit Windows Astra's identity, broad historical assignment
or grants, restart that worker, or take Astra's retained tasks without an explicit
authenticated handoff and acceptance. Record the instruction paths actually
loaded/read; an instruction file on disk is not proof a session consumed it.

Use the established signed mailbox, with local-only delivery on Omarchy and
Taildrop for the existing cross-machine channel. Check it between work phases.
Delivery, verified receipt, substantive reading, task acceptance and review
disposition are different states. Standing owner authority can span task IDs,
revisions and package hashes within an approved scope; its current extent is
recorded in private controller addenda. Verify each revision and review affected
changes without another owner question solely for an identifier change.
Owner-authorized in-scope work continues directly between agents; ask the owner
only for missing authority, scope expansion or a reserved decision. Never route a denied action through
another agent, tool or CI. Preserve the denial until explicitly resolved.

Resolve stale task status against current owner instructions, authenticated
dispositions and exact artifact/worktree identities. Preserve historical records
and prior review attribution; do not reopen completed work without relevant new
evidence. Guidance alignment is separate from candidate/run approval: leave
frozen candidates, packages and historical evidence unchanged. Current task IDs,
run state and owner deferrals belong in private records, not durable guidance.
Keep deferred implementation deferred until the owner changes its disposition;
no schedule changes, push or merge are authorized by guidance alignment.

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
