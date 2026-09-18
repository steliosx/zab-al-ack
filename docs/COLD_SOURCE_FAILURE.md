# Cold source failure and conservative API-error recovery

This disposition follows the native ARM64 batch assessment on PR #24
`7ae972e8488ab9602c60a4895a98e1204d534207` (base
`013fbc6f99c171cc484cbbaafcec4de418b4dfe7`). That candidate, version
`0.1.0a16` and its wheel SHA-256
`1f072f0443a2499cecb27d45ffcee7401c3d5c22b5dd7c3ae5be627ff8463774`
remain historical qualified artifacts, not artifacts containing this correction.

## Observation and primary-source diagnosis

The separately retained batch-02 archive SHA-256
`88edf1bac58e8fa661736f3698850ff0f0126a2d7521db81829e296822809c37`
contains an `ids_pkey` collision initializing the Zabbix acknowledgement counter,
two product write attempts, one successful action, one failed action and 48
actions held by the instance circuit. The exact failed JSON-RPC envelope was
not retained. No automatic replay or circuit reset was performed. The later
500-case pass used a separate serialized synthetic acknowledgement to initialize
the counter; it qualifies that warmed fixture, not cold initialization or recovery.

Inspected official Zabbix tag `7.0.10` points to commit
`237358b56b2584b94b1f8358f479ade669693ba2`:

- [DB.php](https://github.com/zabbix/zabbix/blob/237358b56b2584b94b1f8358f479ade669693ba2/ui/include/classes/db/DB.php):
  `reserveIds()` locks an existing counter row; the absent-row path calls
  `refreshIds()`, which deletes, reads the maximum and inserts a new counter.
  Two concurrent first allocations can race on that insertion. This explains
  the retained SQL failure; it is not proof of every runtime interleaving.
- [CLocalApiClient.php](https://github.com/zabbix/zabbix/blob/237358b56b2584b94b1f8358f479ade669693ba2/ui/include/classes/api/clients/CLocalApiClient.php):
  the exception path attempts rollback, then rethrows database exceptions.
  [api_jsonrpc.php](https://github.com/zabbix/zabbix/blob/237358b56b2584b94b1f8358f479ade669693ba2/ui/api_jsonrpc.php)
  maps uncaught failures to `-32603`. That is the source-derived expected code,
  not a recovered copy of the missing original response.
- [CJsonRpc.php](https://github.com/zabbix/zabbix/blob/237358b56b2584b94b1f8358f479ade669693ba2/ui/include/classes/core/CJsonRpc.php)
  maps both permission and internal exceptions to `-32500`. Commit is inside
  the API client's try block; its later rollback attempt does not establish a
  universal no-effect guarantee. Generic API errors must not authorize replay.

These are tagged-source observations. Source files and retrieval identities are
privately retained; the stopped container's PHP bytes were not independently
compared. Neither the SQL error nor a successful visibility read proves the exact
failed event's final acknowledgement history or remote settlement.

## Focused correction

The affected invariant is DESIGN section 6.3: an unconfirmed remote write stays
uncertain and cannot be resent merely because it returned an error. The client
keeps the source-verified dispatch/validation codes `-32600/-32601/-32602` as
rejections. Application/internal/system/transport and unfamiliar codes instead
raise a sanitized `api_application_error`; a write remains uncertain. Upstream
message/data text is neither logged nor used to guess permission status. Boolean
codes are invalid, not integer rejections.

Forward migration 010 preserves 001–009 and changes only the instance suspension
and incident predicates in `worker_call()`: application errors suspend delivery
even while the action is pending/uncertain. Read-only reconciliation remains
eligible; an unsuccessful reconciliation read keeps uncertainty and eligibility.
An exact observed effect can settle the action without resetting the circuit.
Ordinary retry, circuit reset, elapsed time and expiry cannot establish no effect.
No direct grants, target expansion or retry middleware are introduced.

Forward migration 011 preserves 001–010 and changes one branch of the same
function. 010 suspends the instance where the reporting worker still holds its
delivery lease; a report arriving after that lease expired returned from the
late-response branch before the suspension and incident statements, so the
action was fenced and the instance was not, and the next action on it wrote
into a remote state nobody had established. 011 applies the same suspension and
incident there, bounded to an attempt bound to that worker and generation and
to an action still open, so an operator's explicit settlement and circuit reset
are not overruled by a stale callback about the same attempt. Schema stays 4,
grants are unchanged, the shipped body digest is updated and resealing is
required, exactly as 010 requires. The worker capability gate is unchanged:
011 strengthens behaviour 010 already advertises, so `api_error_recovery_version`
stays 1 and the qualified worker runs against either.

Forward migration 012 corrects 011's bound. 011 restricted the late suspension
to an action still open, so that an operator's explicit settlement was not
overruled; but an authorised retry legitimately returns a settled action to a
deliverable state, and the old callback then re-suspended the instance and
reactivated its incident behind that recovery. Whether the evidence is still
live is a property of the attempt, which the action update in the same branch
already reads through `attempt_unsettled()`; the instance decision now reads it
the same way. A fenced-and-settled no-effect confirmation disposes of the
attempt for good, so no later retry revives it. Schema stays 4, grants are
unchanged, the digest is re-recorded, resealing is required and
`api_error_recovery_version` stays 1.

Install the forward migration and runtime together through the reviewed migration
procedure with acknowledgement paths fenced and delivery disabled. Migration 010
requires schema 4 and matching routine-body fingerprints, retains schema version
4 and existing grants, updates the shipped body digest and requires explicit
resealing. The worker health gate requires 010's recovery capability, including
on an otherwise healthy sealed schema-4 database. See the source-backed
[amendment A6](SPEC_AMENDMENTS.md#a6--ambiguous-zabbix-api-errors-preserve-uncertainty-and-suspend-writes).
Rollback is
a reviewed forward correction while delivery is disabled, never editing applied
migrations or deleting ledger records.

Historical actions labelled `api_permission_or_request_rejected` are not rewritten
by this migration: their records lack the original code. Keep their instance
paused while reviewing attempts, leases and exact-event state/history. A reset or
ordinary retry is not sufficient evidence. Any needed administrative disposition
must be a separately reviewed, evidence-bound procedure; do not infer settlement
from the old reason, and do not manually turn it into PASS.

## Revalidation decision and remaining qualification

This is a demonstrated failure-handling defect, within the feature freeze.
The new source requires focused client classification, source-effect/no-effect,
read-failure recovery, circuit, expiry, operator-retry and fresh/forward-migration
checks plus independent technical review. Before replacing the frozen artifact,
run the new integration head's required Actions checks, producer-to-package proof
and affected installed-wheel/native recovery cases. Rebuild only as an explicitly
identified new candidate; never silently replace the qualified wheel or rebind
old batch results. Existing checks/security/independent-approval gates still apply.

This correction does not repair Zabbix's allocator. Installed-source cold-start
qualification remains open: use a fresh isolated instance, a bounded pair of
synthetic events, retain sanitized error codes and complete exact-event history,
then verify safe suspension and reviewed recovery without a duplicate write.
Do not seed production counters, acknowledge a real event as hidden warm-up or
broaden Zabbix privileges. A serialized installation canary is an explicit
environment precondition only after its scope/authorization is approved; it is
not general cold-start acceptance. Native x64 remains deferred, and T30/G1–G8
and installed-account A4/G5 remain separate requirements.
