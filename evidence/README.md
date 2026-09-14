# Zab-al-ack a16 evidence

Current counts and limits are in [ALPHA_REPORT.md](../ALPHA_REPORT.md). Every
record identifies the source/harness bytes it executed on. The native
(`native-run.json`, 42 passed), real-Zabbix (`real-zabbix-run.json`, 6 passed
including the A4 excluded-action denial) and deployment-path
(`deployment-e2e-run.json`, 4 passed, 13 observations, installed a16 wheel)
records were produced on local services by the R14 runners (`not_docker: true`).
They were executed on a16. Unchanged service-path code supports an inference
about a14 and a15; a14's own records remain NOT_RUN and a15's stand on a15's
runs.

The reference run has 865 passes and 23 skips: 836 unit, mocked, property and
offline cases plus 29 PostgreSQL WASM cases. Profiles and repeated scenarios
overlap and are never added to the unique acceptance total. `mutation-pr-run.json`
and `mutation-nightly-run.json` are the per-profile records a14 introduced;
they are validated when present and optional when absent, and `mutation-run.json`
remains the latest-profile pointer. From a15 every record here is published 0644
and the export audit rejects an exported file that is not readable to group and
other (R20); from a16 ten writer regressions pin both public writers, both
private writers and both umasks (R21).

The independent a12 and a15 reviews' records and a14's qualitative agent
evaluations are retained in the complete handoff as received and are **not**
rebound to this tree — including the a15 review's local Gitleaks result, which
is its evidence and not this candidate's. The browser captures are the a12
images, not recaptured here.

The public export and acceptance tools still reject stale, malformed or
contradictory evidence. G1–G8 remain pending; A2 is unresolved. Docker Compose,
hosted workflows, external security review and signed attestation remain
unexecuted or pending.
