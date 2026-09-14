# Zab-al-ack a16 review and GitHub import

Reviewed 14 September 2026. Repository: [steliosx/zab-al-ack](https://github.com/steliosx/zab-al-ack) (private at verification).

## Result

The uploaded archive was imported successfully. The original plugin source is preserved exactly in [d68fa91c3305da1ba66f25277d1532069787b120](https://github.com/steliosx/zab-al-ack/commit/d68fa91c3305da1ba66f25277d1532069787b120). Its 309 files and Git modes produce the original candidate tree `fa6bc575bcefe6904eb483f46fb314e5e5c8b0b9`.

A subsequent maintenance commit, [d23cad1869d7f69265cd211981ccdc3dce349989](https://github.com/steliosx/zab-al-ack/commit/d23cad1869d7f69265cd211981ccdc3dce349989), fixes the CI bootstrap issue described below. It changes only `tools/agent_guidance.py` and `tests/release/test_agent_guidance.py`. No new version, release tag or replacement package was produced.

## Findings

### Fixed: CI and scanner could not start without PyYAML

The first hosted run failed before the quality, mutation, reference and native test commands. The security job failed for the same reason. Both entrypoints imported `public_audit`, which imported `agent_guidance`, which imported `yaml` before the workflow had installed that dependency.

The import is now inside the guidance-validation function, which is the operation that needs it. Validation still parses YAML and rejects invalid guidance. Three regression cases run Python with `-E -S`: both helper command entrypoints, and actual CI preparation including removal of historical evidence.

The original failure was reproduced locally. The correction passed 80 focused release tests, Ruff, mypy and pip check. GitHub subsequently passed the quality, reference, mutation and security jobs.

### Open: native PostgreSQL CI fails and blocks packaging

The native job starts its disposable Compose services, then reports 0 passed, 24 failed and 0 skipped in its first PostgreSQL phase. The runner stops those services successfully (shutdown exit code 0). Later native phases do not run, and the dependent packaging job is skipped.

The hosted log exposes the phase result but does not include the private pytest log. The underlying exception has therefore not been established. A follow-up should collect bounded, sanitised fixture-failure diagnostics and reproduce that Compose-specific failure. Do not classify the existing local native-service records as evidence of hosted success or weaken the native packaging requirement.

Run: [Alpha reference checks, 34797043917](https://github.com/steliosx/zab-al-ack/actions/runs/34797043917).
Native job: [103832046134](https://github.com/steliosx/zab-al-ack/actions/runs/34797043917/job/103832046134).

## Validation of the supplied archive

The re-upload is byte-identical to the archive reviewed earlier in this workflow. Its SHA-256 is `592adc10f69233f2cb4bd177168cdba51230a559f475ef9233ec06b1b167c33b`. All 1,049 extracted files match that reviewed input. Retained review evidence hashes were rechecked.

The prior independent review, preserved unchanged under `independent-review/`, recorded:

- Reference suite: 865 passed, 23 skipped, 0 failed.
- Quality, SQL, PR/nightly mutation, nightly property and quickstart checks: passed.
- Rebuilt wheel, sdist and source ZIP: byte-identical to the supplied artifacts.
- Fresh non-editable wheel installation and all plugin/CLI entrypoints: passed.
- Gitleaks: no findings in plugin source or its bundled Git history. Fourteen expanded-packet findings were verified as file-checksum false positives; no confirmed secrets.
- Website: 19 HTML pages and 443 local references checked; no broken local references.
- Supplied native (42 passes), real Zabbix 7.0.10 (6 passes), and deployment (4 passes, 13 observations) records: validated against the supplied candidate, not independently re-executed during this review.

Those original reports remain bound to candidate `e9dc46d2202f0a60638b5506f75af2da11643357`. They do not describe the later CI maintenance commit. Historical reports committed on main have not been relabelled as current evidence; fresh CI artifacts belong to their actual run.

## Hosted checks after the correction

| Check | Result |
| --- | --- |
| Quality | Success |
| Reference and PostgreSQL WASM | Success |
| Mutation | Success |
| Dependency and secret scan | Success |
| Native PostgreSQL/Compose | Failed |
| Packaging | Skipped because native failed |
| CodeQL | Skipped by the existing opt-in setting |
| Deployment E2E | Not invoked |

Security run: [34797043956](https://github.com/steliosx/zab-al-ack/actions/runs/34797043956).
CodeQL run: [34797043954](https://github.com/steliosx/zab-al-ack/actions/runs/34797043954).

## Repository layout and preserved history

| Branch | Contents |
| --- | --- |
| `main` | The reviewed a16 plugin source plus the CI bootstrap correction. |
| `website` | All 34 website files unchanged from the archive; verified bytes and Git modes. |
| `handoff/a16` (prepared; not created) | Proposed original complete ZIP, original Git bundle, this review and retained independent-review records. |

The authenticated GitHub API was used because the earlier CLI attempt lacked credentials. GitHub starts with an import commit; original candidate commit identities and the 16-commit bundled lineage are retained in the attached original ZIP and this proposed payload's `source-history/zabalack-0.1.0a16.bundle`. No history was force-pushed.

Automatic approval review rejected creation of the handoff tree, stating that the complete archive/history payload had not been explicitly authorized for this destination. The account was subsequently verified as `steliosx`, the repository as private and push-enabled, and the proposed ZIP as byte-identical to the user's upload. A Gitleaks scan of the prepared handoff found no leaks. The additional archive/history branch remains pending a precise approval; the rejection was not bypassed. Main and website were read back and verified successfully.

The Apache-2.0 license and supplied ignore rules were preserved. Per-deployment G1–G8, amendment A2 and the remaining public-release inputs are still open as documented in the source. The repository remains a private development repository.
