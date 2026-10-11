# Zabalack 0.1.0b1 — beta candidate (draft release notes)

Draft prepared 11 October 2026 for owner review. Status: **unpublished candidate**. This is
a beta for development and isolated evaluation. It is **not qualified for
production deployment**; see "Open items" below.

## What Zabalack does

Zabalack carries one human acknowledgement from Alerta back to the original
Zabbix problem event. One authorised action in Alerta creates a durable record,
a worker delivers it to Zabbix, the result is projected back, and a reconciler
settles late, duplicate or ambiguous outcomes. It replaces the fire-and-forget
upstream plugin with a ledger that can answer "did it acknowledge, or not".

## Identity

| Item | Value |
|---|---|
| Version | 0.1.0b1 |
| Commit | `3c3c0dfbf02f74ae3abe38ec136037afabde48b5` |
| Wheel SHA-256 | `pypi/zabalack-0.1.0b1-py3-none-any.whl: OK
c88dee99900b0da9761cbec082745c45ed8f81e5163b9ca82fa3d619f8d57a9d` |
| sdist SHA-256 | `pypi/zabalack-0.1.0b1.tar.gz: OK
830bcbe654092874451c8cdb005c7be529a9dc6b76a12340997eba3dce1be715` |
| CI run | 38147339640 (GitHub-hosted ubuntu-24.04) |

## Tested environment

| Component | Version |
|---|---|
| Python | 3.12 |
| Alerta server | 9.1.0 |
| PostgreSQL | 18.3 (reference pin 18.0) |
| PgBouncer | 1.24.1 |
| Zabbix | 7.0.10 lab target (7.0.0 reference) |
| Platform | Linux x86_64 in a Docker lab on a hosted runner; other platforms unvalidated |

## What was executed for this candidate

- Reference suite: 1,503 passed, 47 skipped, 0 failed.
- Native lab (PostgreSQL 18.3 + PgBouncer, five phases): 311 passed, 0 failed.
- Mutation gate: 12 mutants killed, 0 survived.
- Public export audit, reproducible build and acceptance mapping: passed.
- Dependency advisories: 58 exact pins, 0 advisories. Secret scan of source and
  history: 0 findings.

## Changes since 0.1.0a17

- Dependency bumps: PyJWT 2.15.0, urllib3 2.8.0, Werkzeug 3.1.9 (18 advisories cleared).
- All active workflows run on GitHub-hosted `ubuntu-24.04` while the dedicated
  ARM64 runner is held. Actions pinned to Node 24 releases.
- Public export allows exactly the two README hero images.
- Every non-200 response to a sent Zabbix write is now treated as uncertain (internal review F2).
- Minimal publication inputs recorded (repository, package owner, primary maintainer).
- No runtime feature change. No SQL body change. See `CHANGELOG.md`.

## Open items (disclosed)

These are open at this release. Each is tracked in `docs/BETA_READINESS.md`.

An internal adversarial review of this revision (11 October 2026, retained privately,
SHA-256 0bd8c5a9…) found no path for an unauthorised actor to acknowledge or close an
event and no credential exposure. Its finding F2 (non-200 write responses recorded as
definite failure) is fixed in this revision (PR #40, merged into `3c3c0dfb`); F3 was disproved against Alerta
9.1.0; F1 is disclosed below. The external review (xAI Grok 4.7) starts after the
repository is public.

| Item | Meaning | Status |
|---|---|---|
| CodeQL | Static analysis workflow | Run 38144863693 **FAILED at ingestion** (11 Oct); all other runs skipped; gate set back off until the repository is public, then re-enabled and triaged before the tag; **no clean scan yet** |
| Signed attestation | SLSA provenance of the wheel via GitHub attestations | Template supplied, **not run** |
| Native x64 qualification | Bounded native run on an authorised x86_64 host | **Deferred**; hosted CI native pass is run evidence only |
| A2 | Reference webhook transport does not authenticate TLS peers | **Open**; do not deploy the reference webhook as shipped |
| A4/G5 | Permission tests on the real installed Zabbix account | **Not executed** |
| G1–G8 | Deployment gates (inventory, ingestion contract, cohort, ownership, records) | **Planned, not verified** |
| Independent security review | External review of this exact revision | **Pending** |
| Internal review finding F1 | An Alerta principal with write access for the customer may edit an alert's Zabbix source attributes before acknowledging, retargeting the acknowledgement to another event of an enabled cohort (depends on Alerta's attribute-update path, unverified) | **Disclosed**; fix planned: bind alert to event at ingestion |

## Upgrade and compatibility

Forward migrations 001–012 apply in order; migration 010 requires explicit
resealing and the runtime health gate. No migration bytes changed in this release.

## Reporting a problem

Use the repository's private vulnerability reporting once enabled (see
`SECURITY.md`). Do not include credentials, signing proofs or operational endpoints.
