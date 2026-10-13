# Proposed joint witness / admission matrix for ack-core 3b (pre-read draft)

Author: Opus (Zabalack session). Status: **PROPOSED r2, NOT_RUN** (r1 reviewed by Codex in
20261013T163920Z-codex-omarchy-750d397d; rows added for its negative cases). No lab creation or execution is requested.
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
| W5 | Ownership | owned-unbound reached **naturally**: a Zabalack-classed alert, then an undeclared, suppressed or routed-away ingestion | class unchanged; current epoch unbound; managed action refused; alert stays owned (not foreign) |
| W6 | Ingestion | create, dedupe and correlate each advance the epoch exactly once per affected row | epoch +1 per affected row; status, tag, attribute, note, staging and delete statements never advance it |
| W7 | Ingestion | (a) **undeclared** replay of an exact receive ID with a forced equal timestamp; (b) **authenticated** new ingestion reusing a receive ID | (a) new unbound epoch, actions refused; (b) new epoch bound by the authenticated provider |
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
| W18 | model_unack | stale **at signing or capture** / mirror false / malformed / suppressed / routed-away / missing pending binding | refused with bounded reason; zero Zabbix calls |
| W19 | Privacy | serialized alert, history and error bodies for each provider's alerts | the union of reserved keys is hidden; no foreign key is exposed |
| W20 | Privacy | reader query guard after `QueryBuilder.init_app` rebind | guard still applied; refusal on bypass |
| W21 | Shared install | factory called twice; two apps in one process | each component installed exactly once per app |
| W22 | Shared install | unknown provider identity or unreviewed callback | process refuses to start |
| W23 | Protection | Voalack r8 delete/restore and no-TRUNCATE protections with the core trigger present | protections hold; core records are preserved across restore |
| W24 | Protection | ack-core DDL attempted during an open Voalack permit | refused by procedure |
| W25 | Single-order | each provider alone (other not installed) | unchanged provider-alone invariants hold (ownership of its own alerts, privacy filtering, refusal reasons, no-resend). Deliberately changed behaviour is listed and tested separately: single final commit instead of the staging commit, core epoch binding instead of F1 `bind_seq`, conflict refusal. Identity with old evidence is **not** expected |
| W26 | Ingestion | dedupe/correlate statement affecting zero or two rows | enlisted ingestion aborted (one-target invariant) |
| W27 | Ingestion | two ingestions of the same alert inside one enlisted transaction; re-entrant hook | second stamp aborts the transaction |
| W28 | Ingestion | `pre_receive` mutates the natural identity so the target row changes | aborted; nothing stamped or bound |
| W29 | Ownership | enrolment bootstrap: first authenticated ingestion of a new lifetime | class established in the same commit as the bind |
| W30 | Ownership | delete, restore and re-create with the same ID | tombstone retained; new lifetime; old class and binds unusable |
| W31 | model_unack | E captured, then E+1 before worker admission | behaviour per the chosen R3 §2b policy (both variants witnessed) |
| W32 | Transaction | unknown COMMIT while the original server transaction is still running | `pg_xact_status` = in progress; state stays `commit_unknown`; no replay |
| W33 | Transaction | unknown COMMIT on a committed **no-capture** branch | the request record shows `committed_no_capture`; absence of capture is not read as rollback |
| W34 | Transaction | retry with the same request_id and a **changed** payload | 409; nothing applied |
| W35 | Transaction | reconciliation attempted against a replica | refused; primary only |
| W36 | Identity | other roles call core routines; calls without the core context or with a mismatched provider or generation | denied or refused; forged in-process context recorded as a stated limit (R5 §2b) |
| W37 | Privacy | pre-commit log lines, error bodies, JSONP responses and query-oracle probes for membership | no reserved key and no membership signal leaks |
| W38 | Ordering | both actual trigger orders on `public.alerts` (core stamp versus provider AFTER triggers) and both lock acquisition orders | behaviour identical; no deadlock under the documented lock order |
| W39 | Transaction | `ForwardingLoop` 202, `RejectException` 400 and `InvalidAction` 409 raised after staging | each passes through the core decision and rolls back |
| W40 | Transaction | housekeeping: preliminary deletes, then one generic per-alert failure | deletes outside the enlisted scope; that alert rolled back; request outcome per R4 open question 3 |

Evidence rule: each run records the base image manifest hash, installed wheel hashes for both providers
and ack-core, the rendered trigger and routine definitions (`pg_get_triggerdef`, `pg_get_functiondef`),
and the ACL inventory. Nothing is rebound across runs.
