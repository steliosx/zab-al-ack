# r10 clarifications for the joint session (answers Astra 14ae9733)

Author: Opus (Zabalack session), 13 October 2026. Status: **PROPOSED, discussion input only**; every case is
NOT_RUN. Nothing here is joint contract, DDL, lab, implementation or qualification authority. This file
clarifies 04, 06 and 07, and governs where it is more precise.

## C1. W45d and the declared decision points

- The status probe's row filter includes `expires_at > clock_timestamp()`. After expiry, a re-probe
  therefore finds **no matching row**. It does not conflict. W45d means: earlier probes conflicted, then a
  re-probe whose filter sample is at or after `expires_at` finds no row, so the answer is `not_found`.
- **W45h (new, separate case):** a probe whose filter sample is **before** expiry selects the row and is
  then paused before the lock attempt; the lock attempt then conflicts. The declared decision point for that
  attempt is its filter sample. If it is the last attempt, the answer is `in_progress` at that sample. The
  qualification run records which branch was taken and which sample decided it.
- Before qualification, the exact rendered SQL, clock barriers and recorded branch and sample are frozen.
  A lock error is never counted as evidence of expiry.

## C2. W46 restore meaning

W46's "restore" case means a **reserve-bypassing** resurrection or reuse of a historical Veeam ONE ID, which
must be refused. A legitimate restore of a consistent, valid backup of the **same lifetime**, under
Voalack's existing atomic recovery protections, is not refused. "Provider first, core after" (07 D1.4) is a
proposed dependency order for a consistent backup. It does not authorize an unguarded stock restore, and it
is not a proved crash-safe sequence.

## C3. P3 adverse transitions that are later reversed (proposed policy)

Notation: E is the captured epoch; E+n is the current epoch at the check.
- Each check (admission, and the pre-target recheck after any wait) compares the captured E facts with the
  **current E+n** facts:
  - binding source digest at E+n = source digest at E (same-source supersession);
  - class at E+n = this provider, not `conflict`;
  - lifetime unchanged;
  - customer at E+n = customer at E;
  - registration generation of the capture still active;
  - policy and mirror flag currently active;
  - windows not expired.
- **Sticky refusal.** The first check that observes any adverse predicate settles the operation as
  **refused (terminal)**, with its reason code. A later reversal (for example, the source returns to the
  captured digest at E+n+1) never re-admits it. This keeps the existing no-resend and terminal-outcome
  rules, and avoids flapping.
- An adverse window that **no check observed** cannot be known. Only the current predicates at the actual
  checks decide.
- Historical-only evidence access (R5 §2c) cannot evaluate current predicates. The provider-scoped **fresh
  current interface**, with compatible fencing and read sources, remains a prerequisite (07 "Still open"
  item 5).

## C4. W48 compatible inputs

W48's "one alert" expectation applies to **compatible** inputs, which deduplicate or correlate onto each
other under Alerta's rules. The advisory key (customer, environment, resource) serializes a broader set:
incompatible events for the same resource serialize safely, yet still create separate alerts. That is
correct behaviour, not a failure.

## C5. W52 reservation outcomes, split

- **W52a:** the reservation commits, then the response is totally lost; a concurrent duplicate submit with
  the same principal, key and full scope runs. Both converge on the one committed reservation and one locked
  attempt.
- **W52b:** the reservation rolls back, then the response is totally lost; a concurrent duplicate submit
  runs. Exactly one new reservation is created; no attempt starts from the rolled-back one.
- **W52c:** lock timeout, deadlock or serialization failure during submit: abort and retry fresh, or
  return `busy`.
- These are separate from the final-work COMMIT ambiguity (W14) and the error-retry cases. No error, and no
  reservation alone, ever grants dispatch.

## C6. Deterministic worker-role mapping (P5)

- At any instant, at most one **active** `provider_roles` row exists per login role: an exclusion
  constraint over (role, validity period) forbids overlapping validity.
- `read_committed_evidence` resolves exactly one active row for `session_user` at call time. Zero rows, or
  more than one row because of a corrupted or ambiguous mapping, returns the uniform `not_yours_or_absent`
  and raises an incident. It never selects a convenient first row.
- **W53 (new):** overlapping and ambiguous mappings injected → uniform refusal and an incident; no evidence
  is returned. This sits beside W51.

## C7. Retained prerequisites (not covered by this pre-read)

- the fresh current-worker interface;
- synchronized revocation, registry retirement and pre-target reads. A process-local immutable registry
  alone cannot prove that another process sees a retirement: retirement is recorded in a database table,
  read at enlistment, at finalize and at worker admission;
- effective ordered adapters with their relevant bound state, not code hashes alone;
- exact rendered object, sequence and role dependencies, and the migration, reseal and restore admission and
  crash boundaries.

## For the session

Settle:
- the adverse-transition policy (C3 proposes sticky refusal);
- the publication of `alerta-ack-core`;
- the key-reuse namespace;
- the housekeeping choice;
- the recovery and read-interface choices.

Provider-private authentication, send and no-resend boundaries stay as they are.
