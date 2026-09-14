# Zab-al-ack — alpha brief

**0.1.0a16 · formerly Ackline · for isolated evaluation.**

Zab-al-ack connects **Zab**bix, **Al**erta and **ack**nowledgements. The technical
identifier is `zabalack`. It is an independent integration, not an official
Alerta or Zabbix product. Public repository/package ownership is being prepared.

An authorised human acknowledges one eligible Alerta alert. PostgreSQL captures
the signed request with that acknowledgement. A worker verifies it, checks the
original Zabbix problem and approved customer/instance/trigger/host scope, then
delivers a bounded acknowledgement. A reconciler investigates uncertain outcomes
without blindly repeating a write. Alerta shows a projection of the ledger.

## What operations teams get

- Fewer repeated acknowledgements between consoles.
- Explicit customer and source boundaries, with a mandatory source pre-read.
- Durable capture, action and delivery records, plus separate bounded leases.
- Audited pause, inspection and exception controls.
- A background reconciler for uncertainty and projection repair.
- Self-hosted Python plugins, SQL, workers, CLI and deployment tooling.

This is not automatic problem resolution, two-way acknowledgement mirroring or
an exactly-once delivery guarantee. Bulk actions, chatops, arbitrary status
changes and multi-event alerts remain outside the alpha. Installing the wheel
does not activate write-back. Production examples contain no active cohort,
endpoint or external-write permission.

## What a9 renames

The package, import and CLI are `zabalack`; Alerta entry points are
`zabalack_zabbix_normalise` and `zabalack_zabbix_ack`. Configuration uses
`ZABALACK_CONFIG_FILE`. The database schema and role/service prefix are
`zabalack`. The public mark, site and docs use Zab-al-ack.

The HMAC domain and history marker now use the new namespace. Old proofs cannot
validate, including one with a relabelled visible prefix. Both proof formats
still receive redaction; legacy reserved input is stripped. The media artifact
suppresses both exact neutral comment formats before endpoint/credential lookup.
Source-contract version 1 and `zabbix_ack_sync*` projections remain stable.

**Fresh installation namespace, not an in-place Ackline upgrade.** Installers
refuse a database containing `ackline`. Retain its original release, ledger,
verification material and migration checksums. Fence old writers and account
for in-flight requests under an approved operational change. Qualify a fresh
Alerta database and new configuration before any pilot; do not run both writers
for one cohort or rewrite historical captures. Same-database continuity needs
a dedicated state-preserving upgrade, which a9 does not supply.

The complete ZIP contains `docs/RENAME.md`, the cumulative changelog, amendment
R13, old/new SQL hashes, unchanged historical artifacts and rollback guidance.

## Engineering upgrades retained

1. Execution-owned, source/harness-bound evidence and current-run CI collection.
2. Hash-pinned Ruff and gradual strict mypy at trust boundaries.
3. Generated input and restricted SQL state tests, including the expiry fix.
4. Native crash/race scenarios; executed on the a12 tree (five scenarios passed).
5. Curated mutation checks that count assertion failures, not errors/timeouts.
6. Installed service-lab tooling; executed on the a12 tree from the installed wheel (local services; Compose form open).
7. Optional, commit-pinned CodeQL; hosted execution is NOT_RUN.
8. Archive/wheel/evidence verification and inactive signed-attestation tooling.

The a8 review's strict deployment-evidence validator remains in place. Digests
show consistency with source; they do not authenticate the report's author.
Historical Ackline results are retained separately and are not a16 evidence.

## Fresh a16 checks

| Layer | Result | Scope |
|---|---|---|
| Reference suite | 865 passed / 23 skipped / 0 failed | 836 unit/mock/property/offline and 29 PostgreSQL WASM passes |
| Additional generated profile | 46 passed | Nightly profile; overlaps reference cases |
| Curated mutations | PR 12/12; nightly 20/20 detected | 111-case baseline; Python unit/mock scope |
| Supplemental SQL / webhook | 8 / 15 groups passed | Real WASM SQL engine / synthetic Node payloads |
| Quality | Ruff, gradual strict mypy, pip check passed | Configured source scope |
| Dependency advisory queries | 58 Python packages, zero advisories; npm zero vulnerabilities | Known advisory results, not a security clearance |
| Native PostgreSQL 18.3 / PgBouncer 1.24.1 | 42 passed / 0 failed / 0 skipped | Five fresh clusters through `tools/native_local.py`; local services, not Compose |
| Real Zabbix 7.0.10 | 6 contract cases passed | Source-built server; includes the new A4 case requiring the specific suppression-permission denial for the lab token |
| Independent review's Zabbix 7.0.30 and 7.0.10 layers | Each: 5 contract + 10 supplemental API cases passed | Recorded in the review's own QEMU lab and kept as its evidence, not restated as a13 results |
| Installed-wheel 7.0.10 pipeline | 4 passed, 13/13 observations, cleanup 0 | `tools/run_deployment_e2e_local.py` from the installed wheel; local services, not Compose |
| Hosted Actions, CodeQL, Gitleaks and signed attestations | NOT_RUN | No hosted or cryptographic provenance claim |

All 888 collected cases were selected. Four skips are native-only cases in the
WASM phase; nineteen require optional services. Profiles overlap and must not
be summed. Build reproducibility, fresh-wheel smoke and archive checks are
recorded in the matching ZIP; use its exact checksums.

## New in a15 and a16

a15 verified a14 — every offline result reproduced exactly — and executed the
three service layers a14 recorded as NOT_RUN: 42 native PostgreSQL 18.3 /
PgBouncer cases, six real Zabbix 7.0.10 contract cases and the installed-wheel
pipeline's four tests with thirteen observations. Verification also found that
published evidence records were carrying the 0600 mode of the temporary file
they were written through. Each writer now states its intended mode and the
export audit rejects any exported file that is not readable to group and other.

a16 reproduces and adopts the independent review of a15. The review rebuilt the
artifacts to the same bytes, reran every offline suite to the same numbers, and
found no runtime or SQL defect. It did find five claims in the release prose
that were not true: the mode defect had not reached every handoff (only a10, a12
and a13 archives carried it, because this project's packaging preserves source
modes while the other producer's normalised them); the two profile mutation
records are optional when absent, not required; a15's service runs are a15's,
and unchanged code is grounds for an inference about a14, not a substitute for
its records; the release verifier is not stdlib-only, because it reaches PyYAML
through the guidance validator; and two owner-only records do remain in the
handoff, retained from the a7 packet. All five are corrected here, and the
review's ten writer regressions — both public writers, both private writers,
both umasks, plus replacement of an existing restrictive file — are adopted.


## Deployment remains a separate gate

Reference targets are Python 3.12, Alerta 9.1.0, PostgreSQL 18.3, PgBouncer 1.24.1
and Zabbix 7.0.30/7.0.10. They are development references, not recommended production
patch levels or blanket compatibility claims. Schema version 3 applies to the
new `zabalack` baseline; all seven migrations must be present.

A1 still requires the Alerta privacy adapter and review of installed logging and
observers. Alerta's startup schema ownership needs installed-environment review.
A2 remains open: the reference Zabbix webhook transport disables peer/hostname
verification and follows redirects; an HTTPS URL alone is insufficient. The
worker's HTTPX TLS verification is separate. A3's exact absent-cause macro fix
is retained; review and import the current media artifact before a pilot.

All G1–G8 and publication-owner inputs remain pending. Retain evidence during
rollback; no tool automatically unacknowledges source events. The consolidated
input register and deployment/rollback runbooks are in the source handoff.

Project-owned material is prepared under Apache-2.0; upstream notices remain.
This static website does not connect to monitoring systems, collect signups or
distribute the plugin source. No plugin repository or package has been pushed
or published by this update.


## Independent review correction

A4 fixes the synthetic role provisioner: explicit individual action/UI rules and
effective role.get read-back precede user/token creation. An omitted rule had
retained enabled access even when the new default was disabled. The first real
supplemental failure and the failing original-source regression are retained.
Runtime Python, SQL and wheel bytes remain unchanged; the reviewed source adds
eight permission regressions and a realistic mock, plus two regressions for
the staged dependency and bounded projection observation. Acknowledge/unacknowledge share an upstream
role capability; the client itself allows only acknowledgement actions 2 and 6.

The supplied 7.0.10 screenshot is not a new 7.0.30 capture. A2, G1–G8, Compose,
hosted workflows, signed attestation and external security review remain open.
Emulated native batch timings do not establish production fleet capacity.
