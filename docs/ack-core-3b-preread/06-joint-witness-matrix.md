# Proposed joint witness / admission matrix for ack-core 3b (pre-read draft)

Author: Opus (Zabalack session). Status: **PROPOSED, NOT_RUN.** No lab creation or execution is requested.
A coexistence fork needs separately frozen image, source, installed-package, tool, command and expected-ID
pins plus its own stage and lane approval. Provider-only, S3a or F1 evidence is never relabelled as 3b/3e
evidence.

Each row runs with **both plugin orders** (Zabalack first; Voalack first) unless marked single-order.

| # | Area | Case | Expected |
|---|---|---|---|
| W1 | Ownership | Zabbix alert, authenticated `POST /alert`, Zabalack binds epoch | owner = Zabalack; Voalack hooks return unchanged and write nothing |
| W2 | Ownership | Veeam ONE alert with Voalack class record | owner = Voalack; Zabalack hooks return unchanged and write nothing |
| W3 | Ownership | unknown-source alert | owner = none; both pass through; local action succeeds; no provider write |
| W4 | Ownership | conflicting evidence (Veeam ONE class + `source_system = zabbix` + Zabalack binding) | owner = conflict; managed action refused; neither stages; response does not reveal membership |
| W5 | Ownership | owned-unbound (Zabbix alert whose current epoch is unbound) | managed action refused; alert stays owned (not foreign) |
| W6 | Ingestion | create, dedupe and correlate each advance the epoch exactly once | epoch +1 per statement; status, tag, attribute, note and staging updates never advance it |
| W7 | Ingestion | replay of an exact receive ID with a forced equal timestamp | new epoch; unbound; actions refused |
| W8 | Ingestion | two concurrent ingestions of one alert | epochs ordered by commit; the second waits on the row lock |
| W9 | Ingestion | ingestion rollback after the stamp | no epoch visible; no gap visible |
| W10 | Ingestion | delete, then re-create with the same `alert_id` | new lifetime; old bindings unusable |
| W11 | Ingestion | failed provider bind inside the ingestion transaction | whole ingestion rolled back |
| W12 | Transaction | action happy path | one commit: staging, tags, attributes, status, history, capture and outbox all visible together |
| W13 | Transaction | failure injected at each boundary (after staging, after tags, after attributes, inside `set_alert`, in post_action) | full rollback each time; nothing staged survives |
| W14 | Transaction | commit raises ambiguously (connection killed during COMMIT) | 503 with action_id; reconciliation by action_id finds all or nothing; no external effect before committed admission |
| W15 | Transaction | a non-owner hook attempts a write or commit while enlisted | commit guard raises; request rolled back |
| W16 | Transaction | housekeeping loop with one failing alert | one transaction per alert; other alerts unaffected |
| W17 | model_unack | severity-rise correlate from ack with the reserved context (R3) | signature over the reserved epoch; admitted only with binding + owner + provenance + mirror |
| W18 | model_unack | stale / poisoned / mirror false / malformed / suppressed / routed-away | refused with bounded reason; zero Zabbix calls |
| W19 | Privacy | serialized alert, history and error bodies for each provider's alerts | the union of reserved keys is hidden; no foreign key is exposed |
| W20 | Privacy | reader query guard after `QueryBuilder.init_app` rebind | guard still applied; refusal on bypass |
| W21 | Shared install | factory called twice; two apps in one process | each component installed exactly once per app |
| W22 | Shared install | unknown provider identity or unreviewed callback | process refuses to start |
| W23 | Protection | Voalack r8 delete/restore and no-TRUNCATE protections with the core trigger present | protections hold; core records are preserved across restore |
| W24 | Protection | ack-core DDL attempted during an open Voalack permit | refused by procedure |
| W25 | Single-order | each provider alone (other not installed) | identical behaviour to its released alone-mode evidence |

Evidence rule: each run records the base image manifest hash, installed wheel hashes for both providers
and ack-core, the rendered trigger and routine definitions (`pg_get_triggerdef`, `pg_get_functiondef`),
and the ACL inventory. Nothing is rebound across runs.
