# Request for independent security review: Zabalack 0.1.0b1

Prepared 11 October 2026 for the owner to send. Replace the bracketed fields. Nothing
else needs to change.

**To:** [reviewer name, organisation]
**From:** [owner name], package owner, Zabalack
**Private report destination:** [email or GitHub private vulnerability report URL]

## What we ask

Review the security of one exact revision of Zabalack, an Alerta plugin and
worker that carries a human acknowledgement from Alerta back to the original
Zabbix problem event. Report findings privately. Do not publish them.

## Exact revision

| Item | Value |
|---|---|
| Repository | https://github.com/steliosx/zab-al-ack |
| Commit | `fee1528a6853cc3f5a19d41594fcf72f0e186f59` |
| Version | 0.1.0b1 |
| Wheel SHA-256 | `38161158addaeb7795f2df0633663d84554c26046fdf7cea7a25be558868941a` |
| sdist SHA-256 | `df9a3e79f530c248cd159248defefef6025957eb086ed07ac4b8229412b3166e` |

Review this commit only. Later commits are out of scope unless we send a new request.

## Scope

In scope:

- The runtime package `src/zabalack/` (plugin, worker, reconciler, RPC client,
  policy, crypto, query guard, configuration, health, CLI).
- The SQL schema and migrations in `sql/` (roles, grants, fences, ledger).
- The trust boundaries described in `docs/DESIGN.md` sections 3, 4 and 5 and
  in `docs/security_and_data.md`: who may acknowledge, which alerts are
  eligible, how one action maps to one Zabbix event, how credentials are held.
- Deployment tooling in `deploy/` and the configuration example
  `config/production.example.json`.

Out of scope:

- Alerta and Zabbix themselves.
- The test harness, labs and CI workflows, except where they reveal a runtime defect.
- Dependency advisories and secret scanning (already automated; results in
  `evidence/security-scans.json`).

## Questions we most want answered

1. Can an unauthorised actor cause Zabalack to acknowledge or close a Zabbix event?
2. Can one Alerta action affect more than one Zabbix event?
3. Can a lost, late or duplicate Zabbix response leave the ledger stating something false?
4. Can the database roles and grants in `sql/` be used to read or write outside the intended tables?
5. Are Zabbix credentials and signing material exposed in logs, evidence or API responses?

## How to report

Send findings to the private destination above. Include affected files and
lines, a minimal synthetic reproduction, expected versus actual behaviour and
impact. Do not include live credentials or operational endpoints. We will
acknowledge receipt within [N] business days.

## How findings are settled

Each finding gets a written disposition: fixed in a named commit, accepted
with reason, or disputed with evidence. Dispositions are recorded in
`docs/RELEASE_AMENDMENTS.md`. A finding fixed after this revision produces a
new revision and, if you agree, a short re-review of the delta only. The
review report is retained privately; a summary with your permission goes in
the release notes.

## Timeline

Requested completion: [date]. The beta tag waits for this review.
