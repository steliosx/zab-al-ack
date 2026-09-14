# Security policy

## Supported versions

0.1.0a13 is an experimental evaluation release. No version is currently endorsed
for production or covered by a response-time SLA. Security fixes will target
the newest alpha; a maintained/backport version table will be added when stable
support begins. Independent external security review remains pending.

## Reporting a vulnerability

On the official public repository, use **Security → Report a vulnerability**.
The publishing maintainer must enable private vulnerability reporting and
confirm that named primary and backup recipients receive it before publication.
This local candidate has no public repository or operational reporting channel.
If no private report option is available, open only a generic request for a
private contact; omit exploit details and all sensitive material.

Include affected versions, a minimal synthetic reproduction, expected versus
actual behaviour and impact. Do not include live credentials, signing proofs,
customer histories or operational endpoints. Maintainers should coordinate a
fix and advisory privately, then publish affected/fixed versions and mitigation.
No response deadline is promised until maintainers adopt and staff one.

## Security boundaries

The most sensitive paths are human authority and cohort binding, HMAC key
handling, replay/action/effect identity, SECURITY DEFINER privileges, capture
health, logging redaction, shared leases and uncertain remote writes. See
`docs/security_and_data.md`, `docs/SPEC_AMENDMENTS.md` and the security tests.
The Alerta application adapter is mandatory; arbitrary observability agents,
new logging handlers and plugins require a deployment review.

Mocked API and WASM passes do not establish actual tenant permissions, native
authentication, transaction-pool isolation, notification suppression or HA.
G1–G8 must be completed for each deployment before external writes are enabled.
