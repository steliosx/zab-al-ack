# Zab-al-ack 0.1.0a17 — integrated recovery candidate

> **Superseded identity (11 October 2026; current candidate 0.1.0b4 since 29 October 2026, amendment R31).** The current candidate is `0.1.0b4`; see
> `CHANGELOG.md`, `docs/BETA_READINESS.md` and amendment R23. This a17 report is
> retained as history and is not rebound to the beta candidate.

This is an unpublished candidate under qualification, not beta acceptance.
It assigns a new version to PR25's integrated correction so that the previously
qualified a16 wheel is never silently replaced. The runtime/SQL correction and
its source-backed rationale are in [COLD_SOURCE_FAILURE](docs/COLD_SOURCE_FAILURE.md).
Forward migration 010 requires explicit resealing and the runtime health gate;
schema 4, prior migration bytes, grants and dependency pins remain unchanged.

The preceding PR25 head `1f23cc25a0c4b386f4799a7cc71bd3f1c6451e21`
passed Actions35300274553/1 (native311, reference1465/47 skipped, mutation12,
quality/package) with privately retained producer-to-package proof. Those
results establish that revision only. Version/export metadata changes affect
source and harness digests, so a17 needs its own required producer/package run
and affected supplied-wheel recovery qualification; no historical report is
relabeled. Current status and owner decisions are in [BETA_READINESS](docs/BETA_READINESS.md).

CodeQL was skipped; a separate current whole-repository security report is
unavailable. Independent technical review is distinct from GitHub approval.
Native x64 remains deferred. Installed-account A4/G5, A2, T30/G1–G8 and
publication/independent-review requirements remain open as applicable.

## Historical a16 report (retained verbatim below)

# Zab-al-ack 0.1.0a16 — the a15 review verified and adopted

This candidate takes the independent review of a15, reproduces each of its
claims before accepting any of them, adopts its ten writer regressions and its
documentation corrections, and re-executes every suite on the corrected tree.
The review found no runtime or SQL defect and proposed no product change; this
candidate makes none. Runtime Python differs from a15 only in the version
constant, SQL and schema version 3 are unchanged, and there is no migration.

## The review, verified

Its 46 checksum entries check out, and the a15 packet it retains
(`cf397f91…`) is byte-identical to the one I delivered, so it reviewed the
release rather than a copy of it. Its manifest declares no change to the
supplied release and no new version, and that is what the bytes show. Its own
execution reproduces mine: reference **855 passed / 23 skipped / 0 failed**
(826 unit/mock/property/offline + 29 PostgreSQL WASM), 8 SQL groups, 15 webhook
groups, mutation PR 12/12 and nightly 20/20, property nightly 46, quality and
quickstart, with wheel, normalised sdist and source ZIP rebuilt to the supplied
bytes. It ran Gitleaks 8.24.3 over the source and the history through the a15
commit and reports zero findings, which is a layer a15 recorded as NOT_RUN. It
validated rather than re-ran the three service records, and says so.

## Its corrections, reproduced here before adoption

**The R20 history was overstated.** I wrote that the owner-only mode "shipped in
every handoff since a7". Reading the stored mode out of each delivered archive
says otherwise: a3/a4/a5/a8 0644, a9 0644, a10 **0600**, a11 0644, a12 **0600**,
a13 **0600**, a14 0644, a15 0644 (`verification/archive-modes.json`; where a
packet's own zip had been consumed the unzip-extracted tree was read instead,
after confirming extraction preserves modes). The writer defect is continuous —
a fresh a14 run still produced 0600 — but only some handoffs carried it to a
consumer, and the reason is mechanical: my packaging copies source files with
`shutil.copymode`, so my packets published whatever mode the writer left, while
packets assembled by the other producer normalised to 0644. That separates what
the writer did, which was wrong in every candidate, from what a consumer
received, which was not.

**"Required by the audit" was wrong.** I described the two profile mutation
records as required. Deleting both from a disposable copy of the tree and
re-running the binding produced an empty `evidence_binding_findings` list: they
are validated when present and optional when absent. The rule is unchanged here;
only the description of it is.

**Service runs belong to the candidate that ran them.** I wrote that a15's
native, real-Zabbix and deployment runs "describe a14's service behaviour too".
Unchanged service-path code supports that as an inference; it does not convert
a14's NOT_RUN records into executions. The wording now says which it is.

**The verifier is not stdlib-only.** `tools/verify_release.py` imports
`tools/agent_guidance.py`, which imports PyYAML, so the claim in `START_HERE.md`
that a stdlib verifier establishes consistency was false. Confirmed by import.

**Two owner-only records do remain in the handoff.** My a15 review table said
there were none. There are two, both inside `reviews/0.1.0a7/plugin/evidence-a7/`
and retained exactly as received — and of the twenty-one records in that
directory they are the only two below 0644, which is the writer defect drawn in
outline. One of them, `real-zabbix-run.json`, is a **NOT_RUN** record at 0600.
That settles a second point: the real-Zabbix writer
never needed a live Zabbix to expose its mode, because the NOT_RUN branch uses
the same writer, and the a7 packet proves the mode was already shipping. My a15
text claimed the instance was "invisible while no candidate could run the
suite"; it was visible in a retained artifact and simply unchecked, because
nothing examined exported modes as a class until R20 added that rule.
`verification/handoff-checks.json` already recorded both files accurately in
a15; only the prose overstated.

## Adopted

- The review's ten writer regressions, as `tests/release/test_evidence_modes.py`:
  both public writers and both private writers, each under a permissive (0o000)
  and a restrictive (0o077) umask, plus replacement of an existing 0600 file and
  an explicit publish through the private-by-default review writer. Verified to
  discriminate: on an unmodified a14 checkout the four public cases fail on the
  mode itself (0600 observed, 0644 required) and the other six fail because
  a14's writers had no `mode` parameter at all. All ten pass here.
- Every documentation correction above, in the changelog, this report, R20 and
  the website, and the `Still owner-only` section headings renamed to `Still
  pending` throughout the changelog so the phrase cannot be read as a file mode.

## Executed on the final tree

| Check | Result | Evidence scope |
|---|---|---|
| Reference suite | **865 passed / 23 skipped / 0 failed** | 836 unit/mock/property/offline and 29 actual PostgreSQL WASM passes; every discovered case selected |
| Native PostgreSQL 18.3 + PgBouncer 1.24.1 | **42 passed / 0 failed / 0 skipped** | `tools/native_local.py`, five fresh-cluster phases (`postgres` 24, `delivery_faults` 5, `worker` 4, `alerta` 7, `performance` 2); cleanup exit 0 per phase |
| Real Zabbix 7.0.10 | **6 passed / 0 failed / 0 skipped** | `tools/record_real_zabbix.py --run` on a disposable source-built instance, including the A4 excluded-action denial |
| Deployment-path lab | **4 passed / 0 failed / 0 skipped; 13/13 observations true** | `tools/run_deployment_e2e_local.py` from the installed a16 wheel |
| Documentation capture | PASS | `tools/capture_docs.py --isolated-lab` on the final tree |
| Curated PR / nightly mutations | 12/12 and 20/20 detected | Separate profile records, validated when present |
| Supplemental SQL / synthetic media contract | 8 groups / 15 groups passed | Real PostgreSQL 18.3 WASM engine; Node payload contract |
| Quality, quickstart, property nightly | PASS, PASS, 46 passed | Pinned Ruff (`src` scope), gradual strict mypy, pip check |
| Python / Node advisory queries | 58 packages / 0 advisories; npm 0 vulnerabilities | Known-advisory queries only |
| SBOM schema, wheel smoke, export audit, release verifier | Valid CycloneDX 1.6 (61 components); PASS; zero findings; PASS | Unsigned local consistency |

These runs were executed on a16. a15 and a16 differ only in the version constant,
documentation and the adopted tests, so the unchanged service-path code supports
an inference about a15's behaviour; a15's own records stand on their own runs.
Repeated checks overlap and are not added to the unique reference total.
**G1–G8 remain MANUAL_GATE_PENDING.**

## Unexecuted and deployment limits

The Docker Compose forms of both labs, hosted Actions, CodeQL, Gitleaks and
signed-attestation issuance remain **NOT_RUN**. Gitleaks was attempted here: a
Go 1.24.7 toolchain is present and the pinned commit clones, but three module
hosts are outside this environment's egress allowlist and substituting mirrors
would stop the binary being the pinned upstream build, so the manifest now
carries that reason instead of the stale "Go toolchain unavailable". The
review's own Gitleaks result is its evidence, not this candidate's, and
`evidence/security-scan.json` stays NOT_RUN. An Alerta Web UI capture is still
missing. A2 (the reference webhook transport does not authenticate TLS peers and
follows redirects) is unresolved. G5 still requires effective-role read-back and
positive and negative permission tests on the approved installed account (A4).
a14's qualitative agent evaluations are retained as received; they are
forward-tests, not benchmarks, and packaging skill files does not establish that
any host discovers them. There is no in-place Ackline upgrade.

## Reproduce and hand off

Use the Python 3.12 reference lock and hash-pinned quality lock; run the
commands in `docs/RELEASING.md`, `docs/agent-workflows.md`,
`docs/local-services.md` and `lab/real_zabbix.md`. `tools/run_review.py
--isolated-lab --plan` lists the local phases. The handoff includes source,
wheel, sdist, source ZIP, a UTC-dated Git bundle, checksums, changelog and
review; `tools/verify_release.py` needs the declared dependencies, PyYAML
included, and establishes unsigned consistency only. Production examples remain
disabled with empty cohorts and endpoints; existing schema-3 `zabalack`
installations need no migration for a16. Do not provide live secrets in chat or
use lab fixtures to close gates.
