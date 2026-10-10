# Local Preloop and SmolVM quality lane (alpha)

Status: native alpha, **quality job only**. On 2026-10-10 (UTC) a local
[Preloop](https://github.com/preloopdev/preloop) engine ran the `quality` job
in a single-use SmolVM microVM on an x86_64 Linux laptop. Its GitHub App
reported check run `114114591155` as `success` on pilot commit `5095eb48`
(branch `ci/preloop-quality`). ruff, mypy and `pip check` passed with
`tree_unchanged: true`. That commit differs from `main` (`3fb266d3`) only in
the pilot workflow `.github/workflows/preloop-quality.yml`; source and harness
files are identical. This is not full CI, not a substitute for the dedicated
runner described in [self-hosted-runner.md](self-hosted-runner.md), and not
release acceptance.

Dates are UTC. The four exact lane files that ran are retained privately with
the run evidence; their SHA-256 values are listed below so a reviewer can verify
those copies without them being published here.

## Pinned stack

| Component | Pin |
|---|---|
| Preloop CLI | v0.33.9, binary SHA-256 `cb753967…`, release commit `77c822f9…` |
| SmolVM | 1.19.0, `smolvm-bin` SHA-256 `9133f40b…` |
| Guest image | Preloop release pack `preloop-ubuntu-24.04-x86_64`, 1,273,088,056 bytes, SHA-256 `618f7970217f039ff92d9c0d4aa03c580f8108f6b2c417d6e0f8a4bfa1d96a4d` |
| Guest resources | 1 vCPU, 3072 MiB, 20 GB; packed golden on, fork off; the release pack set as a custom base so no image bake runs in the job |
| Pool labels | `self-hosted, Linux, x86_64, X64` (`PRELOOP_RUNNER_LABELS=X64`) |
| Workflow `runs-on` | `[self-hosted, Linux, X64, ubuntu-24.04]` |
| Engine service | rootless systemd user unit in an isolated home, listening on `127.0.0.1:9090` only |
| Check reporting | a repository-scoped GitHub App installed on this repository only; job tokens capped at read |

The guest has outbound internet for the alpha, limited to public addresses
(`SMOLVM_EGRESS_FLOOR=strict`). Pinned action commits and the hash-locked
requirement files are still the dependency controls.

## Lane contracts

**One VM per attempt.** A `smolvm` wrapper placed first on the engine's `PATH`
holds a single persistent create permit. It forwards exactly one
`machine create`, and only when all of the following hold:
- the full argument vector matches the reviewed runner name, image pack, data
  root, resources and mounts;
- the public-only egress floor is set;
- the engine, runner, SmolVM, libkrun and image checksums match their pins;
- at least 64 GiB is free.

It creates the permit directory atomically before forwarding and never removes
it, even if the create fails. Other calls must name that one machine: listing,
status, start, stop, forced delete, `exec` with a validated option set, and
copies to or from that guest's own paths. Anything else, including a second
create or any call after the grant's expiry time, is refused with a
`PILOT_CREATE_GATE` line. Each attempt uses a fresh permit directory and a fresh
SmolVM data root. Consumed permits are never reset.

**Bounded runtime.** The unit sets `RuntimeMaxSec=480s`, `TimeoutStopSec=15s`,
`Restart=no` and a one-start limit per day. The bound comes from the engine:
Preloop's orphan sweep first runs at least 600 s after start and deletes the
SmolVM shared-extraction store a running VM depends on. 480 s plus the stop
timeout ends the service before that sweep. The supervisor's episode budget is
450 s.

**Supervisor.** One script starts the socket and service, waits for the
engine's status to show the expected pool labels, dispatches the workflow once
over the engine's local REST endpoint and never retries. It then polls the
GitHub check. It stops on check completion, the episode budget, a gate
refusal before completion, more than one VM or a registration deadline.
Cleanup runs once from an exit trap within 120 s. It cancels the one run,
stops the units, and requires all of the following:
- unit `MainPID` 0;
- an empty SmolVM registry;
- no `libkrun VM`, `preloop` or `smolvm-bin` process;
- no listener on 9090 and no control socket;
- the permit still marked consumed.

**Exact files that ran** (attempt 7, retained privately with the run evidence):

| File | SHA-256 |
|---|---|
| create gate | `26de8e76f699265bc14810372886bc0d8cbb74f556b354378c08adbc1efa6804` |
| engine service unit | `99f28ededcaa8cfa85c79568fa324c2ad94a077f10d5963f7c7ce7d5d8df9691` |
| engine socket unit | `4afc74b204625620440243953cd535883aa8c49b5d7118078513ceadd005d2f1` |
| supervisor | `9aa02d4c2f32beb854a051f62cfa0dc97f440c880f2884240be9ad5bb24e0d62` |

These files are bound to that host, attempt and grant window. They are a
record, not a reusable tool; a reusable lane is deferred until a second job or
the evidence mode below gives its parameters real use.

## Pilot workflow differences from `ci.yml`

`preloop-quality.yml` runs the `quality` job's trust guard, checkout, Python
setup, fresh virtual environment, the three pinned installs, `pip check` and
`tools/run_quality.py`, unchanged in command. It differs from `ci.yml` in four
ways:
- it is dispatch-only and targets the local pool;
- its checkout and Python actions are pinned to their Node 24 releases;
- it omits `collect_ci_evidence.py prepare`/`pack` and the artifact upload
  (see the UUID gap below);
- it prints the fresh `evidence/quality-run.json` into the job log, so the
  digests, tool versions and per-check results survive the deleted VM.

That last step runs only when every earlier step succeeded. A failing quality
check therefore leaves only `run_quality.py`'s per-check status lines.

## Engine gaps found

Each gap below stopped an attempt; attempts 1–6 are retained privately.

- Runner label `X64` versus the pool's default `x86_64`: the job was refused at
  enqueue. Fixed with `PRELOOP_RUNNER_LABELS=X64`.
- With the stock base, the engine bakes dependencies into each runner VM. Its
  orphan sweep then deleted the shared extraction store mid-job (`ESTALE`). Fixed
  by the custom base and the 480 s bound.
- Per-machine extraction (`SMOLVM_DISABLE_SHARED_EXTRACT=1`) failed VM start
  with `EINVAL` under SmolVM 1.19.0. Shared extraction is required.
- Node.js 20 actions are refused since 2026-09-23, as on GitHub. The pilot
  moved to Node 24 releases; `main`'s workflows need the same change, proposed
  separately on branch `ci/node24-action-pins`.
- `GITHUB_RUN_ID` is a UUID and `run_attempt` is always `1`.
  `collect_ci_evidence.py` requires GitHub's decimal identifiers and correctly
  refuses the context ("Invalid run context"). The chosen direction is an
  explicit, separately labelled local-alpha producer mode that records the real
  UUID while the GitHub validator stays strict. It does not exist yet.

None of these has been reported upstream.

## Not claimed

- No job other than `quality` has run on this lane: `reference`, `native`,
  `mutation`, `package`, the scanners and CodeQL are unproven here.
- No evidence bundle, artifact upload or candidate package was produced.
- `main`'s committed `evidence/quality-run.json` (2026-09-13) predates the
  current tree and was not regenerated by this run.
- The dedicated ARM64 runner policy, its holds and every deployment gate are
  unchanged.
