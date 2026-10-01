# Claude Code sessions

This adapter covers how agents running in Claude Code (currently Opus and Fable)
load repository guidance. Policy is shared: [AGENTS.md](../../AGENTS.md) and
[agent workflows](../agent-workflows.md#coordination). Current assignments are
in issue #13.

## Instruction loading

Checked against the Claude Code memory and skills documentation for v2.1.280 on
2026-09-22. Recheck after upgrades.

- Claude Code loads `CLAUDE.md` and `CLAUDE.local.md` from the working directory
  and every directory above it at launch. Files in subdirectories load only when
  Claude reads a file there.
- It reads `AGENTS.md` directly only when no `CLAUDE.md`, `.claude/CLAUDE.md`
  or `CLAUDE.local.md` exists in the working directory or above it (the default
  setting). A private instruction file above a checkout therefore suppresses
  direct `AGENTS.md` loading. Some sessions cannot read `AGENTS.md` directly.
- The repository `CLAUDE.md` imports `AGENTS.md` and this file with `@` imports,
  so a session started in a checkout or worktree loads both in either case.
  Imports resolve relative to the importing file, up to four hops, and are not
  expanded inside code spans or blocks. A session started above the checkout
  loads the repository `CLAUDE.md` only after reading a file inside it; read
  `AGENTS.md` explicitly before repository work.
- Claude Code discovers skills under `.claude/skills`, not `.agents/skills`.
  Read the SKILL.md that `AGENTS.md` routes a task to.
- A subagent that skips project instructions does not receive these files.
  Give it the rules its task needs.

## Startup

1. Confirm the checkout or worktree you will change and that `AGENTS.md` is in
   context.
2. Refresh issue #13 and your own state record. Resume only your own claims.
3. Work on your own branch and worktree. Another agent's branch, worktree or
   controller is read-only to you unless its owner hands it over.

## Agent notes

- **Opus** runs on the owner's x86_64 laptop, which also hosts the x86_64 runner
  VM. Its private workspace instruction file sits above the repository checkout,
  so the explicit `AGENTS.md` read or the repository import applies. Host work
  on that laptop is not excluded by the runner guest's lab lock.
- **Fable** records the delivery form of its current task in issue #13; its
  Docker-removal commits stay local as patch bundles for now.

Neither agent has a separate standing-instruction file in this repository.
Authorizations come from the owner's instructions to that agent, not from this
file.
