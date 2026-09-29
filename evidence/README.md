# Zab-al-ack evidence

This index covers the current candidate, `0.1.0b1`, an unpublished local beta candidate (see
[beta readiness](../docs/BETA_READINESS.md)). Every record identifies the source and harness bytes it executed on, and
describes only that run.

## 0.1.0b1: records adopted for the tested source `34ea703`

The current-path records were produced on source commit `34ea703` (tree `ea674a79…`). Its code digests are source
`c8a7430b…` and harness `fab2507e…`. The commit that adopts them changes only evidence and documentation, so those
digests stay identical. Every record below binds them.

**Local release checks** ([releasing](../docs/RELEASING.md)):

| Record | Result |
|---|---|
| `reference-run.json` | 2329 passed, 57 skipped, 0 failed |
| `mutation-pr-run.json` | 12 killed, 0 survived |
| `mutation-nightly-run.json`, `mutation-run.json` | 20 killed, 0 survived |
| `property-nightly-run.json` | 46 passed |
| `quality-run.json` | ruff, mypy and pip-check pass |
| `runtime_wasm_sql.json` (the SQL checks) | pass. Quickstart also passed; it writes no separate record |

- Of the 57 skips, 52 are the unavailable native and real-Zabbix service layers. The other 5 are layer-specific:
  4 in WASM and 1 in unit/mock.
- The WASM layer is PostgreSQL 18.3 in PGlite: a single backend, no native concurrency, authentication, TLS or
  PgBouncer.

**Deployment path** (`deployment-e2e-run.json`): PASS.
- 4 tests passed, 0 failed, and 13 of 13 record checks.
- It was recorded by the lab run `b1-34ea703-host-01` with the **preliminary** wheel `eaf3c422…`, installed
  non-editably from its verified bytes. The cycle ran against its own disposable lab PostgreSQL and Zabbix services,
  and the runner record was retained.
- It describes that preliminary wheel. It carries to the canonical wheel only if the canonical bytes are identical,
  a comparison recorded with the candidate freeze.
- The same lab run's installed lab-trust validation (13 cases) and S2 webhook-transport harness (51 graded cases and
  15 observations) are source/host evidence. They are retained privately and are not repository records.

**Not run on this candidate:**
- native PostgreSQL 18/PgBouncer (`native-run.json`: NOT_RUN);
- the real-Zabbix API contract (`real-zabbix-run.json`: NOT_RUN).

Neither is inferred from the deployment path.

**Acceptance and audit:**
- `acceptance-results.json` maps 40 checks as PASS and 2 as MANUAL_GATE_PENDING. Every deployment result remains
  MANUAL_GATE_PENDING.
- `public-audit.json` is PASS with no findings.
- `sbom.cdx.json` inventories `0.1.0b1` and is schema-validated (below). It does not establish vulnerability
  clearance.

**Scans and schema validation.** These four records come from a local run on 29 September 2026 (16:30–16:31Z)
over the tree of `d4886059`, in an independent full-history clone. That tree has the same source, harness, locks and
SBOM as this commit. The scans do not cover this adoption commit's own metadata.

| Record | Result |
|---|---|
| `security-scans.json`, `pip-audit.json` | `tools/security_scan.py`, with pip-audit 2.9.0 and Gitleaks 8.24.3. pip-audit: 58 packages, 0 advisories, 0 skipped. Gitleaks: the public selection and the Git history, 0 findings, 245 commits |
| `npm-audit.json` | `npm audit --prefix lab --legacy-peer-deps`: 0 vulnerabilities |
| `sbom-validation.json` | `sbom.cdx.json` is valid against CycloneDX 1.6 (61 components). The schema, pinned by SHA-256, is loaded from a local registry only |

- These are scoped checks: known Python and npm advisories at run time, secrets in the public selection and
  history, and schema validity.
- They are not licence clearance, application-security review or external review.

**Legacy a16 records still kept only in the history.** No tool for this candidate regenerates these three, so they
stay out of `evidence/`. Byte-exact copies remain in `history/0.1.0a16/`, and they are not evidence for `0.1.0b1`.

| Historical record (`history/0.1.0a16/…`) | Current status for `0.1.0b1` |
|---|---|
| `wheel-smoke.json` (the a16 wheel) | NOT_RUN as a separate record. The canonical wheel is byte-identical to the preliminary wheel the deployment path installed |
| `collection-check.json`, `local-validation.json` (a16 / a15) | NOT_RUN as separate records. `reference-run.json` holds this candidate's collection and suite results |

The a16 versions of the four scan and validation records also remain in the history.

`dependency-review.json` is current: the dependency inventory reproduced it for this candidate. The six a16 service
XMLs, which the NOT_RUN recorders remove from the current paths, also remain in the history. Preparation, local and
host runs stay distinct.

## Historical evidence

[history/](history/README.md) holds byte-exact copies of earlier versions' records, under paths that never change.
- `history/0.1.0a16/` preserves every file this directory carried for a16, including its original index. Its
  `MANIFEST.json` lists each file's SHA-256, size, Git blob and original path.
- The a16 records keep their original bindings and counts:
  - native: 42 passed;
  - real Zabbix: 6 passed, including the A4 excluded-action denial;
  - deployment path: 4 passed, with 13/13 observations from the installed a16 wheel;
  - reference: 865 passed and 23 skipped.

The public export and acceptance tools still reject stale, malformed or contradictory evidence. G1–G8 remain pending,
and A2 is unresolved. Docker Compose, hosted workflows, external security review and signed attestation remain
unexecuted or pending.
