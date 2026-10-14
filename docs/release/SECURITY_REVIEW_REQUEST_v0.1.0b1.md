# Request for independent security review: Zabalack 0.1.0b1

Prepared 11 October 2026 for the owner to send. Replace the bracketed fields. Nothing
else needs to change.

**Reviewer:** xAI Grok 4.7, operated by the owner (independent AI-assisted external review;
not a security firm). **Internal reviewer, first pass:** Claude Fable 5.1 (this project's
implementing agent; not independent). **From:** the package owner.
**Report destination:** the owner's session with the reviewer; the owner stores the
report privately under `evidence/` and records its SHA-256 in `docs/RELEASE_AMENDMENTS.md`.

## What we ask

Review the security of one exact revision of Zabalack, an Alerta plugin and
worker that carries a human acknowledgement from Alerta back to the original
Zabbix problem event. Report findings privately. Do not publish them.

## Exact revision

| Item | Value |
|---|---|
| Repository | https://github.com/steliosx/zab-al-ack |
| Commit | `f85212e238fb6322802b163219096db48e428291` |
| Version | 0.1.0b1 |
| Wheel SHA-256 | `bf5b017111742c7c6c90a8701286987b20649a05a8aa7fa12515803f1617719b` |
| sdist SHA-256 | `ce346c9d562dc80ae094a16df4491d3812c47d1383f57eeaedf13051dbb0b821` |

Review this commit only. Later commits are out of scope unless we send a new request.

## Material supplied to the reviewer

Owner decision (11 October 2026): the external review starts after the repository
is published publicly. The reviewer then reads the public repository at the exact
commit above and, as a fixed-bytes cross-check, the source zip:

- `zabalack-0.1.0b1-source.zip` (SHA-256 `9940da37c6b160eae3861e006f278864857fd68bb4898ab7b46011c84bea1cc7`),
  the public source export built by CI run 38187030741 from the commit above.
- From inside the zip: `docs/DESIGN.md`, `docs/security_and_data.md`, `SECURITY.md`,
  `config/production.example.json`, the `sql/` tree and `src/zabalack/`.
- This request document.

The reviewer must not receive credentials, private evidence, signing material or
operational endpoints. Nothing in the zip contains them; verify the hash before use.

## Required output format

One report, plain text or Markdown, containing:

1. The commit and zip hash reviewed, as read from the material.
2. For each of the five questions below: a verdict (HOLDS / WEAK / BROKEN) and the
   code paths traced, with file and line.
3. A numbered list of findings. Each finding: severity (high / medium / low / info),
   title, file and line, what an attacker does, what happens, a minimal synthetic
   reproduction, a suggested fix. Write "no finding" where a question holds.
4. Files actually read. Anything not read is out of the review's coverage.

Speculation without a code reference is not a finding.

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

The owner pastes the complete report, unedited, to the implementing agent's
session. The agent hashes it, stores it under private evidence, and records the
hash. The report is not committed to the repository; a summary with the
reviewer's label goes in the release notes.

## How findings are settled

Each finding gets a written disposition: fixed in a named commit, accepted
with reason, or disputed with evidence. Dispositions are recorded in
`docs/RELEASE_AMENDMENTS.md`. A finding fixed after this revision produces a
new revision and, if you agree, a short re-review of the delta only. The
review report is retained privately; a summary with your permission goes in
the release notes.

## Timeline

The beta tag waits for this review and for a written disposition of every finding.
