# CodeQL triage — first completed run on the public repository (11 October 2026)

Run 38157736793 on `main` 1a498f5115f759121c4212715ecf8f04f05a6586 (python and
javascript-typescript, security-extended queries). Both analyses succeeded and
ingestion was accepted after the repository became public. Eight alerts, all rated
"high" by CodeQL, all in harness paths (`tools/`, `lab/`); none in `src/zabalack/`
or `sql/`. Dispositions below are proposals for the owner and the independent
reviewer; alerts are not dismissed in GitHub until both agree (R28).

| # | Rule | Location | Disposition | Reason |
|---|---|---|---|---|
| 1 | py/overly-permissive-file | tools/run_mutation_checks.py:113 | Accepted, by design | Public evidence records must be group/other readable (R20, export audit `public-file-not-world-readable`). Mode comes from a named constant, never 0o666/0o777. |
| 2 | py/overly-permissive-file | tools/record_real_zabbix.py:75 | Accepted, by design | Same as 1 (`PUBLIC_EVIDENCE_MODE`). |
| 3 | py/overly-permissive-file | tools/record_real_zabbix.py:166 | Accepted, by design | Same as 1. |
| 4 | py/overly-permissive-file | tools/review_runs.py:55 | Accepted, by design | Same as 1. |
| 5 | py/insecure-protocol | lab/deployment_e2e_runtime.py:137 | **Fix recommended (low)** | `ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)` without `minimum_version` permits TLS 1.0/1.1 on the lab's loopback HTTPS server (127.0.0.1:58443, synthetic certificate). Set `context.minimum_version = ssl.TLSVersion.TLSv1_2`. Lab-only; changes the harness identity, so it produces a new candidate run. |
| 6 | py/clear-text-logging-sensitive-data | tools/security_scan.py:108 | False positive | The printed JSON is the scan report (statuses, counts, scope strings). It contains no credential; raw findings stay local by design. |
| 7 | py/clear-text-storage-sensitive-data | tools/run_deployment_e2e.py:64 | Accepted, by design (lab) | Writes synthetic lab credentials to a 0600 file in the disposable lab so services read them from files, not the environment. Not production code. |
| 8 | py/clear-text-storage-sensitive-data | lab/deployment_e2e_runtime.py:61 | Accepted, by design (lab) | Same as 7. |

Summary: 7 accepted or false positive with reasons; 1 low-severity fix recommended in
lab code. No runtime finding. CodeQL remains enabled on push, pull request and weekly.
