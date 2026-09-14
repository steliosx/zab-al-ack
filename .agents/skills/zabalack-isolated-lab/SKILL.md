---
name: zabalack-isolated-lab
description: Run or recover Zab-al-ack tests in a disposable local lab. Use for native PostgreSQL, PgBouncer, real Zabbix or interrupted lab runs.
---

Read [local services](../../../docs/local-services.md) for native processes,
[workflow commands](../../../docs/agent-workflows.md) for retained runs, and
[real Zabbix](../../../lab/real_zabbix.md) when that API layer is requested.

Use only explicitly disposable synthetic services. A missing live inventory is
a deployment gate; it does not stop a configurable reference build. Do not
request real credentials or point lab runners at production endpoints.

Select the runner that matches available services. Plan mode is read-only;
execution requires --isolated-lab and the runner's own fixture checks. Keep
unit/mock, PostgreSQL WASM, native authentication/concurrency and real API
results distinct. Full installed-wheel delivery is a separate service layer.

For interruption, preserve the previous run and inspect its cleanup outcome.
Confirm ownership before stopping leftover local processes. Use a new run ID;
do not overwrite old results or turn RUNNING into PASS. Do not automatically
resume an ambiguous external action. Record an unavailable prerequisite and
continue independent work. Report actual commands, counts, failures/skips and
cleanup results; all deployment-specific gates stay pending.
