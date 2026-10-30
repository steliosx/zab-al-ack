# Zabalack 0.1.0b4 — beta prerelease (release notes)

Prepared 30 October 2026. Status: **published as a GitHub prerelease on 30 October 2026 at 6:31 PM Nassau
(2026-10-30T22:31:57Z)**, with the disclosed exceptions in `docs/release/TAG_PROPOSAL_v0.1.0b4.md`.
**Not published to PyPI.**
This is a beta for development and isolated evaluation. It is **not qualified for
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
| Version | 0.1.0b4 |
| Tagged commit (producer) | `9cff02cb80d98b17d256498797a86c7cb6c1c144` |
| Release documents | not in the tagged tree. These notes and the tag proposal are carried by the documentation PR head and the notes-bearing `main` merge commit, both named in the tag message |
| Wheel SHA-256 | `035f7b69773f01493f9d3bdbcd7683853101b60d5b27294feb06b2f7a046afbc` |
| sdist SHA-256 | `0ca641336501f85f3c587e05fb2ef5c711864cc3b6b37aa650c965b762aabd72` |
| CI run | 41039220769 (daily schedule, nightly Hypothesis profile; GitHub-hosted ubuntu-24.04) |
| Not released | 0.1.0b2 and 0.1.0b3 were candidate identities on `main` only; they are superseded by 0.1.0b4 and get no tag |

## Tested environment

| Component | Version |
|---|---|
| Python | 3.12 |
| Alerta server | 9.1.0 |
| PostgreSQL | 18.6 (reference pin; R25) |
| PgBouncer | 1.24.1 |
| Zabbix | 7.0.25 lab target; the real-Zabbix qualification is **not run** |
| Platform | Linux x86_64 in a Docker lab on a hosted runner; other platforms unvalidated |

## What was executed for this candidate

Run 41039220769 on the tagged commit:

- Reference suite: 1,546 passed, 47 skipped, 0 failed, with the unseeded nightly Hypothesis profile.
- Native lab (PostgreSQL 18.6 + PgBouncer, five phases): all phases passed; the PostgreSQL phase
  ran 209 tests, 0 failed.
- Mutation gate (nightly catalogue): 20 mutants killed, 0 survived.
- Public export audit, reproducible build (wheel, sdist and source archive each byte-identical in two
  builds, build backend setuptools 84.1.0) and acceptance mapping (40 passed, 2 manual gates pending):
  passed.
- Security workflow on the same commit (run 40968133638): 58 exact pins, 0 known advisories; secret
  scan of source and history, 0 findings.

Three further runs on the same commit passed with the same wheel: the merge run 40968133596 (PR
profile) and the ledger soak runs 41013846096 and 41063710126 (nightly profile). Run 41039220769 is the
first daily scheduled `ci.yml` run with the nightly profile that passed on record; the soak run
41013846096, also scheduled with the nightly profile, passed earlier the same day.

## Changes since 0.1.0b1

Ledger correctness (SQL; schema version stays 4; no row is relabelled):

- Migration 013 (R30): the execution-lifetime expiry of `worker_call('reconcile_due')`, and the expiry
  that `operator_call('resume')` applies to held rows, no longer record `expired` while a delivery lease
  is live or a write attempt is unsettled. Before, such a send could be closed as certainly expired, and
  neither `worker_stopped` nor `settle_unknown` could then reach it. Found by the scheduled nightly
  profile; direct SQL regression T43.
- Migration 014 (R31): `worker_call('claim_delivery')` runs its abandoned-attempt fence before its
  lifetime-expiry branch, and a claim past the deadline hands out no lease and leaves the action
  uncertain for settlement. Found by a local nightly pass; direct SQL regression T44. Whether this is the
  native failure that the first 0.1.0b3 nightly showed is not established; scheduled nightly runs on
  0.1.0b4 have passed since.

Toolchain and dependencies:

- Build backend setuptools 83.0.0 → 84.1.0 (GHSA-xmj3-9gf7-h52w).
- brotli 1.2.1 and certifi 2026.10.19 (GHSA-9v8h-qqgm-4m35, GHSA-6q48-6c7w-9gw2).
- The release and attestation verifiers accept explicit beta versions and reviewed beta tags.

Lab, CI and documentation:

- Zabbix lab reference 7.0.10 → 7.0.25; the 7.0.25 qualifications stay not run.
- Dispatch-only Forgehold lane workflow `forgehold-lane.yml` (PR #54) for the quality checks on a
  local runner. The later dispatch-only smoke workflow `forgehold-smoke.yml` (PR #67) is on `main` after
  the tagged commit and is not part of this release.
- Ledger soak: `ci.yml` also runs every three hours on a schedule with the nightly profile, on free
  hosted runners only.
- PostgreSQL tuning benchmark report; 0.2.0 action-sync and source-binding designs. Documentation only.

No runtime feature is added. See `CHANGELOG.md` for each identity (0.1.0b2, 0.1.0b3, 0.1.0b4).

## Open items (disclosed)

These are open at this release. Each is tracked in `docs/BETA_READINESS.md`.

| Item | Meaning | Status |
|---|---|---|
| Real-Zabbix suite | Execution against a real Zabbix 7.0.25 server | **Not run** |
| Manual gates | 2 of 42 mapped acceptance cases are manual; the full deployment catalogue is pending | **Pending** |
| G1–G8 | Deployment gates (inventory, ingestion contract, cohort, ownership, records) | **Planned, not verified** |
| A2 | Reference webhook transport does not authenticate TLS peers; no fix is possible on 7.0.25 | **Open**; do not deploy the reference webhook as shipped |
| A4/G5 | Permission tests on the real installed Zabbix account | **Not executed** |
| Native x64 qualification | Bounded native run on an authorised x86_64 host | **Deferred**; hosted CI native pass is run evidence only |
| Signed attestation | SLSA provenance of the wheel via GitHub attestations | Template supplied, **not run** |
| Internal review finding F1 | An Alerta principal with write access for the customer may edit an alert's Zabbix source attributes before acknowledging, retargeting the acknowledgement | **Disclosed**; fix designed for 0.2.0 (`docs/DESIGN_0.2.0_F1_SOURCE_BINDING.md`) |
| `claim_delivery` branch order | 014 keeps the maintenance-hold and cohort-policy branches of `claim_delivery` before the abandoned-attempt fence: an abandoned send under a pause is recorded `held` first and fenced later by `reconcile_due` (T42 case 3). Whether those branches should also move after the fence is not yet reviewed | **Open**; no failing case is known |
| Generated-test corpus | CI does not keep the Hypothesis example database between runs; failing sequences stay on the runner | **Open** |

## Deferred by owner decision

These items are deferred under the owner's approval of the exceptions recorded in the tag proposal.
They are **not** done:

| Item | Status |
|---|---|
| Private vulnerability-report test (a test report sent and receipt confirmed) | Deferred; `private_security_reporting_verified` is `false` in the register |
| Register approvals: copyright and third-party review, public-content review, PyPI namespace | Deferred; all three are `false` in the register |
| Independent reviewer's inspection of the release artifact bytes | Deferred unless the reviewer inspects them; the code at the tagged commit was reviewed |
| Independent external security review | Deferred; not performed |
| PyPI publication | Not done; not authorized |

GitHub attaches its own automatically generated "Source code" archives of the tag commit to every release.
They are not part of the reviewed release set, were not inspected, and are not listed in the artifact
table. The reviewed files are the wheel, sdist, source zip, `SHA256SUMS` and `BUILD_PROVENANCE.json`.

## Upgrade and compatibility

Forward migrations 001–014 apply in order. Migration 010 requires explicit resealing and the runtime
health gate. Migrations 013 and 014 replace routine bodies, re-record the routine digest and keep schema
version 4; run the readiness reseal afterwards as `docs/deployment.md` describes. Rolling back 013 or 014
returns the gap it closes; see `docs/rollback.md`.

## Reporting a problem

Use the repository's private vulnerability reporting once enabled (see
`SECURITY.md`). Do not include credentials, signing proofs or operational endpoints.
