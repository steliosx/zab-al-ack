# Populated-database upgrade and re-seal — assessment (a17 candidate)

Scope: development-lab verification of migrations **001–012** applied to an
existing, **populated** Zabalack installation. Same-namespace Zabalack migration;
this is not an Ackline-to-Zabalack conversion, which `docs/RENAME.md` states this
release does not implement.

This assessment is not x64 qualification, not production acceptance, and does not
qualify a release artifact. G1–G8 and the deployment gates are unaffected.

## Candidate

| | |
|---|---|
| Candidate | PR #26 head `3ac7829d0ae9c21d23d4f3cf7d522722ab3fc590` |
| Base | `main a3317dc41890ef56411e7597b32c44c70b878416` |
| `sql/` tree | `530693850eb79fa17a623e7b6745d9ac77db5fa8` — identical at both revisions |
| `src/` tree | `2c370954229be83a934c15da3fb52a366d795630` — identical at both revisions |

PR #26 changes only `docs/RELEASE_AMENDMENTS.md`, `docs/tooling_reference_manifest.json`,
`pyproject.toml` and `requirements.lock`. Migration and re-seal behaviour is therefore
the same object at the candidate and at its base, and this assessment covers both.
The regressions are committed on a branch based on `main` so the diff is tests and
docs only; the trees above are what makes that equivalent to running them at the
candidate.

## Baseline

Schema **3** (migrations `001`–`007`), built by `sql/migrate.py` against a staged
tree containing only those files, then populated through the shipped schema-3
`worker_call` and `operator_call` entry points. No ledger row is written directly,
so the upgrade sees states a real earlier installation could actually hold.

Recorded migration checksums, as applied by the runner:

    001_core.sql                      6bb752d0ee1adcf7dc2a595bd1e95be008da1ef2068810ab2a8ee2af3b44b286
    002_capture.sql                   2a1640ceb0d8ba30e25a22f9321b6a0b1d963c1c57725997211be1692d4ae437
    003_worker.sql                    b30b50fc24810fcc9f4bfdfe4537eec9875963f5a99f6c5a02ff7019bdb8a8f2
    004_controls.sql                  58e06b8685406d6f647f43d504a7acd5c21b2d4a41d98c606d61f978cf24f1a0
    005_readiness.sql                 2e07b4c1a9822fd419a7a0231f48ccde7245da8d3836a5add2612553fb5764f9
    006_late_response_fencing.sql     b7dcb6f7df5a4a3eb7dccd69792820cec606737c0b7f2ddaaf38ea4f68b14b65
    007_settled_expiry.sql            bacfd9f7d0612f2b52869b8054aad2a39ddd836110bb47a93a0d59c1efab84a0
    008_generation_fence.sql          8dc04f4b5f61100a709242ac0d0546fd56464df949a662c6c409faa7ab118873
    009_abandoned_attempt_sweep.sql   b30a846b0291419dcb71b55b1285fa163b362394b8ed4f4bcc025fd56f709a29
    010_ambiguous_api_failure.sql     557549c5a9abd30425303f23c49652562aaa5ad6ba3ee0f785f6154beab14258
    011_late_ambiguous_suspension.sql 47b4d98e1ec75cf1ddf32bf5985dab7ef6888489be6910bb09e0976b3c0de5be
    012_settled_attempt_suspension.sql 2b1ec1dc17ab71b7686ded37c34fcead07b9706fc03e37303067fde8ea417e45

Populated states, each reached through a real caller and asserted before any
upgrade runs:

| Seeded | Baseline state | How it was reached |
|---|---|---|
| pending | `authorised`, attempts 0 | verified acknowledgement, never claimed |
| leased/abandoned | `unknown`, uncertain, `abandoned_attempt` | claim, send, lease expired with no outcome |
| uncertain | `unknown`, uncertain, `synthetic_response` | claim, send, unknown outcome while leased |
| delivered | `ok`, evidence `write_response`, effect closed | claim, send, ok outcome |
| operator-settled | `failed`, `operator_no_effect_confirmed` | uncertain, reconciled, then `settle_unknown` |
| live ambiguous | `delivery_claimed` | claimed and sent, lease still live across the upgrade |

Staged intents are covered by the existing pre-fence baseline in
`test_native_generation_fence.py` and are not re-seeded here.

## What was already covered, and reused

`tests/postgres/test_native_generation_fence.py` already upgrades a populated
schema-3 database through `sql/migrate.py`, `09_installer_roles.sql`,
`12_application_grant.sql` and `13_reseal.sql`, and its `upgrade()` applies every
remaining migration in one pass. Its populated cases cover the capture/staging
side: fence tiering, malformed intents, captures awaiting verification, verifier
hold, rejected captures, failed-migration rollback and forward recovery,
incomplete-installation health refusal, and the re-seal refusals and grant
survival. None of that was repeated.

The gap it leaves: that file contains no `claim_delivery` and no `start_attempt`,
so every delivery-ledger row it upgrades is one the migration never had to reason
about. Migrations 009–012 are the ones that act on in-flight delivery state.

## Findings

All ten regressions pass at the candidate tree. No product defect was found.

1. **Protected records and proofs are preserved.** Action state, reason, evidence,
   uncertainty, attempt count, public reference and both deadlines, every
   `attempts` and `outcomes` row and the effect are byte-identical across
   008–012 for all six seeded actions.
2. **Uncertainty is not converted into permission to retry.** Two explicit
   operator acts stand between carried-over uncertainty and a fresh send, and
   the upgrade removes neither:

       after the upgrade        not claimable
       after `retry` alone      not claimable
       after `reconcile_due`    not claimable
       after `settle_unknown`   not claimable
       after settle + retry     claimable

   Only the operator's recorded disposition clears `uncertain`; `retry` preserves
   the public reference and the execution deadline.
3. **Delivered and settled work is not reopened.** A callback replayed from a
   pre-upgrade attempt lands as `late_response`, leaves action, effect and
   attempts unchanged, appends only an audit outcome row, and does not disturb
   any other action.
4. **The unsealed intermediate state is refused by both gates.** Between the
   migrations and `13_reseal.sql` the tree reports `zabalack_schema_version` 4
   with `functions_ok` false; `health.py` raises `capture_health_schema` and the
   `worker.py` capability predicate is false. Both pass after the explicit re-seal.
   Note that `before_write` still answers `allowed` in that window — it is a
   lease-level permission answer, and the seal gate sits above it in the capture
   and worker paths. That is the shipped contract, not a defect.
5. **Re-seal refuses a prohibited grant on a populated tree**, and refuses rather
   than repairing: the grant is left in place and the ledger untouched.
6. **Role callers behave as themselves.** Six caller/routine denials are exercised
   as the actual role and fail with insufficient privilege; the permitted calls
   still work for `zabalack_worker`, `zabalack_operator`, `zabalack_probe` and
   `alerta_app`.
7. **An interrupted upgrade is safe.** An abort inside the 008–012 pass rolls the
   whole pass back: schema version stays 3, no recorded checksum is rewritten, no
   ledger row is deleted, and re-running the real runner completes forward with
   the ledger unchanged.
8. **The ambiguous-API path behaves correctly on a carried-over attempt.** An API
   error on a live pre-upgrade attempt suspends the instance and opens an
   incident; after the operator disposes of the attempt and re-authorises
   delivery, the same callback replayed lands as a late response and does **not**
   re-suspend.

## Discrimination

The assertions were checked against trees that lack the behaviour, not assumed to
discriminate:

| Control | Result |
|---|---|
| 009–012 removed | `test_an_api_error_on_a_carried_over_attempt_suspends_only_while_it_is_live` fails — *"an ambiguous API error on a live carried-over attempt did not suspend the instance"*; the unsealed-state case also fails |
| 011 present, **012 removed** | the same test fails at the second leg — *"a replayed API error re-suspended the instance behind the operator's disposal and retry"* |

The second control is the one that separates 012 from 011.

**Stated plainly:** the other eight regressions still pass with 009–012 removed.
The properties they assert — uncertainty fencing, stale-callback handling, record
preservation, role denials, interrupted-upgrade safety — are supplied by 006/007
and are not specific to 009–012. They are worth keeping as populated-upgrade
coverage, but they are not evidence about those four migrations, and should not
be cited as if they were. Migrations were restored to their recorded digests
after each control.

## Supported and unsupported baselines

**Supported.** A schema-3 Zabalack installation (`001`–`007`) upgraded in one
runner pass to `012`, followed by the installer record, the application grant and
an explicit `13_reseal.sql`, in the runbook's order.

**Unsupported, and untested here.**

- Any pre-`001` or non-Zabalack namespace, including Ackline. `docs/RENAME.md`
  states there is no in-place Ackline upgrade; installation refuses an existing
  `ackline` schema.
- Partial baselines between `001` and `007`. `schema3_database` stages a tree
  containing only the migrations below 008, because handing a schema-3 database
  every later migration individually is not the path a real upgrade takes.
- Upgrading without the explicit re-seal. The tree is left reporting
  `functions_ok` false and both gates refuse; that is the designed state, not a
  supported operating one.
- PgBouncer-pooled and multi-session concurrency during the upgrade pass.
- Real Zabbix delivery during or after the upgrade: outbound delivery is disabled
  throughout, and no external write is attempted.

## Retention

**Pending.** These runs are single-store in an ephemeral development session. No
second-store copy has been verified, and none is claimed. Raw diagnostics stay
private; this file and the issue report carry sanitized results only.
