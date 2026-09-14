# Alpha and stable-release work

Current candidate status against the beta boundary: `docs/BETA_READINESS.md`.

| Stage | Remaining evidence or decision |
|---|---|
| Public hosting | Assign namespace/maintainers/private report channels, review rights and public content, authorize upload |
| Native integration | Execute PostgreSQL 18, actual role logins, concurrent claims, capture cancellation and PgBouncer pool tests; retain outcomes |
| Real Zabbix | Execute exact-event, complete-host, restricted-tenant and acknowledgement/reference fixtures in an isolated server; test macro/update notifications |
| Stable support | Independent external security review, native crash/delayed-write/HA/fleet acceptance, compatibility upgrade policy and staffed security support |
| Every deployment | Complete G1–G8 using actual inventory, credentials by protected reference, cohort approval, permission tests, performance, responders and records policy |

## Narrow upstream collaboration proposals

The Alerta application adapter and effective-routing observer are maintenance
dependencies. Before widening supported versions, inspect each upstream change
and preserve the regression tests. Candidate upstream proposals are a supported
way to observe the effective routed callback sequence without rerunning dynamic
routing, and a supported final pre-persistence/error-redaction extension point.
Document the exact logging/error-detail problem in amendment A1 and supply a
minimal synthetic reproducer. No upstream issue or message has been sent.

Feature expansion to bulk, chatops, fan-in alerts, automatic re-acknowledgement
or new signature formats requires a separate reviewed design and versioned
compatibility plan. It is outside this alpha.
