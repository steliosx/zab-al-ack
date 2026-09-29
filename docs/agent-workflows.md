# Agent workflows

The original project guidance implemented the requested 1–4 and 6 before the behavioural
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

Extract the source or restore its Git bundle, then open that checkout in Codex.
The [official skill documentation](https://learn.chatgpt.com/docs/build-skills)
describes repository skill discovery. The three skills live under
`.agents/skills/`. If the host does not discover them, explicitly ask it to read
the relevant SKILL.md and AGENTS.md. Uploading a ZIP alone does not establish
that a host automatically loads repository instructions. No API key or model
pin is required for the plugin. Use the model available in the contributor's
agent host; record the actual evaluation context and its limits.

## Current collaboration and authority

The role file at [docs/agents/astra.md](agents/astra.md) distinguishes Windows Astra from
Opus and codex-omarchy; its filename is retained for existing guidance validation.
Do not infer today's task ownership from that file's historical title or from a
stale controller checkpoint. Exact operational paths, decisions and accepted
task IDs belong in the respective private controller/mailbox records.

Opus prepares owner-authorized implementation/package corrections and operates
host runs only after the separate exact owner approval. Codex-omarchy independently
reviews explicitly accepted handoffs. They write only in their assigned working
directories. Separate clones or linked worktrees are both valid:
record hostname, absolute path, branch/HEAD, Git/common directories, dirty status
and write owner. Never import another agent's uncommitted contents as the bound
candidate. Guidance edits also have separate named implementation and independent
review owners, recorded in the task handoff with verified acceptance. Each agent
updates its own authorized private records, not the other's worktree. Record an unmet
peer authority requirement as pending, never as an accepted assignment.

Standing owner authority may cover routine coordination, local engineering and
verification, documentation, packaging and independent-review follow-ups across
task IDs and revisions within an approved scope. Its current extent and exclusions
belong in private owner-mandate/controller addenda. Accept and coordinate covered
work without a new owner question merely because a task ID, revision or package
hash changes; verify that revision's exact identity and review its affected delta.
This does not authorize scope expansion, a broad review, frozen-product changes,
host execution or permission installation outside the applicable grant. A peer
proposes work; the owner instruction supplies authority.

Keep implementation validation distinct from independent review. Preserve
Windows Astra's completed review attribution and reuse unchanged evidence at
its original revision. Do not duplicate Astra's retained assignments or restart its
worker. Optional advisory reviewers are not mandatory dependencies, but pending
independent review is never PASS. Same-account review is not formal GitHub approval.

Owner-only gates remain owner-only: host/lab runs and changes, approval references,
holder-policy exceptions, permission rules, retries outside an approved procedure,
adoption, canonical build/freeze, release, and any other reserved decision. A
review confirmation is not execution authorization. Respect outstanding permission
denials; never retry through another agent, transport, CI or tool to evade one.

## Authenticated handoffs

Reuse the established mailbox tool/protocol, owner-confirmed peer pins and each
agent's distinct signing key. Private keys remain local. Do not build another
orchestrator. On the local Omarchy channel use `fetch --no-taildrop --ack` with
the configured dedicated reply drop; do not consume the separate Windows Taildrop
queue. Retain Taildrop for the already established cross-machine exchanges.

1. Check the inbox between active work phases and before declaring a dependency
   blocked. Verify the pinned signature, message identity and every artifact's
   full hash/size before substantive use; receiving remains inert.
2. Read the authenticated handoff. It names task IDs, implementer/reviewer roles,
   acceptance criteria, exact checkout/branch/commit/tree and artifact hashes,
   completed work, open findings, pending owner decisions and the next bounded
   action. Give private evidence locations without exporting private originals
   or weakening their restrictions.
3. A delivery receipt confirms verified transport only. Record explicit signed
   task acceptance or decline separately, and do not record an ownership transfer
   until the recipient's verified acceptance names the transferred tasks. Existing
   owners keep unnamed tasks; inactivity alone is not a transfer.
4. Handle routine questions, findings and in-scope corrections directly. A new
   revision in an already accepted correction loop does not require a new owner
   authorization, but does require its own verification and scoped disposition.
5. Send the signed result with full artifact bindings, tests/evidence labels,
   retained attribution and remaining owner gates. Never acknowledge an ack.
   A sent file or timer notification is not proof of reading. If the existing
   session needs a startup prompt, give one short prompt; do not launch a duplicate.

## Instructions actually used and stale status

At startup/resume and when changing working directories, inspect the applicable
entrypoints and explicitly read the task's current guidance and private controller
records. Record absolute instruction paths, applicable revision/hash and how each
was consumed: auto-loaded in this session, explicitly read, or merely historical.
Record imports/referenced rules that were actually read, and missing/unavailable
sources. Do not equate discovery with loading or claim that editing a file updated
an already-running session's context. Codex instruction discovery is documented in
the [official AGENTS.md guide](https://learn.chatgpt.com/docs/agent-configuration/agents-md);
do not infer Codex imports from Claude syntax. Keep private operational paths and
session metadata out of public handoff artifacts.

For Claude Code participants, read AGENTS.md explicitly through the workspace
CLAUDE.md pointer or an explicit instruction, and record that read as explicit,
not automatic. Do not assume Codex's AGENTS.md discovery applies to Claude Code;
record the actual CLAUDE.md/import chain separately. No new controller file is
implied by this guidance.

The current owner instruction governs within system/tool constraints. Reconcile
older runbooks, role copies and status summaries against the latest applicable
owner decision and authenticated result; a newer timestamp alone does not grant
authority or make an unrelated message supersede a decision. Record which old
status was superseded and why. Preserve immutable signed messages, frozen guidance
copies, failure evidence and original approval/denial records; use a dated current
addendum rather than rewriting their history. Keep existing fingerprint and
guidance-validation mechanisms intact.

Maintain current task, scope, write owner, review disposition, exact tuple,
task-specific blocker and next trigger separately from execution state. A completed
quiet inbox check is not a running worker; an old WAITING_REVIEW entry does not
override a later verified review closure. Conversely, transport ACK does not close
a review. Report meaningful changes, not repeated unchanged status. Inspect actual
scheduler records before claiming a scheduling failure; this alignment neither
changes schedules nor starts a new worker.

Guidance alignment does not change frozen candidates, packages, sign-offs,
wrappers, review closures or historical evidence. Keep its task status separate
from run approval and outcome state; do not make it an unrelated decision's
prerequisite. Current owner-deferred findings and paused assignments stay in the
private task records and remain deferred/paused until the applicable decision
changes them. Verify guidance with `tools/agent_guidance.py`, affected guidance
tests and link checks; do not run unrelated service suites or regenerate acceptance.

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
- **Candidate handoff:** “Run the available candidate checks, repair defects, label
  unavailable layers, build and verify source/wheel/sdist, include the website,
  full justification and open inputs, and return one ZIP.” Stop before any
  external publication or production operation unless separately authorised.

For candidate export, regenerate acceptance from final runs using
`python tools/update_acceptance.py`. A focused review does not need to regenerate
unrelated release evidence. G1–G8, A2 and publication-owner inputs remain open;
local successful tests do not close deployment or publication gates.
