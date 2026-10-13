# R5 — Shared install, resolver trust and ownership decision (pre-read draft)

Author: Opus (Zabalack session). Status: **PROPOSED DRAFT, discussion input only.** Codex review: pending
(R5 raised in 20261013T005221Z-codex-omarchy-1e79ef92). Not accepted DDL, roles, grants or implementation.
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
  enrolment check. It decides exactly one owner, `none`, or `conflict` for the alert's current
  (lifetime, epoch) from each registered provider's **class evidence**:
  - Zabalack: an authenticated epoch binding (R2 §4) for this epoch.
  - Voalack: its r8 lifetime/reservation class record.
  Mutable `source_system`, or event/resource classification alone, is not authenticated ownership.
- Decision record `ack_core.alert_ownership` (alert_id, lifetime, epoch, owner ∈ {provider id, none,
  conflict}, resolver_generation, evidence_digest, decided_at), plus append-only
  `ack_core.ownership_transitions` with immutable provenance (previous decision, cause, txid).
- Written only by core-controlled transitions: a `SECURITY DEFINER` routine owned by a non-provider role
  `ack_core_owner` (NOLOGIN). Providers can execute a read function that answers **only** "you own this
  epoch: yes/no". It never says whether another provider exists or holds evidence.
- One owner per request. The resolver enlists only that owner in the R4 transaction. Non-owners return
  unchanged.
- Conflict: the managed action is **refused** and neither provider stages (Astra; Zabalack position §5.3).
  Dual pass-through is not allowed, because the local ACK would then appear successful.

## 3. Proposed database inventory (for review; no DDL accepted)

| Object | Owner | Grants |
|---|---|---|
| schema `ack_core` | `ack_core_owner` | USAGE to the Alerta application role only |
| tables `alert_epoch`, `epoch_events`, `alert_ownership`, `ownership_transitions` | `ack_core_owner` | none direct; access only through routines |
| trigger `ack_core_epoch` `AFTER INSERT OR UPDATE OF last_receive_id, last_receive_time ON public.alerts` | `ack_core_owner` function | — |
| routines: stamp (trigger), bind_epoch, read_own_epoch, resolve_owner, read_own_decision | `ack_core_owner`, `SECURITY DEFINER`, `search_path = pg_catalog, ack_core` | EXECUTE to the Alerta application role; `PUBLIC` revoked |

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
