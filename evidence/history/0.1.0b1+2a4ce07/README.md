# Zab-al-ack evidence

This index covers the current candidate, `0.1.0b1`, an unpublished local beta candidate (see
[beta readiness](../docs/BETA_READINESS.md)). Every record identifies the source and harness bytes it executed on, and
describes only that run.

## 0.1.0b1: records adopted for the tested source `2a4ce07`

The current-path records were produced on source commit `2a4ce07` (tree `929e6cfe…`). Its code digests are source
`c8a7430b…` and harness `cbe77055…`. The commit that adopts them changes only evidence and documentation, so those
digests stay identical. Every record below binds them. The records they replace, which were adopted for `34ea703`,
are kept byte for byte in [`history/0.1.0b1+34ea703/`](history/README.md).

**Local release checks** ([releasing](../docs/RELEASING.md)), from gate runs `c5-g1-20260929T204918Z` and
`c5-g2-20260929T205303Z`:

| Record | Result |
|---|---|
| `reference-run.json` | 2390 passed, 57 skipped, 0 failed |
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
- It was recorded by the lab run `b1-2a4ce07-host-01` with the **preliminary** wheel `eaf3c422…`, installed
  non-editably from its verified bytes. The cycle ran against its own disposable lab PostgreSQL and Zabbix services,
  and the runner record was retained.
- It describes that preliminary wheel. It carries to the canonical wheel only if the canonical bytes are identical,
  a comparison recorded with the candidate freeze.
- The same lab run's installed lab-trust validation (13 cases) and S2 webhook-transport harness (51 graded cases and
  15 observations) are source/host evidence. They are retained privately and are not repository records.
- The run installed the lock's PyJWT pin, 2.14.0. The lab run enforced that pin through its verified-records gate; it
  did not read the installed version back.

**Not run on this candidate:**
- native PostgreSQL 18/PgBouncer (`native-run.json`: NOT_RUN, written by `tools/native_local.py` without service
  binaries; no service started);
- the real-Zabbix API contract (`real-zabbix-run.json`: NOT_RUN).

Neither is inferred from the deployment path.

**Acceptance and audit:**
- `acceptance-results.json` maps 40 checks as PASS and 2 as MANUAL_GATE_PENDING. Every deployment result remains
  MANUAL_GATE_PENDING.
- `public-audit.json` is PASS with no findings.
- `sbom.cdx.json` inventories `0.1.0b1` and is schema-validated (below). It does not establish vulnerability
  clearance.

**Scans, SBOM and schema validation.** These records come from a local run on 29 September 2026 (20:56Z) in an
independent workspace at `2a4ce07`, with the gate records above uncommitted in it. That tree has the same source,
harness and locks as this commit. The scans do not cover this adoption commit's own metadata.

| Record | Result |
|---|---|
| `security-scans.json`, `pip-audit.json` | `tools/security_scan.py`, with pip-audit 2.9.0 and Gitleaks 8.24.3. pip-audit: 58 packages, 0 advisories, 0 skipped. Gitleaks: the public selection and the Git history, 0 findings, 255 commits |
| `npm-audit.json` | `npm audit --prefix lab --legacy-peer-deps`: 0 vulnerabilities |
| `sbom.cdx.json`, `dependency-review.json` | `tools/dependency_inventory.py` at `2a4ce07`. The SBOM matches both locks (59 Python and 2 npm components, no mismatch; PyJWT 2.14.0) |
| `sbom-validation.json` | `sbom.cdx.json` is valid against CycloneDX 1.6 (61 components). The schema, pinned by SHA-256, is loaded from a local registry only |

- These are scoped checks: known Python and npm advisories at run time, secrets in the public selection and
  history, lock-to-SBOM agreement, and schema validity.
- The SBOM was generated in that run's environment. Hashes it takes from installed files describe that environment;
  it was adopted as generated, not regenerated here.
- They are not licence clearance, application-security review or external review.

**Legacy a16 records still kept only in the history.** No tool for this candidate regenerates these three, so they
stay out of `evidence/`. Byte-exact copies remain in `history/0.1.0a16/`, and they are not evidence for `0.1.0b1`.

| Historical record (`history/0.1.0a16/…`) | Current status for `0.1.0b1` |
|---|---|
| `wheel-smoke.json` (the a16 wheel) | NOT_RUN as a separate record. Whether the canonical wheel is byte-identical to the preliminary wheel the deployment path installed is recorded with the candidate freeze |
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
- `history/0.1.0b1+34ea703/` preserves every current-path file this adoption replaced: the `0.1.0b1` records
  adopted for the tested source `34ea703` (digests `c8a7430b…/fab2507e…`), including their index. They keep their
  original bindings and counts: reference 2329 passed and 57 skipped; the deployment path PASS from lab run
  `b1-34ea703-host-01` with the preliminary wheel `eaf3c422…`; native and real Zabbix NOT_RUN.

The public export and acceptance tools still reject stale, malformed or contradictory evidence. G1–G8 remain pending,
and A2 is unresolved. Docker Compose, hosted workflows, external security review and signed attestation remain
unexecuted or pending.
