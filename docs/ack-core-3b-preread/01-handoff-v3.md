# ack-core 3b — Zabalack input for the joint design (13 October 2026)

Prepared by the Zabalack Claude session, 12 October 2026, for the joint session with Voalack (Astra reviews on
the Voalack side; Codex reviews on the Zabalack side). Status: INPUT for discussion, not a design and not an
agreement. v3 (12 October, late evening) replaces the superseded §2 text with the positions of §5
(Codex review 20261013T005221Z-codex-omarchy-1e79ef92, R1) and lists Codex's remaining contracts in §6.
Sources: main 92aa6ba4 (`src/zabalack/*.py`, `sql/`), `docs/DESIGN_0.2.0_ACTION_SYNC.md` r10,
`docs/DESIGN_0.2.0_F1_SOURCE_BINDING.md` r4.2, and `handoffs/SHARED-LAB-AND-COEXISTENCE-DIRECTION-20261011.md`
(3b prerequisites 1-7).

## 1. What Zabalack installs in Alerta today (inventory for the shared install)

| Kind | Item | Where |
|---|---|---|
| WSGI factory | `zabalack.plugins:create_app` wraps `alerta.app.create_app`, then `configure_app` | `plugins.py:117`, `:237` |
| Response callbacks | `@app.after_request` (error-body redaction); `@app.before_request` (reader query-guard refusal) | `plugins.py:140`, `:148` |
| Logging | `CaptureLogRedactor` installed on the app and ancestor log handlers | `plugins.py:132-138` |
| Global patches | `Alert.serialize` and `RichHistory.serialize` filters that hide Zabalack's reserved attributes | `plugins.py:60-116` |
| Global patches | reader query guard on `qb.alerts`, plus a follower that re-applies it when `QueryBuilder.init_app` rebinds the class | `query_guard.py`, `plugins.py:180-235` |
| Routing observer | records the exact receiver iterable that the status-change dispatcher uses | `plugins.py:244-296` |
| Plugin | `ZabbixNormaliser`: `pre_receive` (ingestion authentication), `post_receive` (F1 bind in 0.2.0), `status_change` (pass) | `normalizer.py:50-106` |
| Plugin | `AckSigningPlugin`: `pre_receive`, `post_receive`, `take_action` (stage), `status_change` (sign), `post_action` | `plugins.py:347-560` |
| Database | schema `zabalack`; trigger `zabalack_capture` on `public.alerts`; `stage_generation()` commits on Alerta's request connection | `sql/` |

All of these must become entries in one ack-core install, with one factory and one reviewed-callback registry
(3b prerequisites 1 and 2). Zabalack's attribute filters must filter only Zabalack's reserved keys.

## 2. What Zabalack 0.2.0 needs from 3b

1. **Shared hook paths.** The housekeeping path (`take_action` and `post_action` with `timeout`, the `system`
   actor) and the ingestion path (`status_change` on correlate) are seen by every provider. ack-core must run
   each provider's hook in a fixed, recorded order, and pass-through must hold in every position
   (3b prerequisite 4).
2. **An ingestion epoch ordered under the per-alert lock (closes the F1 residual, owner decision F1-D1).**
   F1 r4.2 ranks bindings by recording order, because nothing records ingestion order inside the ingestion
   transaction. ack-core records one trusted epoch per accepted ingestion, inside that ingestion's transaction,
   under the per-alert (and per-lifetime) lock, with a committed current-epoch pointer. A sequence value gives
   identity only; order comes from the lock and holds within one alert lifetime, not globally. Ordinary status,
   tag, attribute, staging and projection updates never advance it. A delayed bind for epoch E can never
   displace E+1. A missing or failed bind refuses managed actions, and never makes an owned alert foreign. The
   exact event, lock key and pointer contract is open item R2 in §6. The epoch mechanism is separate from
   Voalack's frozen r8 triggers; merging them would be a joint migration change.
3. **`model_unack` admission (F1 §3.5).** This stays diagnostic-only. Naming an epoch inside the correlate
   statement is not enough, because Alerta calls the signing hook before the correlate SQL runs. The
   replacement needs its own reviewed signing-context and delayed-admission protocol (open item R3 in §6).
4. **Transaction model (3b prerequisite 5).** Zabalack accepts the default of one core-controlled request
   transaction with one final commit (§5.2). Independent durability of the staged intent is not required.
   Zabalack's early commit (`plugins.py:298-321`) exists only because Alerta commits `update_tags()` and
   `update_attributes()` separately (`utils/api.py` process_action; `_updateone` commits). Removing only the
   plugin's commit would be wrong. The whole enlisted path needs one reviewed transaction owner, with no
   intermediate commit and a defined unknown-COMMIT recovery (open item R4 in §6).
5. **Single-owner decision (3b prerequisites 6 and 7).** Ownership is a core-owned record written by a
   non-provider role with immutable provenance (§5.3). It is resolved after the permission and customer
   filters and before private enrolment. Zabalack's input is its F1 binding (authenticated `POST /alert`,
   exact epoch). Conflicting managed evidence **refuses** the managed action and stages neither provider. The
   refusal never reveals whether a private binding exists. Pure foreign pass-through stays separate from
   managed refusal.

## 3. Constraints Zabalack keeps

- Every DDL on `public.alerts` comes from the ack-core install, under joint review. The exact rendered trigger
  definitions are exchanged before implementation.
- There is no Zabalack DDL during an open Voalack maintenance permit.
- Schemas, roles, HMAC keys and workers stay separate per provider. The same PgBouncer is used.
- Released 0.1.0b1 behaviour is not changed by 3b work. Adoption of ack-core by Zabalack is new integration
  work, after the beta tag.

## 4. Questions for the Voalack session

1. (Answered by Astra, §5.1: Voalack's earlier classification trigger is obsolete; any merge with the epoch
   stamp would be a joint migration change.)
2. Does Voalack's transaction rule (action hooks local and transactional) accept option (b) of 2.4?
3. Which core table holds the ownership decision, and which role writes it?
4. Is the coexist lab fork (shared lab step 6) the first place where 3b is qualified?

## 5. Astra's answers (relayed by Voalack, 12 October 2026, evening) and Zabalack's provisional positions

Astra's answers are a paraphrase relayed by the Voalack session. Her words govern. They are Voalack's design
position for the session, not accepted DDL or grants. Zabalack's positions below are provisional until Codex
reviews them.

1. **Epoch (2.2).** Astra: a sequence gives identity, not commit order. Use a per-alert row lock for
   ordering. Re-read the epoch, lifetime, customer and source under that lock, and define exactly which
   ingestion paths advance the epoch. A missing binding may refuse managed actions, but it never makes an owned
   alert foreign. Voalack's earlier `BEFORE UPDATE OF event, resource` trigger is obsolete. Its r8 §3 freezes
   other triggers, and merging them with a core stamp would be a joint migration change.
   **Zabalack position: accepted.** The epoch value comes from a counter that is advanced under the per-alert
   row lock that ingestion already holds, so its order is the commit order for that alert. Zabalack's own
   generation fence already relies on a per-alert lock for the same reason (migration 008). The race,
   failed-bind, rollback, ID-reuse, retry and stale-epoch evidence becomes F1 closure evidence.
2. **Transaction model (2.4).** Astra: by default, one core-controlled Alerta request transaction, with
   exactly one owner enlisted. The owner's hook may stage before finalisation. Intent, history and the local
   action become durable together at the single final commit. There are no intermediate commits, and an
   unknown COMMIT outcome is handled. Astra asks Zabalack to settle whether intent must be durable on its own
   before the status commit.
   **Zabalack position: independent durability is not required.** Zabalack commits early today only because
   Alerta's `process_action()` commits `update_tags()` before `update_attributes()`. A pending staging write
   would otherwise ride the wrong commit (`plugins.py:298-312`). In one core-controlled transaction, the
   capture trigger runs inside the same transaction and sees the staged intent and the generation fence row.
   They become durable atomically with the history row. Zabalack therefore accepts the single-final-commit
   default, provided that ack-core removes Alerta's intermediate commits for enlisted actions and defines the
   unknown-COMMIT outcome. Zabalack's worker already treats an unknown write outcome as `uncertain`, never
   resend.
3. **Single owner (2.5).** Astra: a dedicated core schema and a non-provider owner role, for example an
   `ack_core.alert_ownership` row with immutable transition provenance. It is resolved after the permission
   and customer filters and before private enrolment. Conflicting managed evidence must **refuse** the managed
   action and stage neither provider. Dual pass-through is not sufficient, because the local ACK could then
   appear successful. The resolver must not reveal whether a private binding exists.
   **Zabalack position: accepted.** This replaces "both pass through with a skip reason" in 2.5.
4. **First qualification (question 4).** Astra: the coexist lab fork is the right first joint target, after
   joint source and design review and a separate admission. The exact image, versions and witnesses come
   first. **Zabalack position: agreed.** Neither project has a grant to build or run it yet.

## 6. Open contracts for the joint session (Codex review R2-R5, 13 October 2026 UTC)

- **R2, epoch event and current pointer.** List every accepted create, dedupe and correlate path, including
  equal timestamps, reused receive IDs and undeclared ingestion. Stamp exactly once per trusted ingestion.
  Name the lock key, including lifetime and ID reuse, the acquisition point, the counter allocation, the
  committed pointer and the binding write contract. Under the action lock, re-read the epoch, lifetime,
  customer, source evidence and owner decision. Gaps from rollback or retry are harmless.
- **R3, `model_unack`.** Either a trusted single-use ingestion context visible at signing and committed
  atomically, or a restricted pending-capture admission protocol. Never retrofit an epoch onto an old
  signature. Never promote diagnostic data into intent. Until then, F1 r4.2 §3.5 behaviour stays.
- **R4, transaction owner and unknown COMMIT.** Map every call and commit through `take_action`,
  `process_action`, `process_status`, status SQL with capture, and `post_action`. Define rollback ownership,
  lock order, response timing and recovery by stable action ID, with no blind replay and no external effect
  before committed admission. Keep it separate from the existing no-resend rule and from the migration 013
  suspension gap.
- **R5, shared-install and resolver trust.** Preserve logging and response privacy, reserved-key filtering,
  query-builder rebind protection, reader refusal and readiness, routing observation, idempotence and
  per-app isolation. Install every global patch once. Name the core routines and tables, their writers and the
  locked resolver before any grants. Mutable `source_system` or event/resource classification is not
  authenticated ownership.

Evidence for all of these is NOT_RUN. It needs native witnesses, exact rendered triggers, image and version
bindings, and a separate admission first.
