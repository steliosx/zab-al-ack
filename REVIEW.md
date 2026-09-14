# Zab-al-ack a16 review and GitHub import

Reviewed 14 September 2026. Repository: [steliosx/zab-al-ack](https://github.com/steliosx/zab-al-ack) (private at verification).

## Result

The uploaded archive was imported successfully. The original plugin source is preserved exactly in [d68fa91c3305da1ba66f25277d1532069787b120](https://github.com/steliosx/zab-al-ack/commit/d68fa91c3305da1ba66f25277d1532069787b120). Its 309 files and Git modes produce the original candidate tree `fa6bc575bcefe6904eb483f46fb314e5e5c8b0b9`.

Subsequent maintenance commits fix the CI bootstrap and investigate the hosted native lab, as described below. These changes concern CI, lab infrastructure, diagnostics, tests and documentation. The plugin implementation and SQL remain unchanged. No new version, release tag or published package was produced.

Final main commit: [89a93b1f641a0f978b04862c747b8d4161df3f5a](https://github.com/steliosx/zab-al-ack/commit/89a93b1f641a0f978b04862c747b8d4161df3f5a). All required hosted jobs pass, including native PostgreSQL/PgBouncer and the dependent package build. The website branch is also present. The original archive, Git-history bundle and review records are uploaded and verified on [handoff/a16](https://github.com/steliosx/zab-al-ack/tree/handoff/a16).

## Findings

### Fixed: CI and scanner could not start without PyYAML

The first hosted run failed before the quality, mutation, reference and native test commands. The security job failed for the same reason. Both entrypoints imported `public_audit`, which imported `agent_guidance`, which imported `yaml` before the workflow had installed that dependency.

Commit [d23cad1869d7f69265cd211981ccdc3dce349989](https://github.com/steliosx/zab-al-ack/commit/d23cad1869d7f69265cd211981ccdc3dce349989) moves the import inside the guidance-validation function, which is the operation that needs it. Validation still parses YAML and rejects invalid guidance. Three regression cases run Python with `-E -S`: both helper command entrypoints, and actual CI preparation including removal of historical evidence.

The original failure was reproduced locally. The correction passed 80 focused release tests, Ruff, mypy and pip check. GitHub subsequently passed the quality, reference, mutation and security jobs.

### Fixed: the native Compose lab could not reach PostgreSQL

The initial native job started its disposable Compose services, then reported 0 passed, 24 failed and 0 skipped in its first PostgreSQL phase. The runner stopped those services successfully (shutdown exit code 0). Later native phases did not run, and the dependent packaging job was skipped.

Commit [93178e66a2f0658f33315513b09baf52a1b74fd2](https://github.com/steliosx/zab-al-ack/commit/93178e66a2f0658f33315513b09baf52a1b74fd2) adds bounded failure diagnostics. The public summary contains only allowlisted exception classes, failure categories, stage and counts. It excludes exception messages, test identifiers, DSNs and raw logs, and verifies the raw JUnit digest recorded by the source-bound native runner. Seventy-five focused tests passed. The hosted summary established that all 24 errors were `OperationalError` / `database_connection_refused` during fixture setup.

Commit [a7931157a0a587f41300e4a241426e5e9c443c4e](https://github.com/steliosx/zab-al-ack/commit/a7931157a0a587f41300e4a241426e5e9c443c4e) makes the PostgreSQL healthcheck use explicit TCP. The image's temporary initialization server accepts Unix sockets, so the previous socket healthcheck could report readiness before the final TCP server. This correction alone did not fix hosted connectivity; the fresh run retained the same 24 connection-refusal errors.

A second defect explains that outcome: the services use an internal-only Docker bridge, while host pytest expects published localhost ports. Docker 28.0.4 skips `ProgramExternalConnectivity` for internal networks; port mappings are installed through that skipped path. The native runner must retain isolation and provide an explicit host-to-lab connection path. See the [Docker endpoint implementation](https://github.com/moby/moby/blob/v28.0.4/libnetwork/endpoint.go), [bridge implementation](https://github.com/moby/moby/blob/v28.0.4/libnetwork/drivers/bridge/bridge_linux.go), and [PostgreSQL image entrypoint](https://github.com/docker-library/postgres/blob/master/docker-entrypoint.sh).

Commit [89a93b1f641a0f978b04862c747b8d4161df3f5a](https://github.com/steliosx/zab-al-ack/commit/89a93b1f641a0f978b04862c747b8d4161df3f5a) fixes the connection path with lifecycle-managed host loopback TCP relays. Before contacting either service, the runner verifies the exact fresh Compose project, service, running container, internal bridge and assigned private address. Both localhost ports are reserved before startup, and relays close before Compose shutdown. Port publishing is removed; lab usage instructions now call the managed runner. Existing test DSNs, database assertions and the native packaging requirement are preserved.

The correction passed 115 focused tests covering concurrent binary streams, directional EOF, ownership rejection, port collision, cleanup, stale-evidence prevention and failure propagation. Two independent reviews found no blocking defect. All 311 files and modes in the resulting GitHub tree were compared with the expected local content, with no mismatch. Hosted verification succeeded: [34804234276](https://github.com/steliosx/zab-al-ack/actions/runs/34804234276).

The fresh hosted native run passed all 42 tests with no failures or skips. Every phase reported successful shutdown (exit code 0):

| Native phase | Passed |
| --- | ---: |
| PostgreSQL | 24 |
| Delivery faults | 5 |
| Worker | 4 |
| Alerta/PgBouncer | 7 |
| Performance | 2 |

The run's implementation/SQL digest is `15ec73b619509428a88b6211737e54339147033dfb1d6ea44090538c21806b5b`; its harness digest is `7a8d9de3915c2c20872bc46ad92c8240f882fe8dad26eadf710bb18b73719c3a`. Packaging successfully collected this workflow's evidence, generated acceptance mapping, passed the public audit with no findings, built the candidate artifacts and uploaded them to the workflow. This is a CI artifact build, not a tagged release or package-registry publication.

The Compose runner requires a local Linux Docker host with direct routing to its bridge containers. Docker Desktop/rootless routing is not assumed; unavailable routing fails the phase. An unrelated process or existing socket state occupying a required localhost port also causes a clean failure. The runner does not change global network settings or stop unrelated listeners.

Nonblocking timing observation: a separate synthetic fragmented-frame probe measured approximately 44 ms median round trips with the current relay socket defaults, compared with 1–2 ms in an in-memory `TCP_NODELAY` variant. That demonstrates possible relay latency, not a PostgreSQL failure; the complete hosted native suite, including its performance checks, passed. Timing results describe this declared lab topology. Socket tuning is a possible later improvement.

Run: [Alpha reference checks, 34797043917](https://github.com/steliosx/zab-al-ack/actions/runs/34797043917).
Native job: [103832046134](https://github.com/steliosx/zab-al-ack/actions/runs/34797043917/job/103832046134).
Diagnostic run: [34803251875](https://github.com/steliosx/zab-al-ack/actions/runs/34803251875).
TCP-healthcheck run: [34803409827](https://github.com/steliosx/zab-al-ack/actions/runs/34803409827).

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

Those original reports remain bound to candidate `e9dc46d2202f0a60638b5506f75af2da11643357`. They do not describe the later CI maintenance commits. Historical reports committed on main have not been relabelled as current evidence; fresh CI artifacts belong to their actual run.

## Hosted checks after the correction

| Check | Result |
| --- | --- |
| Quality | Success |
| Reference and PostgreSQL WASM | Success |
| Mutation | Success |
| Dependency and secret scan | Success |
| Native PostgreSQL/Compose | Success: 42 passed, 0 failed, 0 skipped |
| Packaging | Success |
| CodeQL | Skipped by the existing opt-in setting |
| Deployment E2E | Not invoked |

All rows describe final commit `89a93b1f641a0f978b04862c747b8d4161df3f5a`.
Alpha run: [34804234276](https://github.com/steliosx/zab-al-ack/actions/runs/34804234276).
Security run: [34804234254](https://github.com/steliosx/zab-al-ack/actions/runs/34804234254).
CodeQL run: [34804234331](https://github.com/steliosx/zab-al-ack/actions/runs/34804234331).

## Repository layout and preserved history

| Branch | Contents |
| --- | --- |
| `main` | The reviewed a16 plugin source plus CI and lab maintenance corrections. |
| `website` | All 34 website files unchanged from the archive; verified bytes and Git modes. |
| `handoff/a16` | Original complete ZIP, original Git bundle, this current review and retained independent-review records. |

The authenticated GitHub API was used because the earlier CLI attempt lacked credentials. GitHub starts with an import commit; original candidate commit identities and the 16-commit bundled lineage are retained in the attached original ZIP and this branch's `source-history/zabalack-0.1.0a16.bundle`. No history was force-pushed.

Automatic approval review rejected creation of the handoff tree, stating that the complete archive/history payload had not been explicitly authorized for this destination. The account was subsequently verified as `steliosx`, the repository as private and push-enabled, and the proposed ZIP as byte-identical to the user's upload. A Gitleaks scan of the prepared handoff found no leaks. The branch was left pending until the user supplied precise authorization; the rejection was not bypassed. Main and website were read back and verified successfully.

After the user said “continue”, a single retry was also rejected: automatic approval did not accept that wording as authorization for the original complete ZIP, Git-history bundle and review records at `handoff/a16` in `steliosx/zab-al-ack`. At that point no handoff branch was created. The proposed handoff ZIP remains unchanged as the historical proposal prepared before the later native CI work.

The user then explicitly authorized uploading the original a16 ZIP, Git-history bundle and review records to `handoff/a16` in `steliosx/zab-al-ack`. The upload succeeded. [Archive commit 0a536674505f01194c41f42fe8f549d60e84acf2](https://github.com/steliosx/zab-al-ack/commit/0a536674505f01194c41f42fe8f549d60e84acf2) preserves the exact original 37-file proposal. Every Git blob and file mode was read back and matched the local payload, and the ZIP/bundle SHA-256 checksums were validated. The bundle verifies as complete history. A subsequent documentation update brings this review, the landing page, import status and checksum manifest up to date; the original ZIP, bundle and 30 independent-review files remain unchanged.

The Apache-2.0 license and supplied ignore rules were preserved. Per-deployment G1–G8, amendment A2 and the remaining public-release inputs are still open as documented in the source. The repository remains a private development repository.
