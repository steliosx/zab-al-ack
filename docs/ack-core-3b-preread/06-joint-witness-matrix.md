# Proposed joint witness / admission matrix for ack-core 3b (pre-read draft)

Author: Opus (Zabalack session). Status: **PROPOSED r4, NOT_RUN** (r1 reviewed in 750d397d, r2 in
d7391d2a, r3 in 20261013T174021Z-codex-omarchy-d7d59f1e; rows added and adjusted for each). Step letters R, C, K, S, T, F
and X refer to the R2 §4 ingestion state machine. No lab creation or execution is requested.
A coexistence fork needs separately frozen image, source, installed-package, tool, command and expected-ID
pins plus its own stage and lane approval. Provider-only, S3a or F1 evidence is never relabelled as 3b/3e
evidence.

Each row runs with **both plugin orders** (Zabalack first; Voalack first) unless marked single-order.

| # | Area | Case | Expected |
|---|---|---|---|
| W1 | Ownership | Zabbix alert, authenticated `POST /alert`, Zabalack binds epoch | owner = Zabalack; Voalack hooks return unchanged and write nothing |
| W2 | Ownership | Veeam ONE alert with Voalack class record | owner = Voalack; Zabalack hooks return unchanged and write nothing |
| W3 | Ownership | unknown-source alert | owner = none; both pass through; local action succeeds; no provider write |
| W4 | Ownership | conflicting evidence: two claims; a sole claim against an existing other-provider class; an existing `conflict` class; a disagreeing Voalack r8 record | decision `conflict` at K (re-checked at F); no binding, no provider-naming owner decision, no admissible candidate; managed action refused; conflict sticky until lifetime change; response does not reveal membership |
| W5 | Ownership | owned-unbound reached **naturally**: a Zabalack-classed alert, then an undeclared, suppressed or routed-away ingestion | class unchanged; current epoch unbound; managed action refused; alert stays owned (not foreign) |
| W6 | Ingestion | create, dedupe and correlate each advance the epoch exactly once per affected row | epoch +1 per affected row; status, tag, attribute, note, staging and delete statements never advance it |
| W7 | Ingestion | (a) **undeclared** replay of an exact receive ID with a forced equal timestamp; (b) **authenticated** new ingestion reusing a receive ID | (a) new unbound epoch, actions refused; (b) new epoch bound by the authenticated provider |
| W8 | Ingestion | two concurrent ingestions of one alert | epochs ordered by commit; the second waits on the row lock |
| W9 | Ingestion | ingestion rollback after the stamp | no epoch visible; no gap visible |
| W10 | Ingestion | delete, then re-create with the same `alert_id` | new lifetime; old bindings unusable |
| W11 | Ingestion | failure at each step R, C, K, T and F (including a failed finalize write) | whole ingestion rolled back; no epoch, claim, bind, class change or candidate survives |
| W12 | Transaction | action happy path | one commit: staging, tags, attributes, status, history, capture and outbox all visible together |
| W13 | Transaction | failure injected at each boundary (after staging, after tags, after attributes, inside `set_alert`, in post_action) | full rollback each time; nothing staged survives |
| W14 | Transaction | commit raises ambiguously (connection killed during COMMIT) | 503 with request_id; recovery by R4 §3 step 4 finds the outcome or the unlocked `reserved` row; no external effect before committed admission |
| W15 | Transaction | a non-owner hook attempts a write or commit while enlisted | commit guard raises; request rolled back |
| W16 | Transaction | housekeeping loop: one alert fails with RejectException, another with a generic exception | per-alert transactions; already committed earlier alerts stay committed; the failed alert rolls back; the handling of unprocessed later alerts follows R4 open question 3 (both variants stated) |
| W17 | model_unack | severity-rise correlate from ack with a single claim (steps R-X) | signed over reserved E; candidate committed; worker admits only via the worker evidence interface (binding + owner + provenance + mirror + HMAC + window) |
| W18 | model_unack | injected mismatches: nonce of another context, E ≠ reserved E, wrong lifetime (abort, no target access); zero claims / conflict / mirror false / malformed / missing provenance (committed non-executable refusal record) | aborts leave nothing; refusal records are never admitted; zero Zabbix calls |
| W19 | Privacy | serialized alert, history and error bodies for each provider's alerts | the union of reserved keys is hidden; no foreign key is exposed |
| W20 | Privacy | reader query guard after `QueryBuilder.init_app` rebind | guard still applied; refusal on bypass |
| W21 | Shared install | factory called twice; two apps in one process | each component installed exactly once per app |
| W22 | Shared install | unknown provider identity or unreviewed callback | process refuses to start |
| W23 | Protection | Voalack r8 delete/restore and no-TRUNCATE protections with the core trigger present | protections hold; core records are preserved across restore |
| W24 | Protection | ack-core DDL attempted during an open Voalack permit | refused by procedure |
| W25 | Single-order | each provider alone (other not installed) | unchanged provider-alone invariants hold (ownership of its own alerts, privacy filtering, refusal reasons, no-resend). Deliberately changed behaviour is listed and tested separately: single final commit instead of the staging commit, core epoch binding instead of F1 `bind_seq`, conflict refusal. Identity with old evidence is **not** expected |
| W26 | Ingestion | real dedupe/correlate statements that affect zero or two rows; controls: tag/attribute/status statements affecting zero or several rows | ingestion: finalize aborts (counter not 1); controls: counter untouched, no abort |
| W27 | Ingestion | two ingestions of the same alert in one enlisted transaction; re-entrant hook re-using the context | counter 2 or a consumed context → aborted |
| W28 | Ingestion | `pre_receive` mutates the natural identity so the target row changes | finalize detects a target mismatch → aborted; nothing stamped, bound or captured survives |
| W29 | Ownership | bootstrap: first finalized ingestion of a new lifetime with exactly one claim; with zero claims; with two claims | one: class established with bind in the same commit; zero: unbound, no class; two: class `conflict` committed without a binding |
| W30 | Ownership | delete, restore and re-create with the same ID | tombstone retained; new lifetime; old class and binds unusable |
| W31 | model_unack | E captured, then E+1 before worker admission | behaviour per the chosen R3 §2b policy; both variants witnessed (conditional on the joint decision) |
| W32 | Transaction | unknown COMMIT while the original transaction is still running | the reservation row is locked → `in_progress`; no second attempt; no replay |
| W33 | Transaction | unknown COMMIT on committed `no_capture`, `refused` and `local_only` branches | the recorded outcome is returned; absence of a capture is never read as rollback |
| W34 | Transaction | same key with a changed payload; same key and payload under another customer, principal, lifetime or kind; simultaneous callers with the same key | changed or wrong scope: refusal identical to "no such request"; simultaneous: one runs, others get `in_progress` |
| W35 | Transaction | recovery attempted on a replica; with a stale snapshot; reservation past retention; operator `pg_xact_status` too old or in progress | refused or `unknown`; never a fresh dispatch |
| W36 | Identity | other roles call core routines; app calls without the core context or with a mismatched owner or generation; a **fresh** provider-worker connection with only the declared effective grants (schema USAGE + EXECUTE on read_committed_evidence) reads its own evidence (positive), another provider's and absent evidence (uniform `not_yours_or_absent`), and attempts table access and mutating routines (denied) | as stated; forged in-process app context recorded as a stated limit |
| W37 | Privacy | pre-commit log lines, error bodies, JSONP responses and query-oracle probes for membership | no reserved key and no membership signal leaks |
| W38 | Ordering | both firing orders of the stamp trigger and provider capture triggers; both lock acquisition orders | identical committed eligibility or refusal in both trigger orders (captures validate the context, not the stamp; F validates the stamp); an unexpected trigger set refuses readiness; the documented lock order shows no deadlock |
| W39 | Transaction | `ForwardingLoop` 202, `RejectException` 400 and `InvalidAction` 409 raised after staging | each passes through the core decision and rolls back |
| W40 | Transaction | housekeeping: preliminary deletes, then one generic per-alert failure | deletes outside the enlisted scope; that alert rolled back; request outcome per R4 open question 3 |
| W41 | Ingestion | transport retry of POST /alert with the same key after total response loss | original outcome returned; no second ingestion or epoch |
| W42 | Transaction | original submitter paused after its reservation commit and before taking the lock; a duplicate submit runs and commits; the original resumes | the original acquires the lock, sees the terminal outcome and returns it: exactly one operation and one epoch |
| W43 | Transaction | a waiter queued behind a committing holder on the same key | the waiter gets the updated row under the lock and returns the existing outcome; no second attempt |
| W44 | Transaction | status(key) for an absent key, a wrong-scope key and an expired (tombstoned) key; submit on a tombstoned key | status: identical `not_found`, no work started; submit on a tombstone: refused, never re-executed |

Evidence rule: each run records the base image manifest hash, installed wheel hashes for both providers
and ack-core, the rendered trigger and routine definitions (`pg_get_triggerdef`, `pg_get_functiondef`),
and the ACL inventory. Nothing is rebound across runs.
