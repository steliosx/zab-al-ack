---
name: zabalack-release-review
description: Review and package a Zab-al-ack alpha candidate with source-bound evidence. Use for candidate exports or release artifact reviews.
---

Read [release preparation](../../../docs/RELEASING.md) and the
[workflow commands](../../../docs/agent-workflows.md). Review the requested
candidate or artifact set; a documentation edit does not require release review.

For an inspection-only request, verify supplied bytes and evidence read-only and
return findings. Do not run tests, rebuild, repair or replace records, or create
a release ZIP unless the requested scope includes those actions. The preparation
steps below apply when preparing a candidate export.

Use a clean checkout and the declared dependency pins. Preserve historical
results separately; executed evidence must match the current source and harness.
Run the candidate gates available in the isolated workspace and repair failures.
Record unavailable service layers as NOT_RUN with the supplied recorders. Never
rebind old evidence, hide skips, or treat local fixtures as G1–G8 acceptance.

Before export, regenerate acceptance from final runs, run the public audit,
build twice and verify the artifacts. The guidance manifest is a separate hash
of instructions, not an agent-performance score or signed attestation.

Return one ZIP containing source, built artifacts, website, changelog with
justification, results, hashes, deployment/rollback guides and the consolidated
missing-input register. Keep raw private logs out. Stop at the reviewed local
handoff unless the user explicitly authorized publication or remote pushes.
