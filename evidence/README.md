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
- `sbom.cdx.json` inventories `0.1.0b1`. It does not establish vulnerability clearance.

The six a16 service XMLs, which the NOT_RUN recorders remove from the current paths, remain in the history.
Preparation, local and host runs stay distinct.

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
