# Acceptance evidence by catalogue ID

Every deployment result remains **MANUAL_GATE_PENDING**. PASS below describes only executed checks in the named layer, not full catalogue or deployment acceptance. WASM means PostgreSQL 18.3 in PGlite, a single backend; native concurrency, authentication, TLS, PgBouncer and HA are not established by it.

| ID | Unit/mock | PostgreSQL WASM | Native PostgreSQL | Real Zabbix |
|---|---|---|---|---|
| T1 | PASS | PASS | PASS | PASS |
| T2 | NOT_RUN | NOT_RUN | BLOCKED_ENVIRONMENT | NOT_RUN |
| T3 | PASS | NOT_RUN | NOT_RUN | NOT_RUN |
| T4 | PASS | NOT_RUN | BLOCKED_ENVIRONMENT | NOT_RUN |
| T5 | PASS | PASS | PASS | NOT_RUN |
| T6 | NOT_RUN | PASS | PASS | NOT_RUN |
| T7 | NOT_RUN | PASS | BLOCKED_ENVIRONMENT | NOT_RUN |
| T8 | PASS | PASS | PASS | NOT_RUN |
| T9 | PASS | PASS | PASS | NOT_RUN |
| T10 | PASS | PASS | BLOCKED_ENVIRONMENT | NOT_RUN |
| T11a | PASS | NOT_RUN | NOT_RUN | PASS |
| T11b | PASS | NOT_RUN | NOT_RUN | NOT_RUN |
| T12 | PASS | NOT_RUN | NOT_RUN | NOT_RUN |
| T13 | PASS | NOT_RUN | NOT_RUN | PASS |
| T14 | PASS | PASS | PASS | NOT_RUN |
| T15 | PASS | NOT_RUN | BLOCKED_ENVIRONMENT | NOT_RUN |
| T16 | PASS | PASS | PASS | NOT_RUN |
| T17 | PASS | NOT_RUN | NOT_RUN | NOT_RUN |
| T18 | PASS | NOT_RUN | NOT_RUN | NOT_RUN |
| T19 | PASS | PASS | PASS | NOT_RUN |
| T20 | PASS | NOT_RUN | BLOCKED_ENVIRONMENT | NOT_RUN |
| T21 | PASS | PASS | PASS | NOT_RUN |
| T22 | NOT_RUN | PASS | PASS | NOT_RUN |
| T23 | PASS | NOT_RUN | NOT_RUN | NOT_RUN |
| T24 | PASS | NOT_RUN | NOT_RUN | NOT_RUN |
| T25 | PASS | NOT_RUN | NOT_RUN | PASS |
| T26 | PASS | NOT_RUN | NOT_RUN | NOT_RUN |
| T27 | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| T28 | PASS | NOT_RUN | NOT_RUN | NOT_RUN |
| T29 | PASS | PASS | PASS | NOT_RUN |
| T30 | NOT_RUN | PASS | PASS | NOT_RUN |
| T31 | PASS | PASS | PASS | NOT_RUN |
| T32 | PASS | PASS | BLOCKED_ENVIRONMENT | NOT_RUN |
| T33 | PASS | PASS | NOT_RUN | PASS |
| T34 | PASS | NOT_RUN | PASS | NOT_RUN |
| T35 | NOT_RUN | PASS | PASS | NOT_RUN |
| T36 | PASS | PASS | PASS | NOT_RUN |
| T37 | PASS | NOT_RUN | PASS | NOT_RUN |
| T38 | NOT_RUN | PASS | PASS | PASS |
| T39 | NOT_RUN | PASS | PASS | NOT_RUN |

Exact executed/not-run test references, report paths and requirement wording are in `docs/acceptance_matrix.csv`. T2, T20, T27, T30, T31 and T34 require the explicit environment procedures in `docs/deployment_acceptance.md`. T11b deliberately preserves the approved trusted-operator scope without claiming historical ingestion lineage.
