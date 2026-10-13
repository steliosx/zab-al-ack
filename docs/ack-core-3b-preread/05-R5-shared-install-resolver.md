# R5 — Shared install, resolver trust and ownership decision (pre-read draft)

Author: Opus (Zabalack session). Status: **PROPOSED DRAFT r4, discussion input only.** Codex review: r1
CHANGES_REQUIRED (750d397d: P1, P5); r2 CHANGES_REQUIRED (20261013T170941Z-codex-omarchy-d7391d2a: Q1, Q2,
Q4); r3 CHANGES_REQUIRED (20261013T174021Z-codex-omarchy-d7d59f1e: S2 answered in r4); r4 review
(20261013T180356Z-codex-omarchy-604951f7): those items closed as draft corrections; no open item in this file. R5 was
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
  are reachable only through core entry points that take that capability. For action and housekeeping
  requests, the core sets a transaction-local context (`set_config(..., true)`) before any provider hook,
  naming the enlisted owner and registration generation. For ingestion (R2 §4), no owner is enlisted:
  the context names the ingestion (request_id, nonce), and each claim names its claimant through the core
  claim entry point that takes the claimant's capability. The routines refuse unless the context is present
  and matches the declared call. Arbitrary SQL issued by code inside the same process could forge that context. Isolation against
  that threat rests on code review of every installed plugin, not on the database. This limit is stated, not
  hidden.
- **Outside the application (enforced by the database).** Reader, worker, installer and the other
  provider's separate roles get no EXECUTE on the mutating routines and no table access. The membership
  read function is EXECUTE-granted only to the application role.
- **Trigger-only versus callable.** The epoch stamp runs only as a trigger function. It is not callable and
  has no EXECUTE grant. The callable application interfaces are the R2 §4 step routines (`reserve_ingestion`,
  `claim_ingestion`, `close_claims`, `finalize_ingestion`), `reserve_request` (R4 §3), `resolve_owner` (core
  only) and `read_own_class`, which answers only for the context's provider. Workers use only
  `read_committed_evidence` (§2c).
- Provider schemas, keys, workers and Zabalack's readiness, grant, routine body-digest and seal checks stay
  separate and unchanged in kind.

Witnesses: other roles are denied on every routine. A call without the core context, or with a mismatched
provider or generation, is refused. A forged context inside the process is demonstrated as **not**
detectable by the database, which records the limit.

## 2c. Worker evidence interface (Q4)

Provider workers run as their **own database roles**, separate per provider (for Zabalack, the existing
worker role). This is unlike the shared application role, so the database **can** tell provider workers
apart.

- `ack_core.provider_roles` (role name → provider id, registration generation) is written by the installer
  only.
- `ack_core.read_committed_evidence(alert_id, lifetime, nonce_or_epoch)` is `SECURITY DEFINER` and read-only.
  It derives the provider from `session_user` through `provider_roles`; **no provider argument exists**.
  It returns only the calling provider's own committed facts:
  - the nonce → E mapping;
  - the owner decision for that context;
  - the binding for exactly that (alert, lifetime, E) with its source digest;
  - the class **at E** (historical, from provenance).

  For any alert, epoch or context that is not the caller's, it returns the same `not_yours_or_absent`
  answer, so there is no membership oracle.
- EXECUTE is granted to each registered provider **worker** role only. It is not granted to the application
  role, readers, the installer at runtime, or `PUBLIC`. Workers get no table access and cannot mutate core
  tables.
- Binding to the captured operation: the provider's capture stores (alert_id, lifetime, nonce or E,
  source digest, owner-decision digest). The worker compares those with the interface's answer before HMAC
  verification and before any endpoint or credential resolution.
- Captured versus current: the worker checks the captured E binding and the class at E. Whether it also
  requires the **current** class and epoch depends on the joint delayed-admission choice (R3 §2b).

## 3. Proposed database inventory (for review; no DDL accepted)

| Object | Owner | Grants |
|---|---|---|
| schema `ack_core` | `ack_core_owner` | USAGE to the Alerta application role **and** to each registered provider worker role (schema lookup only; S2) |
| tables `alert_epoch`, `epoch_events`, `epoch_bindings`, `alert_class`, `alert_tombstones`, `alert_ownership`, `ownership_transitions`, `request_keys` (R4 §3), `ingestion_context`, `ingestion_claims`, `provider_roles` | `ack_core_owner` | none direct; access only through routines |
| trigger `ack_core_epoch` `AFTER INSERT OR UPDATE OF last_receive_id, last_receive_time ON public.alerts` | `ack_core_owner` function | — |
| trigger-only routine: stamp (no EXECUTE grant) | `ack_core_owner` | none |
| callable routines (application): reserve_ingestion, claim_ingestion, close_claims, finalize_ingestion, resolve_owner, reserve_request, read_own_class | `ack_core_owner`, `SECURITY DEFINER`, `search_path = pg_catalog, ack_core`; each requires the core transaction-local context (§2b) | EXECUTE to the Alerta application role only; `PUBLIC` revoked |
| callable routine (workers): read_committed_evidence | `ack_core_owner`, `SECURITY DEFINER`, read-only; provider from `session_user` (§2c) | EXECUTE to registered provider worker roles only. Worker roles have no table privileges, no EXECUTE on any mutating routine, and no `PUBLIC` path. Readiness pins the exact `session_user` login-role → provider mapping and generation; a `SET ROLE` alias is not accepted |
| trigger attachment | installer role, at install time only | installer authority is not a runtime grant |

The stamp trigger and the provider capture triggers have **no required firing order**. Captures reference
the context nonce, not the stamp (R2 §4 step T). Readiness freezes the exact trigger names, functions and
body digests. An unexpected trigger set on `public.alerts` refuses readiness.

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
2. (Resolved in r3: no firing order is required, because captures reference the context nonce. Readiness
   freezes the trigger set; see §3.)
3. How Zabalack's existing `zabalack_capture` trigger and grants map into the core inventory and Zabalack's
   reseal checks (`sql/install/12_application_grant.sql`, `13_reseal.sql`).
4. Whether the resolver decision is cached per epoch, or recomputed per request under the lock.
