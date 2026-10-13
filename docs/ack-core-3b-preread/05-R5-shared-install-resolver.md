# R5 — Shared install, resolver trust and ownership decision (pre-read draft)

Author: Opus (Zabalack session). Status: **PROPOSED DRAFT r2, discussion input only.** Codex review: r1
CHANGES_REQUIRED (20261013T163920Z-codex-omarchy-750d397d; P1 and P5 answered here); r2 pending. R5 was
raised in 20261013T005221Z-codex-omarchy-1e79ef92. Not accepted DDL, roles, grants or implementation.
Zabalack's current installs are listed in `01-handoff-v3.md` §1, with exact `plugins.py`, `normalizer.py`
and `query_guard.py` locations at `main` `d06a3632`.

## 1. One versioned shared install (proposed shape)

`alerta-ack-core`, one released version pinned by both providers. It owns:

| Component | Replaces (Zabalack today) | Rule |
|---|---|---|
| WSGI factory | `zabalack.plugins:create_app` | one Gunicorn target; calls `alerta.app.create_app`, then each enrolled provider's `configure` hook in a fixed, recorded order |
| Provider registry | — | exact module, class and hook identities, provider version, registration generation; unknown or changed identity → refuse to start |
| Reviewed-callback registry | `@app.after_request` redaction, `@app.before_request` reader refusal | callbacks run in a fixed, recorded order; an unreviewed callback → refuse to start |
| Privacy adapter | `Alert.serialize` / `RichHistory.serialize` filters | filters the **union** of all enrolled providers' reserved keys; each provider declares its key prefixes; installed once |
| Query guard | reader guard on `qb.alerts` + rebind follower | one guard, one follower, idempotent across `init_app` rebinds and per app |
| Log redaction | `CaptureLogRedactor` on app and ancestor handlers | one redactor, union of patterns |
| Routing observer | receiver-iterable recorder | one observer; providers read its record |
| Transaction owner | provider early commits | R4 core-owned request transaction |
| Ownership resolver | provider `source_system` checks | §2 |

Freeze before private settings: union-key filtering, and "foreign hooks return unchanged before touching
any private state" behaviour, are fixed in the core before any provider reads its settings, credentials or
health. Idempotence: repeated factory calls, two apps in one process, and `init_app` rebinding each install
exactly once per app.

## 2. Ownership resolver (proposed)

- Runs per request after Alerta's permission and customer filtering, and **before** any provider's private
  enrolment check. It decides exactly one owner, `none`, or `conflict` for the alert's lifetime from the
  **durable classification** `ack_core.alert_class` (R2 §4a), not from the current epoch's binding:
  - Zabalack class evidence: an authenticated, bound ingestion of this lifetime (it establishes the class).
  - Voalack class evidence: its r8 lifetime/reservation class record.
  Mutable `source_system`, or event/resource classification alone, is not authenticated ownership.
  The current epoch's binding is a **separate** check, made by the owner at action time (R2 §5). An
  owned-unbound alert (class set, current epoch unbound) resolves to its owner, and that owner refuses the
  managed action. The alert is never treated as foreign.
- Decision record `ack_core.alert_ownership` (alert_id, lifetime, epoch, owner ∈ {provider id, none,
  conflict}, resolver_generation, evidence_digest, decided_at), plus append-only
  `ack_core.ownership_transitions` with immutable provenance (previous decision, cause, txid).
- Written only by core-controlled transitions: `SECURITY DEFINER` routines owned by a non-provider role
  `ack_core_owner` (NOLOGIN). Providers read through a function that answers **only** "you own this
  lifetime: yes/no". It never says whether another provider exists or holds evidence.
- One owner per request. The resolver enlists only that owner in the R4 transaction. Non-owners return
  unchanged.
- Conflict: the managed action is **refused** and neither provider stages (Astra; Zabalack position §5.3).
  Dual pass-through is not allowed, because the local ACK would then appear successful.

## 2b. Trust boundary for provider identity (P5)

Both providers run inside one Alerta process, which connects as one application role. Database ACLs
therefore **cannot** tell which provider is calling a routine, and a provider ID passed as an argument proves
nothing. This draft does **not** claim ACL-enforced isolation between providers. It states the boundary as
follows:

- **Inside the application process (trusted administrative boundary).** Provider identity is enforced in
  Python by ack-core: each enrolled provider receives a registration capability object (exact
  module/class/hook identity, registration generation) when the core starts. The mutating core routines
  are reachable only through core entry points that take that capability. Before any provider hook runs, the
  core sets a transaction-local context (`set_config(..., true)`) naming the enlisted provider and
  registration generation. The routines refuse unless that context is present and matches the declared
  call. Arbitrary SQL issued by code inside the same process could forge that context. Isolation against
  that threat rests on code review of every installed plugin, not on the database. This limit is stated, not
  hidden.
- **Outside the application (enforced by the database).** Reader, worker, installer and the other
  provider's separate roles get no EXECUTE on the mutating routines and no table access. The membership
  read function is EXECUTE-granted only to the application role.
- **Trigger-only versus callable.** The epoch stamp runs only as a trigger function. It is not callable and
  has no EXECUTE grant. The callable interfaces are `bind_epoch` (core entry point, requires the reserved
  ingestion context, R3 §2), `resolve_owner` (core only), `read_own_class` and `read_own_epoch` (answer
  only for the context's provider).
- Provider schemas, keys, workers and Zabalack's readiness, grant, routine body-digest and seal checks stay
  separate and unchanged in kind.

Witnesses: other roles are denied on every routine. A call without the core context, or with a mismatched
provider or generation, is refused. A forged context inside the process is demonstrated as **not**
detectable by the database, which records the limit.

## 3. Proposed database inventory (for review; no DDL accepted)

| Object | Owner | Grants |
|---|---|---|
| schema `ack_core` | `ack_core_owner` | USAGE to the Alerta application role only |
| tables `alert_epoch`, `epoch_events`, `epoch_bindings`, `alert_class`, `alert_tombstones`, `alert_ownership`, `ownership_transitions`, `requests` (R4 §3), `ingestion_context` | `ack_core_owner` | none direct; access only through routines |
| trigger `ack_core_epoch` `AFTER INSERT OR UPDATE OF last_receive_id, last_receive_time ON public.alerts` | `ack_core_owner` function | — |
| trigger-only routine: stamp (no EXECUTE grant) | `ack_core_owner` | none |
| callable routines: bind_epoch, resolve_owner, read_own_class, read_own_epoch | `ack_core_owner`, `SECURITY DEFINER`, `search_path = pg_catalog, ack_core`; each requires the core transaction-local context (§2b) | EXECUTE to the Alerta application role only; `PUBLIC` revoked |

Lifecycle:
- Install and upgrade are forward-only, with a version and capability row. Rollback to a pre-core provider
  is declared incompatible, and core records are preserved.
- Dependencies are recorded (`pg_depend`) and checked by both providers' readiness and reseal routines.
- Restore follows Voalack's protection and restore contract. No TRUNCATE.
- No ack-core DDL runs during an open Voalack maintenance permit.
- The exact rendered definitions are exchanged before implementation.

## 4. Explicit unknowns

1. Who publishes and versions `alerta-ack-core` (Voalack hosts `packages/ack-core` today), and its review
   chain.
2. Trigger firing order between `ack_core_epoch` and the providers' AFTER triggers on `public.alerts`
   (PostgreSQL fires same-event triggers in name order).
3. How Zabalack's existing `zabalack_capture` trigger and grants map into the core inventory and Zabalack's
   reseal checks (`sql/install/12_application_grant.sql`, `13_reseal.sql`).
4. Whether the resolver decision is cached per epoch, or recomputed per request under the lock.
