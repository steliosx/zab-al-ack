# R3 — `model_unack` signing context and delayed admission (pre-read draft)

Author: Opus (Zabalack session). Status: **PROPOSED DRAFT r2, discussion input only.** Codex review: r1
CHANGES_REQUIRED (20261013T163920Z-codex-omarchy-750d397d; P2, P3 and the R3 closure details answered
here); r2 pending. R3 was raised in 20261013T005221Z-codex-omarchy-1e79ef92. Until a replacement is reviewed and separately
qualified, F1 r4.2 §3.5 stays: `model_unack` is diagnostic-only (`skipped:
source_binding_unavailable_at_ingestion`), with no intent, signature, marker or generation.

## 1. Scope: exact action kind (existing design, action-sync r10)

| Item | Value |
|---|---|
| Kind | `model_unack`: alarm-model ACK-4, a severity rise on the **correlate** path from `ack` to `open` |
| Actor | `model`; `invoked_by` = the authenticated ingestion principal (`g.login`), a mapped ingestion key with `user_id` None |
| Permission | ingestion-key mapping for the alert's customer and instance (Zabalack normaliser checks); no human scope |
| History | exactly one new history row, `type = 'severity'`, `status = 'open'`, `update_time = NEW.create_time`; the preceding history row has `status = 'ack'` (action-sync §3/§4.3) |
| Policy | `mirror_model_transitions` for the bound cohort revision (owner Q5: default true, opt-out) |
| Zabbix effect if admitted | unacknowledge (16) only |

Timing fact: the signing hook (`status_change`) runs at `models/alert.py:359`, **before** the correlate SQL
at `:379`. Codex R3: "a token first allocated inside that later SQL statement is not available to the
earlier signing hook."

## 2. Proposed protocol: core-reserved ingestion context

Requires R4's core-owned ingestion transaction and R2's epoch store. The ordering is chosen so that the
binding exists **before** the correlate statement and its AFTER triggers run.

1. **Resolve and reserve (before any hook).** At the start of the enlisted POST /alert, ack-core resolves
   the target row with Alerta's own dedupe/correlate identity, takes `FOR UPDATE` on that `public.alerts`
   row and its `ack_core.alert_epoch` row, and **reserves** `E = current + 1` in a single-use,
   transaction-local context (`ack_core.ingestion_context`: alert_id, lifetime, E, nonce, owner decision,
   registration generation). If no row is found, the ingestion is a create, and `E = 1` of a new lifetime.
2. **Authenticate and pre-bind (in `pre_receive`).** The authenticated provider writes a **pending binding**
   for exactly (alert_id, lifetime, E) through the core bind routine. It is pending because it is not yet
   committed; it is visible inside this transaction only. If `pre_receive` changes the alert's natural
   identity so that the target row would differ, the transaction aborts (fail closed).
3. **Sign (in `status_change`, `models/alert.py:359`).** The owner signs the `model_unack` intent over the
   canonical bytes (§3), which include E, the lifetime, the nonce and the owner decision digest.
4. **Statement and triggers (`models/alert.py:379`).** The correlate `UPDATE` runs. The core stamp trigger
   requires exactly one affected row, that row = the reserved alert, and stamp = E. Any mismatch aborts.
   The provider's capture trigger then finds the pending binding for E in the same transaction and records
   a **candidate capture**. A candidate is non-executable: it carries no admission and holds no outbox
   visibility outside the transaction.
5. **Commit.** One final commit makes the stamp, binding, class change (if any) and candidate capture
   visible together. A failed bind or any mismatch rolls back the whole ingestion, so there is never a
   committed epoch with a silently failed bind.
6. **Worker admission (after commit).** Only the provider's worker admits, and only committed evidence:
   (a) the committed binding for exactly E in the historical store (R2 `epoch_bindings`); (b) class = this
   provider; (c) history provenance (§1); (d) the mirror flag for the bound cohort revision; (e) **HMAC
   verification by the worker, before any target access** (DESIGN §5.2/§6.2). Provider keys stay in the
   provider; the database and the core never hold them; (f) the execution window, as an independent
   mandatory gate.

The context is consumed once, on success, exception, suppression, rerouting, re-entrant call or teardown.
No request-local slot can authorize a later ingestion, and an old signature is never relabelled with a newly
allocated epoch.

### 2b. Admission policy across later epochs (P3; proposed, for joint decision)

- **Stale at signing or capture → refused.** The capture records a refusal if E is not the reserved and
  stamped epoch. That is W18's "stale" case.
- **Superseded after capture → still admissible.** If E+1 arrives after a committed capture for E, the
  `ack` → `open` transition has already happened in Alerta. The operation is frozen-source: it executes
  against its own committed binding for E (kept in the historical store), within its own execution window,
  and in per-event order behind earlier operations, under the existing no-replay and uncertainty fences.
  The worker does **not** require E to be current. It does require the window, the committed E binding and
  per-event ordering.
- This choice belongs to the joint design review. If the reviewers choose "current at worker admission"
  instead, the worker must take the epoch-row lock and re-check currency before target access.
- Witness: E, then E+1 before worker admission, for both policies.

## 3. Canonical signing bytes (proposed)

- Domain string: `zabalack/model_unack/v1`. It is distinct from release one's `ack` domain, so a signature
  cannot be replayed across kinds.
- Bytes: `domain || 0x00 || canonical_json(payload)`. `canonical_json` is UTF-8 with keys sorted, no
  insignificant whitespace, integers as JSON numbers, timestamps as RFC 3339 UTC strings with microseconds,
  and no floats.
- Payload keys (exact set): `version`, `kind`, `action_id`, `alert_id`, `lifetime`, `epoch`, `nonce`,
  `owner_decision_digest`, `key_id`, `customer`,
  `source` {instance, event_id, trigger_id}, `actor` {type: model, invoked_by}, `history` {from: ack,
  to: open, change_type: severity, update_time}, `cohort_revision`, `policy_revision`,
  `registration_generation`, `issued_at`, `capture_expires_at`, `execution_expires_at`.
- MAC: HMAC-SHA-256 with the provider's private signing key and key ID (existing Zabalack key handling).
  Keys, target endpoints and send authority stay private to the provider. Public routing data is not
  permission.

The public, non-secret context (alert_id, lifetime, epoch, owner decision, registration generation) lives
in core tables. The MAC and any provider-private field live only in provider schemas.

## 4. Refusals (no membership disclosure)

| Case | Public outcome | Provider-private record |
|---|---|---|
| Owned, unbound epoch | managed action refused | `source_binding_missing` |
| Ownership conflict | managed action refused for every provider; neither stages | `ownership_conflict` |
| Malformed or unverifiable staged data | refused | bounded reason code; no raw bytes stored (existing capture allowlist rule) |
| Mirror disabled | no write | `mirror_disabled` |
| Provenance missing | no write | `model_provenance_missing` |

Public responses never say whether a private binding or a foreign provider exists.

## 5. Explicit unknowns

1. Whether ack-core can resolve the correlate target row **before** `pre_receive` without duplicating
   Alerta's lookup logic. A mismatch between the core's and Alerta's chosen row aborts the transaction
   (§2 step 4).
2. Whether suppressed alerts (`process_alert` skips both hook loops) and routed-away hooks still produce a
   reserved context. Proposal: reservation always happens, so a skipped hook leaves `E` unbound and
   refuses later actions.
3. The §2b policy choice (superseded-after-capture admissible versus current-at-worker).
4. Receive-ID reuse: an **undeclared** replay produces a new, unbound epoch and is refused. An
   **authenticated** new ingestion that reuses a receive ID is a new authenticated event and binds its own
   epoch. No separate anti-replay rule is proposed, because the epoch, not the receive ID, is the identity.
