# Maintainers and project scope

This independent project implements the release-one single-event acknowledgement
design. Its alpha purpose is reproducible evaluation and focused compatibility
feedback. There is no commercial support promise or staffed on-call service.

Before public hosting, the repository owner must nominate a primary and backup
maintainer, establish the private security-report channel and record repository
and package ownership in `config/publication.pending.json`. These values remain
explicitly unassigned in this candidate; no fictitious person or address is used.

Maintainers review contributions, explain release decisions, publish accurate
test evidence and triage security reports. Changes affecting authentication,
tenant authority, SQL privileges, proof exposure or ambiguous remote effects
need a second qualified review before a stable release. Document disagreements
and design amendments with the evidence that resolves them.

Before 1.0, Python implementation details and configuration schemas may change
with release notes. Persisted data, SQL migrations and signed wire formats
require explicit upgrade compatibility even during alpha. A later stable
release must publish a support window and maintenance/backport policy.
