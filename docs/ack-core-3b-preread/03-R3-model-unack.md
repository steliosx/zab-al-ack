# R3 — `model_unack` signing context and delayed admission (pre-read draft)

Author: Opus (Zabalack session). Status: **PROPOSED DRAFT, discussion input only.** Codex review: pending
(R3 raised in 20261013T005221Z-codex-omarchy-1e79ef92). Until a replacement is reviewed and separately
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

Requires R4's core-owned ingestion transaction.

1. **Reservation before hooks.** At the start of the enlisted ingestion (before `pre_receive`), ack-core
   resolves the target row by Alerta's correlate/dedupe identity. It takes `FOR UPDATE` on the
   `public.alerts` row and the `ack_core.alert_epoch` row, and **reserves** epoch `E = current + 1` in a
   transaction-local context (`ack_core.ingestion_context`: alert_id, lifetime, reserved epoch, owner
   decision, registration generation, nonce). Nothing is visible outside the transaction.
2. **Signing.** The owner's `status_change` hook signs the `model_unack` intent over the canonical bytes
   below, including the reserved context.
3. **Stamp check.** The core epoch trigger (R2) fires in the correlate statement and requires its stamp to
   equal the reserved `E` and the same lifetime. A mismatch aborts the transaction.
4. **Bind.** The owner binds epoch `E` in the same transaction (R2 §4).
5. **Capture.** The capture function records the `model_unack` capture bound to `E`. Admission requires,
   at capture time and again in the worker: (a) the owner's committed binding for exactly `E`; (b) the
   ownership decision for `E` = this provider; (c) history provenance (§1); (d) the mirror flag; (e) a
   valid signature over the canonical bytes. Otherwise it is recorded as refused, with a bounded reason.
6. **Commit.** One final commit. Delayed admission: the worker admits only after the commit is visible and
   only if `E` is still the alert's current epoch **or** the intent's execution window is still open (open
   question 3).

A late binding cannot admit a stale `E` after `E+1`: binds are only accepted for the current epoch inside
its own ingestion transaction (R2 §4), so no binding for `E` can be created after `E+1` exists.

## 3. Canonical signing bytes (proposed)

- Domain string: `zabalack/model_unack/v1`. It is distinct from release one's `ack` domain, so a signature
  cannot be replayed across kinds.
- Bytes: `domain || 0x00 || canonical_json(payload)`. `canonical_json` is UTF-8 with keys sorted, no
  insignificant whitespace, integers as JSON numbers, timestamps as RFC 3339 UTC strings with microseconds,
  and no floats.
- Payload keys (exact set): `version`, `kind`, `action_id`, `alert_id`, `lifetime`, `epoch`, `customer`,
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
   Alerta's lookup logic. A mismatch between the core's and Alerta's chosen row must abort the transaction.
2. Whether suppressed alerts (`process_alert` skips both hook loops) and routed-away hooks still produce a
   reserved context. Proposal: reservation always happens, so a skipped hook leaves `E` unbound and
   refuses later actions.
3. Delayed admission when `E+1` arrives before the worker runs: admit an intent bound to the superseded
   `E` within its window (the transition happened), or refuse? Proposal: admit only if `E` is still
   current at capture; the worker re-checks the window but not currency.
4. A `model_unack` replay of an exact receive ID produces a new event and epoch (R2 §1). It is unbound
   unless authenticated, so it is refused.
