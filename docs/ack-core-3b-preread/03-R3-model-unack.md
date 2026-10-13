# R3 — `model_unack` signing context and delayed admission (pre-read draft)

Author: Opus (Zabalack session). Status: **PROPOSED DRAFT r8, discussion input only.** r8: answers Astra 23b86285; `07-r8-frozen-decisions.md` governs where it differs; r8 review pending. Codex review: r1
CHANGES_REQUIRED (750d397d: P2, P3, closure); r2 CHANGES_REQUIRED (20261013T170941Z-codex-omarchy-d7391d2a:
Q1, Q2, Q4); r3 CHANGES_REQUIRED (20261013T174021Z-codex-omarchy-d7d59f1e: S3, S4 answered in r4); r4 review
(20261013T180356Z-codex-omarchy-604951f7): those items closed as draft corrections; no open item in this file. R3 was raised in 20261013T005221Z-codex-omarchy-1e79ef92. Until a replacement is reviewed and separately
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

## 2. Proposed protocol (follows R2 §4 steps R, C, K, S, T, F, X exactly)

The ingestion sequence and its writers are defined once, in R2 §4. For `model_unack` this means:

1. **R, C, K.** The core reserves E. Providers that authenticate the ingestion claim it during
   `pre_receive`. At K the core decides, including the existing class and every conflict predicate (R2 §4).
   Only a provisional owner (single, non-conflicting claim) may sign. With zero claims or a conflict
   decision nobody may sign, and any `model_unack` attempt becomes a committed, non-executable refusal
   record (`owner_unresolved` or `ownership_conflict`). If `pre_receive` changes the natural
   identity so that the target would differ, finalize aborts.
2. **S.** In `status_change` (`models/alert.py:359`), the provisional owner signs the intent over the
   canonical bytes (§3), which include E, the lifetime, the nonce and the owner-decision digest.
3. **T.** The correlate statement (`models/alert.py:379`) fires the stamp trigger and the provider's capture
   trigger in **either order**. The capture writes a non-executable **candidate** that references the
   context nonce and the signed intent. It does not read the stamp, so trigger order cannot change the
   outcome.
4. **F, X.** Finalize checks counter = 1, target, E and lifetime, writes the binding for E from the pending
   bind, records the class transition, and persists the context mapping (nonce → alert, lifetime, E,
   owner decision) as committed evidence. Then one commit. Any mismatch rolls back everything, candidate
   included.
5. **Worker admission (after commit).** Only the provider's worker admits, and only committed evidence, read
   through the **worker evidence interface** (R5 §2c) under the provider worker's own role:
   (a) nonce → E mapping and the owner decision = this provider; (b) the committed binding for exactly E,
   whose source digest equals the captured intent's source; (c) history provenance (§1); (d) the mirror flag
   for the bound cohort revision; (e) **HMAC verification by the worker before any endpoint or credential
   resolution** (DESIGN §5.2/§6.2), with keys only in the provider; (f) the execution window, as an
   independent mandatory gate. The separate verification and delivery leases of the existing worker stay.

The context is consumed once, on success, exception, suppression, rerouting, re-entrant call or teardown.
No request-local slot can authorize a later ingestion, and an old signature is never relabelled with a newly
allocated epoch.

### 2b. Admission policy across later epochs (r8: see 07 D2, which governs)

- **Validation split (S3)** stays: capture validates the reserved context, nonce, E, lifetime and signed
  identity in either trigger order; F validates stamp, count, target, lifetime and E. Any F mismatch aborts.
  Zero claims, conflict, mirror disabled, missing provenance and malformed data become committed,
  non-executable refusal records.
- **After capture**, the worker uses the 07 D2 table: historical evidence (binding and class at E, intent,
  windows) plus current mandatory predicates, read at admission and again before target access. Ordinary
  supersession with the same source by the same authenticated provider is admitted. A changed source, an
  unbound E+1, a conflict or other provider, a retired lifetime, a customer change, a retired registration
  or role, a revoked policy or mirror flag, and an expired capture or execution window are each refused,
  with their own reason codes.

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
