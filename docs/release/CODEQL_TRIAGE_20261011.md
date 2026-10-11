# CodeQL triage — first completed run on the public repository (11 October 2026)

Run 38157736793 on `main` 1a498f5115f759121c4212715ecf8f04f05a6586 (python and
javascript-typescript, security-extended queries). Both analyses succeeded and
ingestion was accepted after the repository became public. Eight alerts, all rated
"high" by CodeQL, all in harness paths (`tools/`, `lab/`); none in `src/zabalack/`
or `sql/`. Dispositions below are proposals for the owner and the independent
reviewer; alerts are not dismissed in GitHub until both agree (R28).

| # | Rule | Location | Disposition | Reason |
|---|---|---|---|---|
| 1 | py/overly-permissive-file | tools/run_mutation_checks.py:113 | Accepted, by design | `_atomic_json(path, data, mode=...)` writes raw diagnostics with `PRIVATE_DIAGNOSTIC_MODE` (0o600) inside a 0700 directory and only the published evidence record with `PUBLIC_EVIDENCE_MODE` (0o644), which the export audit requires (R20, `public-file-not-world-readable`). The flagged `chmod` applies whichever caller-chosen mode is passed; never 0o666/0o777. |
| 2 | py/overly-permissive-file | tools/record_real_zabbix.py:75 | Accepted, by design | Writes the published redacted record with `PUBLIC_EVIDENCE_MODE` (0o644) after staging it in a checked private path (`checked_private_path`, `private_file`); raw reports stay private. Required by R20. |
| 3 | py/overly-permissive-file | tools/record_real_zabbix.py:166 | Accepted, by design | Same as 2: copies the staged private report to the public evidence path with `PUBLIC_EVIDENCE_MODE`. |
| 4 | py/overly-permissive-file | tools/review_runs.py:55 | Accepted, by design | `atomic_json(path, data, mode=0o600)` is private by default inside a 0700 `private_directory`; a caller writing a published record must pass the mode its readers need (0o644). The flagged `chmod` applies the caller's mode. |
| 5 | py/insecure-protocol | lab/deployment_e2e_runtime.py:137 | **Fixed in PR #46 (low)** | `ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)` set no explicit `minimum_version`, so the floor depended on the OpenSSL build and configuration. On the reference lab platform (CPython 3.12, OpenSSL 3.x) the default floor is already TLS 1.2 (`minimum_version == 771`), so TLS 1.0/1.1 were not actually accepted there; the alert is about relying on that default. PR #46 sets `context.minimum_version = ssl.TLSVersion.TLSv1_2` explicitly on the loopback lab server (127.0.0.1:58443, synthetic certificate). Lab-only; harness identity changes. |
| 6 | py/clear-text-logging-sensitive-data | tools/security_scan.py:108 | False positive | The printed JSON is the scan report (statuses, counts, scope strings). It contains no credential; raw findings stay local by design. |
| 7 | py/clear-text-storage-sensitive-data | tools/run_deployment_e2e.py:64 | Accepted, by design (lab) | `protected(path, value)` writes a synthetic lab credential to a file and sets 0o600. This is the file-based secret design, not an exception to it: the runtime reads secrets only through `read_protected_file` (`src/zabalack/config.py`), which refuses symlinks, non-regular files, any group/other bits and files not owned by root or the service user. Plain-text storage of secrets in owner-only files is the deployment contract; the lab mirrors it with synthetic values in a disposable environment. |
| 8 | py/clear-text-storage-sensitive-data | lab/deployment_e2e_runtime.py:61 | Accepted, by design (lab) | Same as 7 (`secret(name, value)` under the lab's `PRIVATE` directory, 0o600). |

Summary: 7 accepted or false positive with reasons; 1 low-severity item fixed in lab code by
PR #46 (explicit TLS 1.2 floor; the platform default was already TLS 1.2). No runtime
finding. CodeQL remains enabled on push, pull request and weekly.
