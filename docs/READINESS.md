# Readiness by decision boundary

This table is the short status view for reviewers. It deliberately separates
whether a change can be merged, whether an alpha can be released, and whether a
specific installation can be enabled. A result in one column is not evidence
for another column. Detailed candidate results remain in `ALPHA_REPORT.md`, and
the executable deployment checks remain in `deployment_acceptance.md`.

| Boundary | Required evidence | Current status | Owner / next action |
|---|---|---|---|
| Merge | Focused regression checks for the change; required CI jobs at the proposed head; review of the actual diff and head SHA. | **Needs current-head CI/review.** The scheduled scan and prior candidate records are inputs, not a pass for a changed head. | Change owner supplies the head SHA and CI links; reviewer checks that exact head. |
| Alpha release | All candidate commands in `RELEASING.md`; source-bound quality, reference, mutation, property and applicable service records; dependency/security review; reproducible verified artifacts; reviewed disclosure of every unavailable layer. | **Not ready for publication.** Historical a16 records are retained, but hosted Actions, Compose labs, CodeQL, the candidate's own Gitleaks run and signed attestation remain unexecuted. Publication-owner inputs are also unset. | Release owner reruns the final candidate and completes `config/publication.pending.json`; independent reviewer verifies the resulting artifacts. |
| Installed environment | G1–G8 and amendment A2 against the approved installation, including effective-role and tenant-boundary tests, exact identities, transport controls, rollback and records ownership. | **Blocked on deployment evidence.** Every gate remains `MANUAL_GATE_PENDING`; local, WASM, mocked and synthetic-lab passes do not close them. | Deployment owners provide the authorised inventory and execute `deployment_acceptance.md`; Opus owns any runtime/SQL repair exposed by those checks. |

## Evidence handling rules

- Record the candidate head and base SHAs with every review. A changed head
  requires fresh assessment of the affected checks; do not cite a green run
  from an ancestor as current-head CI.
- Treat `NOT_RUN`, skipped cases, stale scans, setup errors and interrupted
  sessions as gaps. They are never converted to passes by a later unrelated
  run.
- Keep fixture, PostgreSQL WASM, native-service, real-Zabbix and installed
  deployment results separate. State precisely which layer a command exercised.
- Do not regenerate acceptance or package a candidate until its final required
  runs are complete and digest-bound to the same implementation and harness.

## Recurring root cause

The recurring release risk is **evidence-domain conflation**: a correct local
result is easy to describe as if it covered hosted CI, a container path, or an
installed permission boundary. Digest binding prevents stale bytes from being
silently adopted, but it cannot correct an over-broad human claim. Reviews must
therefore name the tested head, harness, execution layer and unavailable scope.
Disagreement should be escalated with the reproducer, the competing gate
interpretations and the exact release or deployment decision required.
