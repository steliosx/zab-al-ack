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

Extract the source or restore its Git bundle, then open that checkout in Codex.
The [official skill documentation](https://learn.chatgpt.com/docs/build-skills)
describes repository skill discovery. The three skills live under
`.agents/skills/`. If the host does not discover them, explicitly ask it to read
the relevant SKILL.md and AGENTS.md. Uploading a ZIP alone does not establish
that a host automatically loads repository instructions. No API key or model
pin is required for the plugin. Use the model available in the contributor's
agent host; record the actual evaluation context and its limits.

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
