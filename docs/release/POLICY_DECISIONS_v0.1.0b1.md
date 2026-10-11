# Three release-policy decisions for the beta (brief for the owner)

Prepared 11 October 2026. Each section states what the decision is, what each option
costs, and a recommendation. Record the decision in the table at the end.
Nothing here waives an existing requirement silently; a waiver must be written.

## 1. CodeQL

**What it is.** `.github/workflows/codeql.yml` runs GitHub CodeQL
(security-extended queries, Python and JavaScript/TypeScript) on every push,
pull request and weekly. The job is gated by the repository variable
`ZABALACK_CODEQL_ENABLED`; when the variable is not `true` the job is
visibly skipped. Today every run shows SKIPPED.

**Enable.** Set one repository variable to `true`. Cost: about 5–10 hosted
minutes per run; code-scanning alerts appear on the repository's Security tab.
Risk: new findings may need triage before the tag. Benefit: the beta ships
with a static-analysis result instead of a skipped job.

**Waive.** Record in `docs/RELEASE_AMENDMENTS.md` that the beta ships without
CodeQL and why. Cost: a visible gap in the release notes and the readiness list.

**Recommendation: enable now.** It is one variable, the repository is private
to you today, and a first result before the tag is cheaper than a first result
after it. Run once by `workflow_dispatch`, triage, then decide on the tag.

## 2. Signed attestation and second-platform reproduction

**What it is.** `release/attest.yml.example` is an inactive template. On a
manual dispatch for a `v*` tag, gated by `ZABALACK_ATTESTATIONS_ENABLED`,
it rebuilds through the normal CI, then signs the same-run wheel with GitHub's
`actions/attest` (SLSA provenance v1) in a protected environment named
`release-attestation`. `tools/verify_attestation.py` verifies the bundle
offline. Enabling it writes to the public Sigstore transparency log.
"Second-platform reproduction" means rebuilding the wheel on a second,
independent platform and matching the hash; the docs name it as an option, not
a defined procedure.

**Require for beta.** Cost: move the template into `.github/workflows`,
create the environment with required reviewers and tag restrictions, set the
variable, run once, verify. About half a day of reviewed work plus a public
log entry. Benefit: consumers can verify the wheel came from this repository's
workflow at this commit.

**Defer to the first stable release.** Cost: the beta wheel has local
provenance (`BUILD_PROVENANCE.json`, two-build hash comparison) only. The
readiness list already states this is a stable-release requirement.

**Recommendation: defer attestation and second-platform reproduction to
stable.** Record the deferral in R24. The beta is evaluation-only and
unpublished to PyPI; the public log write is better made when the publishing
identity is final.

## 3. Supported-environment statement

**What it is.** The release must say which environments the beta is for. The
documents today say: Python 3.12; Alerta 9.1.0; PostgreSQL 18.3 (pin 18.0);
PgBouncer 1.24.1; Zabbix 7.0.10 lab target; Linux x86_64 reference, other
platforms unvalidated. Native x64 qualification on an authorised host is
deferred; the hosted CI native pass is run evidence only.

**Option A: state exactly that.** Cost: none. The statement is true today.

**Option B: wait for the SmolVM x64 lab, then state "native x64 qualified".**
Cost: the beta waits for a lab that is not built yet (sizing estimate exists).

**Recommendation: Option A for the beta.** Ship with the true statement. Add
the x64 lab result in a point release when it exists. One housekeeping item:
`docs/release-provenance.md` lines 37–38 still say routine CI targets the
ARM64 self-hosted runner; correct that sentence in the same PR as the decision.

## Decision record (owner fills in)

| Decision | Choice | Date (Nassau) | Recorded in |
|---|---|---|---|
| CodeQL | enable / waive | | R24 |
| Attestation + second platform | require / defer | | R24 |
| Supported environments | A / B | | R24, release notes |
