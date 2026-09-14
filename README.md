# Zab-al-ack

**0.1.0a16 · public alpha candidate · development and isolated evaluation**

Formerly **Ackline**. 0.1.0a9 renamed the package, CLI, Alerta entry points,
configuration, services, database namespace and signing domain to `zabalack`;
0.1.0a10 executed that namespace on real services for the first time (native
PostgreSQL 18.3 / PgBouncer, real Zabbix 7.0.10 and the deployment-path lab from
the installed wheel) and supplies the non-Docker runners it used (R14). See the
[rename and transition guide](docs/RENAME.md), [changelog](CHANGELOG.md),
[executed results](ALPHA_REPORT.md) and [local services](docs/local-services.md).

**Fresh installation namespace:** this is not an in-place Ackline upgrade.
Installation refuses an existing `ackline` schema; old ledgers and proofs must
be retained unchanged. New signatures cannot validate old proofs. Migrations
001–011 form the new `zabalack` baseline, schema version 7; archived Ackline
migration history remains immutable. Original a8 artifacts and evidence are
preserved separately in the complete handoff.

a11 added an [illustrated lab walkthrough](docs/visual-guide.md); a12 executed
the service suites on that tree and added browser captures, including a real
Zabbix 7.0.10 frontend view of the deployment lab's acknowledged event (lab data
only; no Alerta UI). **a13 adopts the [independent a12 review](docs/INDEPENDENT_REVIEW.md)**:
its lab-role finding (amendment A4 — a Zabbix 7.0 role keeps the enabled default
of an omitted action rule, so a "default-deny" fixture could still suppress
problems) was reproduced here on a separate instance, its corrections are taken
unchanged, and the same requirement is extended to the operator-provisioned
real-Zabbix fixture, where the live token's excluded actions are now proved to
be denied by the role permission itself.

a14 adds [task-specific agent guidance](docs/agent-workflows.md), three repository
skills, retained local review runs and guidance provenance in the source packages.
**a15 verifies a14 and executes the three service suites it recorded as NOT_RUN**
(native PostgreSQL 18.3 / PgBouncer 42, real Zabbix 7.0.10 6, deployment-path lab
4 from the installed wheel), and corrects one packaging defect verification
exposed: published evidence records inherited the 0600 mode of the temporary file
they were written through, so the export audit now rejects any exported file that
is not readable to group and other (R20). **a16 reproduces and adopts the
independent review of a15** — ten writer regressions covering both public and
both private writers under either umask, and precise corrections to how R20's
history, the optional profile records, the service-run attribution and the
verifier's PyYAML dependency were described (R21). Runtime acknowledgement
behaviour and SQL remain as in a13.

Durable, scoped acknowledgement delivery from Alerta to Zabbix. An authorised
human acknowledges one Alerta alert; PostgreSQL captures the signed intent in
the same transaction; a worker verifies it, checks the original Zabbix problem
and delivers a bounded acknowledgement. A reconciler repairs projections and
investigates uncertain outcomes without blindly repeating a write.

This is a Python package **plus PostgreSQL migrations, an Alerta application
adapter and background services**. Installing the wheel alone does not activate
write-back. Production examples have external writes disabled and no active
cohorts or endpoints. No production environment has been validated.

## Try the isolated reference lab

Prerequisites: Python 3.12, Node.js 22, npm and a disposable checkout. The commands
download pinned public dependencies and run synthetic tests; they do not contact
a configured Zabbix server or production database.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements-quality.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
npm ci --prefix lab --legacy-peer-deps --ignore-scripts
.venv/bin/python tools/quickstart.py --isolated-lab
```

The quickstart checks the inert CLI configuration, validates synthetic source
payloads and executes an actual Alerta HTTP → PostgreSQL capture → worker flow.
The PostgreSQL engine is **PGlite/WASM** and source API effects are **mocked**.
Expected output names each evidence layer and records failures and skips. It
does not demonstrate native concurrency, real login authentication, PgBouncer,
Zabbix permissions or update notifications. See [the lab guide](lab/README.md)
for native PostgreSQL, PgBouncer and optional real-Zabbix procedures.

## Quality and service checks

```bash
.venv/bin/python tools/run_quality.py
.venv/bin/python tools/run_mutation_checks.py --profile pr
.venv/bin/python tools/run_reference_suite.py --wasm
# Requires a disposable local Docker host:
.venv/bin/python tools/run_deployment_e2e.py --isolated-lab --plan
.venv/bin/python tools/run_deployment_e2e.py --isolated-lab
# Without Docker, on operator-provided local services (docs/local-services.md):
.venv/bin/python tools/native_local.py --isolated-lab --plan
.venv/bin/python tools/run_deployment_e2e_local.py --isolated-lab --plan ...
```

See [mutation testing](docs/mutation-testing.md),
[deployment E2E](docs/deployment-e2e.md),
[security automation](docs/security-automation.md) and
[release provenance](docs/release-provenance.md). Hosted CodeQL and signed
attestation issuance have not run here. The attestation template is inactive
and package publication stays disabled.

## Supported scope

- Authorised human `PUT /alert/<full-UUID>/action` with `action=ack`.
- One original Zabbix problem per eligible Alerta alert, within an approved
  customer–instance–trigger cohort and complete host set.
- Capture-health enforcement, two-stage HMAC signing, replay protection,
  restricted SQL interfaces, shared leases and delivery limits.
- Pre-read validation, already-acknowledged handling, expiry, pause, audited
  operator controls and conservative handling of unknown remote outcomes.

Bulk actions, chatops, arbitrary status/attribute updates and fan-in alerts are
outside this alpha's write-back scope. A later source unacknowledgement does not
automatically cause re-acknowledgement. See [design](docs/DESIGN.md),
[source-backed amendment A1](docs/SPEC_AMENDMENTS.md) and
[final-review interpretations](docs/final_review_notes.md).

## Compatibility and evidence

| Component | Alpha reference | Evidence / support limit |
|---|---|---|
| Python | 3.12 | Linux x86_64 reference; other platforms unvalidated |
| Alerta server | 9.1.0, default alarm model | Real installed hooks, HTTP and persistence tests |
| Alerta Web UI | Revision `e6eb99e` | Source inspected; no UI changes |
| PostgreSQL | Native 18.3 (reference pin 18.0); WASM engine 18.3 | Sequential WASM SQL and persistence executed; five native phases executed for a12 on a source-built local cluster (`tools/native_local.py`, 42 passed); Compose form NOT_RUN |
| PgBouncer | 1.24.1, transaction pool | Executed for a12 on a source-built local pool from `lab/pgbouncer.ini` (native `alerta` phase); Compose form NOT_RUN |
| Zabbix | 7.0.0 reference; 7.0.10 isolated lab target | Pinned source inspected and mocked contracts executed; real 7.0.10 API contract (5 passed) and deployment-path lab (4 passed, 13/13 observations, installed wheel) executed for a12 on disposable source-built instances; production behaviour remains G2/G3 |

These are reference pins, not a recommendation to deploy old patch releases.
Dependency or upstream upgrades require compatibility review and reruns. Exact
commits, hashes and observations are in [reference manifest](docs/reference_manifest.json)
and [compatibility manifest](docs/compatibility_manifest.json). Current executed
results are in [alpha report](ALPHA_REPORT.md) and [test evidence](evidence/README.md).
All T1–T39 requirements, including T11a/T11b, retain traceability in the
[acceptance matrix](docs/acceptance_matrix.csv); G1–G8 remain deployment gates.

## Installation and operations

Use [deployment](docs/deployment.md), [rollback](docs/rollback.md) and
[runbook](docs/runbook.md). Supply only protected secret-file references; never
put credentials into issue reports. The [input register](docs/INPUTS_REQUIRED.md)
consolidates operator requirements. The required Alerta factory is
`zabalack.plugins:create_app()` or an existing application wrapped with
`configure_app(app)` after its setup and before serving.

The wheel installs the plugins and `zabalack` CLI. The source release also contains
versioned SQL, deployment tooling, collectors, media artifacts and tests. Keep
the source release matching the wheel when installing migrations. Review
[public release instructions](docs/RELEASING.md) before distributing packages.

## Contributing and security

Read [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md),
[governance](GOVERNANCE.md) and [code of conduct](CODE_OF_CONDUCT.md). This is an
independent integration project; it is not an official Alerta or Zabbix product.
Project-owned material is licensed under [Apache-2.0](LICENSE); upstream material
retains its licence and attribution in [NOTICE](NOTICE) and
[THIRD_PARTY.md](THIRD_PARTY.md).
