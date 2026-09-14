# Independent review of 0.1.0a12

**Adopted in 0.1.0a13** (see `docs/RELEASE_AMENDMENTS.md` R18): the finding below
was independently reproduced on a separate Zabbix 7.0.10 instance before the
corrections were taken, and A4 was extended to the operator-provisioned
real-Zabbix fixture. The text below is the review's own account, as received.

This is a reviewed edition of the supplied candidate, not a new tagged release.
The original input, its original evidence and the independently rebuilt original
artifacts are retained separately in the complete handoff. See the handoff's
`review/REVIEW.md` and `review/results.json` for the final execution totals.

## Finding corrected: effective Zabbix role permissions

The original deployment fixture set `actions.default_access` and
`ui.default_access` to zero, but omitted most individual rules. In inspected
Zabbix 7.0.10 and 7.0.30 source, role creation starts with enabled old defaults;
the rule compiler retains that value for omitted rules. A default setting in a
create request is therefore insufficient evidence of the effective role.

A real 7.0.30 negative test reproduced an unintended suppression capability on
a synthetic event. Close and severity requests encountered read-only host
permissions before the intended action-role boundary. A corrected regression
also fails against the unmodified a12 provisioner. These failures are retained;
they are not replaced with successful reports.

`lab/zabbix_roles.py` now enumerates the inspected User-type action and UI rules,
sets each explicitly, and verifies the complete effective role through
`role.get`. Provisioning stops before users or tokens are created if the result
is missing, duplicated, ambiguous or broader than requested. The mock models
the upstream omitted-rule behavior, and eight added regressions exercise the
read-back guard. A ninth regression imports the provisioner from its staged
Docker context, ensuring that the new shared helper is included. See
**SPEC_AMENDMENTS A4** for the precise scope and remaining deployment requirement.

The production client already restricts writes to actions 2 and 6. Runtime
Python, SQL, migrations, grants and the wheel's runtime payload are unchanged.
Zabbix nevertheless shares the acknowledge/unacknowledge role capability: the
token's inherent authority is broader than the client's allowed requests.

## What the expanded lab establishes

A full disposable Ubuntu virtual machine runs real OS users, PostgreSQL 18.3,
PgBouncer 1.24.1, and source-built Zabbix 7.0.30 and 7.0.10. It uses QEMU CPU
emulation and a restricted guest network, with services on loopback. This
permits real database logins, leases, concurrent processes and source API
permissions without altering PostgreSQL's root guard or broadening SQL grants.
It does not establish production performance, HA topology or fleet capacity.

The original real-Zabbix contract is supplemented with ten real API cases:
resolved original versus recovery identity, complete multi-host validation,
action 2 without a comment, three excluded-action denials, outside-scope write
denial with an independent witness, the shared upstream unacknowledge capability,
expired credentials, and a server-accepted write followed by a lost response.
The latter checks the client/reference boundary; it is not an additional proof
of durable reconciler behavior or HA fencing. Repeated cases across versions
and test layers must not be added as unique acceptance cases.

Two failures in the review's first supplemental run were mistakes in its new
assertions: Zabbix returned integer acknowledgement-result IDs where the tests
expected strings. The existing client handles both forms. Only those new test
assertions changed; exact event identity and specific permission-denial checks
remain required. All first-attempt evidence is retained in the handoff.

## Review-harness corrections before the final runs

The new role module initially fell outside the deployment context’s filename
selection. The stager now copies it explicitly, and the regression imports both
modules from the isolated staged tree. This was an omission introduced by the
review’s helper split; it is not reported as an original a12 product defect.

The first installed-wheel attempt had four setup errors: a cold synthetic
`trigger.create` took 13 seconds under QEMU, exceeding this provisioning helper’s
10-second RPC timeout. The bounded lab provisioning/observation client now uses
30 seconds. The production worker client, delivery expiry, leases and all
assertions are unchanged. Failed attempt records and successful cleanup are
retained. Because these changes alter the harness digest, the final reference,
native and real-service records are regenerated; prior 74e… results are labelled
as intermediate evidence rather than rebound.

The next installed-wheel attempt passed three tests and twelve observations,
but sampled the projection immediately after the ledger reached ok. The worker
commits those steps separately; the retained database later had projection ok.
The sample did not record which field failed, so a pre-projection read is an
inference supported by the separate commits. The lab now boundedly waits for
the same full event/status/history/projection predicate. A tenth regression
proves that pending state, another event, an open status and a different history
type are all rejected before the correct projection is accepted. No production
code or SQL changed. The failing 022… attempt is retained separately.

## Evidence and remaining work

Source and harness digests bind the corrected runs independently of the original
candidate's records. The acceptance updater, export audit and release verifier
still reject stale or contradictory evidence. Documentation changes do not
rewrite execution records. The original screenshot remains labelled as a
supplied Zabbix 7.0.10 lab capture; no new browser capture is claimed.

**A2 remains open.** The current webhook artifact does not enable the inspected
7.0.30 peer/hostname verification options and redirects are followed. The
separate worker HTTP client verifies TLS and refuses redirects. A reviewed
webhook transport remedy and negative certificate/hostname/redirect tests are
the next release-blocking security work; a successful loopback lab does not
resolve this boundary.

The Docker Compose forms, hosted workflows, external security review, signed
attestation and production G1–G8 remain unexecuted or pending as identified in
the compatibility manifest. `INPUTS_REQUIRED.md` is the consolidated deployment
and publication input register. No production endpoint, credential or cohort
was used; generated production configuration remains disabled and empty.

For deployment and rollback, continue to use `deployment.md`, `runbook.md`,
`local-services.md` and `RENAME.md`. This review adds no database migration and
provides no same-database Ackline-to-Zab-al-ack upgrade. Discard old disposable
lab users/tokens with their lab, and provision a fresh fixture with the corrected
effective-role checks before relying on its permission evidence.
