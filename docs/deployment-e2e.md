# Deployment-path service lab

The Docker form of this opt-in lab is designed to exercise the installed wheel
through ordinary application and worker processes. The historical Ackline
0.1.0a8 report recorded all four service cases on local processes without
Docker from an editable source installation (`docs/RELEASE_AMENDMENTS.md` R12);
that result did not establish the packaged-wheel service path. 0.1.0a10 supplies
the local runner (`tools/run_deployment_e2e_local.py`, `docs/local-services.md`,
R14) and records the four cases from a runner environment in which the built
wheel is installed non-editably, with the installed bytes verified against the
wheel; that historical run did not execute the Docker Compose form below. Offline
orchestration tests are separate and establish no PostgreSQL authentication,
Zabbix permissions, notification or macro-expansion result.

Before export, `tools/deployment_evidence.py` checks the reported case counts,
observations, cleanup, versions, runtime identities and source binding. The same
check runs during archive verification and before the Docker runner records
PASS. This is evidence consistency, not proof of authorship. See
`docs/INDEPENDENT_A8_REVIEW.md` for the reproduced failure and remaining scope.

Two behaviours of a real Zabbix 7.0.10 that the first execution exposed are now
built into the lab: an imported media-type script is stored with CRLF line
terminators (the shipped script is compared with normalized terminators), and
the server evaluates actions from a configuration cache refreshed every
`CacheUpdateFrequency` seconds, so the lab waits a bounded grace after creating
the source action before raising the problem. Run the server with a clean
environment: libcurl inside `zabbix_server` honours `HTTPS_PROXY`.

On a development host with a local Unix-socket Docker daemon and Compose:

```bash
python tools/run_deployment_e2e.py --isolated-lab --plan
python tools/run_deployment_e2e.py --isolated-lab
```

To qualify a previously produced wheel without rebuilding it, supply both its
path and independently obtained SHA-256:

```bash
python tools/run_deployment_e2e.py --isolated-lab \
  --wheel /private/artifacts/zabalack-0.1.0b4-py3-none-any.whl \
  --wheel-sha256 "$EXPECTED_WHEEL_SHA256"
```

This mode checks the archive hash, layout, project/version and complete package
payload against this checkout before contacting Docker. The image contains no
`src` directory and installs only that wheel, without dependency resolution or
a fallback build. It verifies installed package files, metadata, entry-point
targets, version and import origin both at image build and immediately before
service tests. A PASS requires the matching installed-wheel receipt. The public
record adds `artifact_mode: supplied_wheel` and the wheel filename, version,
SHA-256 and installed-byte verification result; raw receipts stay private.
Bind the artifact's producer revision/provenance separately from this runner's
source/harness digests. A changed harness does not transfer old qualification
results to its new revision. Legacy source-build/local records remain scoped
to their original execution and do not imply this exact-artifact mode.

The runner builds a Python 3.12.10 image from `requirements.lock` and a wheel from
the current source by default. Services use PostgreSQL 18.6, PostgreSQL 16.4 for the source
server, and Zabbix 7.0.25 server/web images. Version tags are explicit; a successful
run records the actual Docker image IDs. Image pulls/build dependency downloads
need ordinary registry access. The runtime network is a new internal Compose
network with **no published ports**, host networking, Docker-socket mount or
external endpoint input. A random project name and fresh named volumes avoid
reusing an existing service. An inherited remote Docker context is refused.

The application runs the installed `zabalack.plugins:create_app()` factory through
the Flask CLI HTTPS server. This is a real HTTP process and upstream factory,
with the actual registered plugin entry points and file-based configuration;
it is a lab server, not a claim about a production Gunicorn/systemd deployment.
No test client, injected JWT/actor, injected health probe, patched persistence or
runtime `SET ROLE` facade is used. The worker runs the installed `zabalack` CLI
in another process. Its source endpoint is a fixed HTTPS loopback proxy to only
this project's Zabbix API; the production HTTP client verifies a generated lab
certificate. No source URL or token is accepted from a caller fixture.

The four service cases require these observations:

| Case | Required observations |
| --- | --- |
| Installed authenticated path | Real signup/login; human `/action`; one committed capture; one source acknowledge/comment; successful projection; repeated action returns HTTP 409. |
| Customer and actor scope | A separately logged-in outside customer cannot read/acknowledge the alert; a customer-bound ingestion key can create/local-acknowledge a separate alert but produces no capture. |
| Native privileges | Distinct password-authenticated app, worker, probe and operator sessions; direct worker table update and probe table read fail under their real logins. |
| Media and updates | Import the shipped disabled media artifact unchanged; configure only private lab parameters; trigger a real problem via `history.push`; require macro-expanded ingestion; observe a completed update webhook and no duplicate ingestion for the neutral write-back comment; require a human source comment to ingest; recover the problem and preserve its original problem ID alongside the distinct recovery ID. |

The Zabbix worker user has a single synthetic host group and API method allowlist.
The action matches exactly one generated trigger and one private webhook
recipient. It contains no email or command operation. A completed source alert
record is required before treating a suppressed update as evidence: silence or
a failed delivery does not pass. Credentials are generated privately; the
Zabbix default account is used only to provision its new disposable instance.
Existing enabled source actions or an existing synthetic host cause refusal.

`evidence/deployment-e2e-run.json` is invalidated before daemon discovery, bound
to source/harness digests before and after execution, and can become PASS only
when all four exact cases and every required observation pass, image identities
are recorded (or, for the local runner, `not_docker: true` with component and
runner identities), the tree is unchanged, and cleanup succeeds. Exit 2 means NOT_RUN
because the local runtime is unavailable/refused; exit 1 means execution,
validation or cleanup failed. Public output contains fixed check names, booleans,
counts and reference versions only. Service logs, generated credentials and raw
JUnit remain under the private generated directory in `build/private/`; they
are excluded from source/release exports and must not be uploaded as CI artifacts.

Cleanup stops the exact generated runner container after checking its project
label, service label and full name, including after a timeout. It then stops only
that generated Compose project. It retains volumes and private logs for local
inspection; it never resets acknowledged events or deletes database volumes.
The generated `compose.json` identifies those resources for an engineer's
separately chosen local cleanup. Do not share that file or its private directory.
The source DB password is mounted as one read-only file readable by the pinned
Zabbix image's UID 1997; its host parent is mode 0700. Application keys and other
DSNs are not mounted into Zabbix containers.
The runner container uses the numeric UID and GID of its generated private
directory. This lets the installed application read owner-only bootstrap and
secret files and leaves generated mode-0600 results readable by the host owner.
Protected-file ownership checks and modes remain unchanged; the other service
containers retain their pinned images' identities.
The generated nonsecret build context recursively normalizes copied directories
to mode 0755 and files to mode 0644, including when source permissions or the
host umask are restrictive. The runner can then traverse and read the copied
source after Docker assigns it root ownership. Source permissions remain
unchanged, and the enclosing private directory stays mode 0700.

Two reference limitations are explicit:

- Pinned Alerta 9.1.0 runs schema ALTER statements on every factory startup. The
  lab application therefore logs in as the actual Alerta table owner, a trusted
  installation identity. Zab-al-ack's non-login migration owner and restricted
  worker/probe/operator remain separate. This does not prove a non-owner Alerta
  deployment; `docs/deployment.md` describes the existing ownership gate.
- The pinned Zabbix webhook `HttpRequest` disables peer/host certificate
  verification and follows redirects. HTTPS ingestion in this isolated lab
  therefore **does not prove authenticated TLS**. See amendment A2 in
  `docs/SPEC_AMENDMENTS.md`. Worker-side HTTPS uses its independent verified
  client. Production transport and redirect containment remain open inputs.

This suite does not establish installed customer/cohort permissions, production
notification behavior, deployment topology, PgBouncer, multi-process Gunicorn,
Ansible/systemd installation, UI behavior, HA or G1–G8. The separate native lab
covers its own PostgreSQL/PgBouncer cases. A successful service lab run supplies
reference evidence only; the installed-environment gates stay open.

Inspected interfaces: Alerta 9.1.0 `auth/basic.py`, `views/keys.py`,
`database/base.py`, `database/backends/postgres/base.py` and `sql/schema.sql`;
Zabbix 7.0.10 commit `237358b56b2584b94b1f8358f479ade669693ba2`
`CUser`, `CUserGroup`, `CRole`, `CToken`, `CAction`, `CMediatype`,
`CConfiguration`, `CHistory`, `CAlert` and `CEvent`; official Zabbix Docker
7.0.10 commit `8593930481428e518ac239a785629fb574b88a46`
server/web Dockerfiles and entrypoints. No interface was inferred from a mock's
successful return alone.

Those inspections were made at 7.0.10, and the lab keeps that commit only as a
historical inspection identity (`INSPECTED_SOURCE_COMMIT_7_0_10`). The 7.0.25
reference has no inspected-commit pin yet; the official source tarball is
identified by its SHA-256 (`3a42e85da9cbd8aadd11bfcbe021f0f9c6caa510f564e37f3ac1046984acb729`),
and upstream's Git tag `7.0.25` pointed at commit
`d607227714fc00ec6117e663d0934cb90c0f842b` when read on 15 October 2026. For the
7.0.25 reference, only a static source comparison has been made so far: the official 7.0.25 tarball's `CEvent`,
`CTask`, `CRole`, `CToken`, JSON-RPC and schema files against 7.0.10 and 7.0.29.
The other interfaces above and the 7.0.25 Docker images are covered only by a
7.0.25 lab run.

For a hosted isolated run, the supplied `deployment-e2e.yml` workflow is manual
and requires `ZABALACK_DEPLOYMENT_E2E_ENABLED=true` after repository policy review.
It uses no custom secrets, has read-only repository permissions, and uploads only
the fixed public result JSON even on failure. It is separate from the required
quality/reference/native/mutation packaging workflow; a service result is not
silently imported into that workflow's acceptance evidence. Hosted execution and
repository enablement are NOT_RUN here.
