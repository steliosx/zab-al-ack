# Agent workflows

The project guidance implements the requested 1–4 and 6 before the behavioural
checks in 5. It applies to maintenance of this repository; it does not add an AI
component to the acknowledgement pipeline or send alerts to a model.

| Item | Implementation | Purpose |
|---|---|---|
| 1 | Task routes in AGENTS.md | Read the affected design, lab or release documents when needed. |
| 2 | Three .agents/skills entrypoints | Separate release, isolated-lab and migration work with narrow descriptions. |
| 3 | Completion examples below | Carry local work through repair and a reviewable result. |
| 4 | tools/run_review.py and separate mutation reports | Retain attempts and serialize cooperating writers. |
| 6 | tools/agent_guidance.py plus build/verify integration | Check instruction bytes in both source distributions; fix stale release examples. |
| 5 | evals/agent-workflows/cases.json and handoff review | Exercise realistic agent tasks after implementation, independently of unit tests. |

This adapts OpenAI's [skills and prompts article](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra)
(2026-09-11): keep discovery precise, route detail by task, state completion and
allow already-authorised local follow-through. Safety invariants remain fixed.
The agent evaluations are qualitative observations, not a controlled comparison
of models, token use or speed.

## Using the repository skills

Extract the source or restore its Git bundle, then open that checkout in the
agent host. The three skills live under `.agents/skills/`. Codex discovers them
there ([skill documentation](https://learn.chatgpt.com/docs/build-skills));
Claude Code does not, so Claude sessions read the routed SKILL.md explicitly
([Claude Code adapter](agents/claude-code.md)). If a host does not discover them,
explicitly ask it to read the relevant SKILL.md and AGENTS.md. Uploading a ZIP
alone does not establish that a host automatically loads repository instructions.
No API key or model pin is required for the plugin. Use the model available in
the contributor's agent host; record the actual evaluation context and its limits.

## Coordination

Issue #13, "Agent coordination", is the live record of claims, assignments,
handoffs and results; each worker's existing state file records its own run
progress. This section holds durable rules only. Dated assignments stay in #13
and the [snapshot](#current-assignment-snapshot) points to them.

### Roles and assignments

Implementation, review, integration, documentation and validation are roles
assigned per task. Any agent can hold any role when the owner assigns it or its
own standing authorization covers it. An assignment lasts until the task is
completed, released by its holder or reassigned by the owner. It is neither a
permanent limitation of that agent nor, once finished, a standing right.

Older documents name agents, for example "review with Opus", "Fable is advisory"
or "Opus owns native x86_64". These record the assignment in force when written.
The requirement behind the name still applies (for example, a non-author
technical review of an execution-policy change); the owner assigns who performs
it. A global x86_64 deferral and ARM64-only priority were earlier milestone
decisions, superseded by task-scoped x86_64 work; see the snapshot.

### Authority

Authority for an action comes from the owner's explicit instructions to the
agent doing it, including denials, and from that agent's standing controller
instructions. Repository guidance grants none. Agents post in #13 through the
owner's account; the leading name identifies the writer. An agent-written
comment, including one that records an owner decision, is a record, not by itself
a new owner authorization, and it grants nothing to another agent. Ask the owner
when a recorded decision is ambiguous.

Already-authorized local work proceeds without repeated confirmation. Merges,
releases, production changes and runner, label, service or privilege changes
each need their own explicit authorization. Special
permissions stay with the agent that received them; a handoff or citation does
not transfer them. Temporary authorizations end with their task: the 2026-09-22
runner-22 maintenance window (remove the custom label, drain, stop and restore
the listener) covered one exact-wheel baseline and is not a right to stop
services, change labels or alter privileges later. Outstanding approval denials
stand; never retry a denied action through another agent, CI or another route.

### Claims and task records

Claim a task in #13 before edits and keep its record in that claim, updated by
the closeout, not in a parallel tracker:

| Field | Record |
|---|---|
| Scope | What is included and excluded. |
| Owner | Holding agent, and who assigned the task. |
| Branch/base | Own branch or worktree, full base SHA, fixed candidate/artifact identities. |
| Environment | Host and architecture, execution form, runner or lab, locks needed. |
| Allowed actions | The authorization relied on, including any task-scoped exception. |
| Evidence | What is retained privately, where, and what may be posted. |
| Blockers | Task-specific blockers and the condition for resuming. |
| Handoff | Exact SHAs, hashes, commands, NOT_RUN items and open findings. |
| Completion | Criteria, and who accepts completion. |

A quiet owner's claim stays with that owner. Elapsed time or inactivity does not
transfer it; only its holder's release or the owner's reassignment does. Work that
is independent, unclaimed and already authorized may continue meanwhile without
touching the claimed branch, worktree, checkout, lab resources or artifacts.
Review or validation of another agent's work starts from its finalized handoff
(exact SHAs and checksums), not from work in progress.

### Review

Keep three things distinct and record each reviewed SHA or range and scope:

1. **Author verification**: the author's own tests. This is implementation
   validation, not review.
2. **Technical review**: by an appropriate non-author agent or person assigned
   to the task. Automatic Codex PR review counts only for its actual coverage.
3. **Formal approval**: GitHub approvals, branch protections and owner
   decisions. Because agents share the owner's account, their technical review is
   not formal GitHub approval.

An optional advisory request does not block independent work; a required review
blocks readiness until it is done. Pending or unperformed review is never PASS.
Fix or explicitly disposition substantive findings from any reviewer.

### Runners and execution venue

Select a runner or lab by capability and approved access, not by which agent
executes the task. Record separately:

- **Architecture**: ARM64 or x86_64; one never establishes the other.
- **Execution form**: native processes, Docker Compose lab, installed wheel or
  installed environment.
- **Run type**: a manual/local run is local evidence, even on a runner host.
  Actions provenance needs the run ID, attempt, runner, labels and head SHA.

Workflow runner routing is CI policy, documented in `docs/ci-evidence.md` and
`docs/self-hosted-runner.md`; changing it needs its own review.

### Resources and locks

A lock excludes only the parties that take it. Workflow jobs take the shared lab
lock (`docs/self-hosted-runner.md`) on the machine executing the job. On a
runner VM, that lock is inside the guest: it serializes guest jobs and agent labs
that also take it, but does not exclude labs, Docker or other work on the
physical host, or another VM. Host-level exclusivity needs explicit coordination,
such as an owner-approved runner drain. Record which lock or coordination a run
relied on. The review runner's checkout lock below is also cooperative.

### Current assignment snapshot

Dated 2026-09-22. Issue #13 prevails over this list.

- Astra (local Codex) holds integration ownership and its existing claims
  ([standing instructions](agents/astra.md)).
- Fable is the sole active writer for Docker removal and continues independently
  ([owner reassignment record](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5782499722),
  [clarification record](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5783909836)).
  Its commits stay local as patch bundles until safe job routing or its
  execution handoff exists; each change needs non-author technical review.
- Opus completed the a18 exact-wheel baseline `omarchy-x64-a18-supplied-wheel-01`
  ([closeout](https://github.com/steliosx/zab-al-ack/issues/13#issuecomment-5785803947)):
  PASS only for its four deployment-path cases and 13 observations, with wheel
  `e339fa79…` on a native x86_64 host Docker lab (a manual run, not Actions). It
  does not cover real-Zabbix, Docker-free, A4/G5, A2, G1–G8, cold-source, formal
  review or CodeQL, or a later candidate. Its off-machine evidence retention is
  pending.
- Opus's review and x86_64 validation of Fable's final handoff is a separate,
  future assignment that starts only from that finalized, checksummed handoff.

## Retained local review runs

Install requirements.lock and requirements-quality.lock as in RELEASING.md,
then install the pinned Node lab dependencies with `npm ci --prefix lab
--ignore-scripts --legacy-peer-deps`. Select phases for the task:

```bash
python tools/run_review.py --plan
python tools/run_review.py --isolated-lab --phase quality --phase reference
python tools/run_review.py --isolated-lab --run-id candidate-first \
  --phase quality --phase sql --phase reference --phase mutation-pr
python tools/run_review.py --isolated-lab --phase mutation-nightly \
  --phase property-nightly --phase quickstart
# With explicitly disposable native services configured (docs/local-services.md):
python tools/run_review.py --isolated-lab --phase native-local
# With the protected synthetic fixture from lab/real_zabbix.md:
python tools/run_review.py --isolated-lab --phase real-zabbix
```

Every invocation gets a fresh UTC/random directory in `build/review-runs/`, or
a single-use explicit run ID. The runner records phase commands, exit codes,
source/harness digests, start/end times and changed JSON/JUnit evidence. Existing
unchanged reports are not copied as fresh results. Captured stdout and snapshots
are private; review/redact them before public export. These files can include
fixture information. The checkout's conventional evidence paths remain compatible
with existing acceptance and CI tools.

The runner holds a nonblocking local checkout lock, runs phases sequentially and
stops at the first failure or unavailable phase. Later phases are NOT_RUN. Run
independent remaining phases under a new invocation when useful. Direct runner
commands retain their existing behavior: do not run other evidence writers in the
same checkout concurrently. Separate agent tasks use separate disposable copies.
This is cooperative coordination, not protection from a privileged file editor.

After interruption, preserve the old directory. A surviving RUNNING record is
unfinished. The wrapper terminates the child process group on timeout/interrupt,
but reports cleanup as NOT_CONFIRMED_AFTER_INTERRUPTION: killing a client does
not prove that native services or detached containers stopped. Inspect the owning
runner's cleanup record and the exact disposable service instance. Do not blindly
resume an unknown external write. A new run never modifies an old run record.

Mutation checks keep every redacted outcome report under
`build/private/mutation/run-*/report.json` and maintain independent
`evidence/mutation-pr-run.json` and `evidence/mutation-nightly-run.json` records.
`mutation-run.json` remains the latest compatibility record. A lock prevents two
mutation writers sharing that pointer. A new attempt invalidates its current
profile and latest pointer before running; historical attempts remain intact.
A profile report present in a release must pass validation for that exact profile
and source/harness. Delete no failure merely to pass an export check; preserve
attempts in the handoff review and replace current evidence only by fresh runs.

## Completion examples

- **Documentation correction:** “Fix the incorrect command on this page and
  check the affected example/links. Finish with the diff and what you verified.”
  No full service lab is required for a typo.
- **Trust-boundary repair:** “Reproduce the customer-scope issue with a synthetic
  negative case, repair it without broadening authority, run the affected tests
  and report the invariant and results.” Preserve the frozen baseline.
- **Interrupted lab:** “Inspect this unfinished disposable run, preserve it,
  establish cleanup, rerun the required phase with a new ID, and record both.”
- **Alpha handoff:** “Run the available candidate checks, repair defects, label
  unavailable layers, build and verify source/wheel/sdist, include the website,
  full justification and open inputs, and return one ZIP.” Stop before any
  external publication or production operation unless separately authorised.

For candidate export, regenerate acceptance from final runs using
`python tools/update_acceptance.py`. A focused review does not need to regenerate
unrelated release evidence. G1–G8, A2 and publication-owner inputs remain open;
local successful tests do not close deployment or publication gates.
