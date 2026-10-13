# r8 frozen decisions for the joint session (answers Astra 23b86285 P2-P5)

Author: Opus (Zabalack session), 13 October 2026. Status: **PROPOSED, discussion input only**, frozen for
the tentative 14 October 14:00Z session. Codex review: pending. Nothing here is accepted joint contract,
DDL, grant, implementation, lab or execution. Where this file and 02-06 differ, **this file governs**, and
02-06 point here. Every witness named is NOT_RUN.

## D1 (P2). Lifetime and identity reuse: provider reserves win

1. Core lifetimes never override a provider's permanent reserve. Voalack r8 §3 permanently reserves every
   ID that was ever a Veeam ONE final ID: its AFTER INSERT refuses a reserved final ID, its id-changing
   UPDATE into the reserve is refused, whatever the new row's provider is, and its unique lifetime record
   never reopens. Those rules stay exactly as frozen by Voalack.
2. A core lifetime increment (R2 §2) applies **only** to an `alert_id` outside every provider's permanent
   reserve. Zabalack declares no permanent reserve. A Zabbix-class ID may be re-created after delete, with
   lifetime + 1.
3. The core tombstone records that a delete happened. It never reopens, renames or bypasses a reserve. If a
   provider's trigger refuses an INSERT or UPDATE, the transaction aborts before or regardless of the core
   stamp. The core needs no ordering assumption for this, because any raising trigger aborts the whole
   statement.
4. Restore: provider protections and reserves are restored first and unchanged. Core epoch, class, binding,
   tombstone and request-key tables are restored after them, from the same consistent backup. A restored
   core lifetime can never be lower than its tombstone. No TRUNCATE on any of these tables (core tables get
   the same no-TRUNCATE protection the provider uses).
5. Witnesses: Zabbix-class delete and re-create → new lifetime, old class and binds unusable (W10, W30).
   Historical ONE ID: re-create, restore, foreign-provider INSERT and id-changing UPDATE into the reserve are
   all refused, and no core lifetime is created (W46).

## D2 (P3). Delayed admission across adverse transitions (proposed policy)

**Historical evidence** (fixed at capture, read through R5 §2c): the committed binding for E and its source
digest, the class at E, the owner decision, the signed intent (HMAC), the history provenance, the cohort and
policy revisions named in the intent, and the capture and execution windows.

**Current mandatory admission predicates**: read at worker admission under the provider's send fence, and
read **again** immediately before target access (after any verification or delivery wait):

| Adverse transition after capture | Decision | Reason code |
|---|---|---|
| Ordinary supersession: E+1 is an authenticated ingestion by the same provider with the **same** source digest | admit (frozen source) | — |
| E+1 with a **different** source digest, or E+1 unbound (undeclared, suppressed, routed away) | refuse | `superseded_source_changed` / `superseded_unbound` |
| Class at E+1 (or later) is `conflict` or another provider | refuse | `ownership_changed_after_capture` |
| Alert deleted, or lifetime changed or retired | refuse | `lifetime_retired` |
| Customer changed | refuse | `customer_changed` |
| Provider registration generation of the capture retired, or the worker role mapping retired or reassigned | refuse | `registration_retired` |
| Cohort revoked, policy revision withdrawn, or the mirror flag now false | refuse | `policy_revoked` |
| Capture window: capture time ≥ `capture_expires_at` | refuse at capture | `capture_expired` |
| Execution window: `clock_timestamp()` ≥ `execution_expires_at` at admission **or** at the pre-target recheck | refuse | `execution_expired` |

Equality semantics are strict: an action is allowed only when the time is **before** the bound, so equality
refuses. The request-key retention clock (R4 §3) is a different clock, and it never stands in for these
worker gates. HMAC verification stays before any endpoint or credential resolution. Existing
provider-private send fences, leases and no-resend rules stay unchanged. No retired identity, generation
or role gains send authority, because the registration predicate is current and mandatory.

Witnesses: one per row above, plus boundary cases (exactly at, one microsecond before, and one after each
window bound) and a recheck after an injected verification/delivery wait (W31 split into W31a-W31i; W47).

## D3 (P4). Absent-target (create) concurrency

- Before resolving the target, the core takes a transaction-level advisory lock on a hash of the
  **natural identity** that Alerta's dedupe and correlate share: (customer, environment, resource). It does
  this with `pg_advisory_xact_lock`, so the lock is held until commit or rollback. Concurrent ingestions for
  the same natural identity serialize there, whether or not an alert row exists yet. A hash collision only
  over-serializes.
- Under that lock, the core resolves the target. If no row exists, the reservation is
  `new(natural identity)`, E = 1, and lifetime = (tombstone lifetime + 1), or 1. The INSERT then creates the
  row, the stamp trigger records its ID, and finalize requires counter = 1 and that the recorded row is the
  created one.
- Duplicate create: the second ingestion waits on the advisory lock. After the first commits, it resolves
  the now-existing row and takes the dedupe or correlate path. After the first rolls back, it creates the
  row itself.
- Rollback or retry: the advisory lock and everything stamped roll back together. A retry is a new
  ingestion, or a transport recovery when it carries the same request key (R4 §3).
- Lock order (R2 §3) becomes: request reservation row → natural-identity advisory lock →
  `public.alerts` row → `alert_epoch` row → class or ownership row → provider-private rows.
- Witnesses: two concurrent first creates of one natural identity (W48), first rolls back (W49).

## D4 (P5). Resolver snapshot, registry and adapter pinning

- **Per-request snapshot, no cache.** For action and housekeeping requests, the resolver runs once per
  request, after the permission and customer filters, under the `public.alerts` row lock and the class-row
  lock, which are held until the final commit. It records an immutable snapshot (lifetime, current epoch,
  class, registry generation). Finalize re-reads those facts under the same locks, and any difference
  aborts. There is no cache across requests and no provider switch inside a request.
- **Registry generation.** The registry is loaded at process start and is immutable until restart. A
  change is a new generation. In-flight requests holding an older generation abort at finalize if that
  generation was retired.
- **Pinned consumed adapters.** Readiness records, after configuration and after every `init_app` rebind,
  a digest of the callables actually in use:
  - `app.before_request_funcs` and `app.after_request_funcs`;
  - the status-change hook receivers;
  - the `qb.alerts` class identity and query guard;
  - `Alert.serialize` and `RichHistory.serialize`;
  - the log handlers carrying the redactor;
  - the session finalizer and commit guard.

  Each entry is a qualified name plus a code hash. Any drift refuses readiness. Witnesses cover two apps,
  repeated factories and both provider orders (W21, W50).
- **Worker role generations.** `provider_roles` rows carry (role, provider, registration generation,
  valid_from, valid_to). They are never edited in place: retirement sets valid_to, and reassignment creates
  a new row. `read_committed_evidence` answers only for captures whose registration generation equals the
  generation of the mapping row active for `session_user` at call time. A reused login-role name never sees
  another generation's or provider's evidence, and the answer for anything else stays the uniform
  `not_yours_or_absent` (W36, extended by W51).

## D5 (P5). Joint recovery and protection inventory (proposed)

| Object | Kind | Depends on | Protection |
|---|---|---|---|
| `ack_core` schema | schema | — | owner `ack_core_owner` (NOLOGIN) |
| `alert_epoch`, `epoch_events`, `epoch_bindings`, `alert_class`, `alert_tombstones`, `alert_ownership`, `ownership_transitions`, `ingestion_context`, `ingestion_claims`, `request_keys`, `provider_roles` | tables | schema | no direct grants; no TRUNCATE; append-only where stated |
| stamp | trigger function + trigger `AFTER INSERT OR UPDATE OF last_receive_id, last_receive_time ON public.alerts` | `public.alerts` | added to the **joint** frozen trigger inventory on `public.alerts`; a joint migration changes that inventory |
| step, resolver, reservation and evidence routines | `SECURITY DEFINER` functions | tables | controlled `search_path`; `PUBLIC` revoked; exact EXECUTE lists (R5 §3) |
| provenance and body digests | readiness records | all of the above | drift refuses readiness |

Boundaries:
- Migration: core objects form their own forward-only chain, installed and resealed separately from each
  provider. Provider reseal checks include the core objects they depend on.
- Restore: order and rules as in D1.4.
- Final-row ownership, permanent reserves and no-TRUNCATE stay as Voalack froze them.
- No core DDL runs during an open Voalack maintenance permit.
- The exact rendered definitions are exchanged before any implementation.

## Still open after r8 (stated, not hidden)

1. Who publishes `alerta-ack-core` and its review chain.
2. Request-key reuse beyond both retention windows is the client's error (R4 §3 step 5); the namespace
   contract text is pending.
3. Housekeeping: whether a generic per-alert failure aborts the rest of the run (R4 open question 3).
4. The D2 policy is proposed. The joint session may choose differently, for example universal refusal after
   any supersession.
5. Implementation-design prerequisites named by Codex (1ec50d44), still open:
   - a provider-scoped, fresh **current-predicate** read interface for workers, in addition to R5 §2c's
     captured facts. It returns current lifetime, epoch, binding, class and customer uniformly, without
     widening worker table privileges, and is re-read under compatible fencing;
   - rendered digest semantics for adapter pinning (effective ordered hook and receiver bindings plus their
     relevant bound state, not source hashes alone);
   - synchronization and read sources for authorization revocation, registry retirement and the
     pre-target rechecks;
   - restore, reseal, key-reuse namespace and the housekeeping choice.
