# Webhook transport: the supported configuration (A2 candidate S2)

`docs/SPEC_AMENDMENTS.md` A2 is the Zabbix webhook → Alerta ingestion path. That path carries the scoped
ingestion key, so its TLS peer and its redirects must be controlled. This page describes candidate **S2**:
what it changes, the configuration it depends on, and what has been tested.

**Status of this page:**
- It is not an enablement: A2, G1 and G2 stay open until independent review and acceptance on the installed path.
- The worker's own Zabbix API client is a different path and is unchanged.

## What Zabbix 7.0.31 provides

Source: `src/libs/zbxembed/httprequest.c` at tag 7.0.31. The annotated tag object is `fc62e967`; it peels to
commit `e7da53eea21`, which is the revision `zabbix_server -V` prints for the Ubuntu package
`1:7.0.31-1+ubuntu24.04`.

- **Verification is available but off by default.** `new HttpRequest({SSLVerifyPeer: true, SSLVerifyHost: true})`
  enables libcurl peer and host verification.
  - The 7.0 branch reads these options from 7.0.28 onwards; 7.0.10–7.0.27 ignore them silently.
  - Trust comes from libcurl's compiled default CA file and directory on the executing server (on Ubuntu,
    `/etc/ssl/certs/ca-certificates.crt` and `/etc/ssl/certs`). There is no per-request CA option.
- **Redirects are always followed.** `CURLOPT_FOLLOWLOCATION` is hard-coded, and the script cannot limit redirects.
  - The method is set with `CURLOPT_CUSTOMREQUEST`, so a 301, 302 or 303 is re-sent as POST (with no body)
    rather than as GET.
- **The executing server's environment matters.** libcurl honours proxy variables in `zabbix_server`'s own
  environment (not the script's). A proxy there is a hop in this path.

## The supported configuration (all required)

| # | Requirement | Enforced by |
|---|---|---|
| 1 | The media script is `zabbix/v1/webhook.js`, which enables both verifications | `tools/webhook_transport_check.py` (script check) |
| 2 | Every server node that can execute the webhook runs the qualified build: 7.0.31, revision `e7da53eea21`. That means each HA node, since any of them can become active. Other numbers and branches are refused until separately evidenced | the precondition's executor check, run on each node |
| 3 | The ingestion route's certificate chains to a CA in each executor's system trust store, for the exact host name in `alerta_api_url` | the precondition's trust check (a handshake only) |
| 4 | The ingestion route never answers with a redirect. Neither may any layer in front of it (load balancer, VIP, WAF, canonical-path or authentication redirects) | the route configuration (next section), plus acceptance check T7 |
| 5 | No proxy in `zabbix_server`'s environment for this destination, or the proxy hop is part of the reviewed route | inspection of the service unit's environment |
| 6 | If the name resolves to IPv6, the route is served on that address with the same configuration | inspection; the lab covers IPv4 only |

Run the precondition on each node before enabling:
`tools/webhook_transport_check.py --zabbix-server <that node's executable> --media-export <configuration.export JSON>
--ingest-url <alerta_api_url> [--ca-file F --ca-path D]`.
It exits 0 with `READY` only when checks 1–3 all hold. Otherwise it exits 3 with a structured `REFUSED`, and it
never falls back to disabled verification. Malformed inputs, such as a port outside 1–65535 (443 applies only when
the URL names no port; an explicit `:0` is refused before any connection), a malformed export,
an executor that does not exit 0, or an unreadable input, are refusals, not tracebacks.

**What the operator binds.** This is a manual operator gate, not a runtime interlock. Its result is only as good as its inputs:
- `--zabbix-server`: the executable the node's running service actually executes. Read it from the unit or
  from `/proc/<pid>/exe` of the running `zabbix_server`.
- `--media-export`: exported at check time from the Zabbix that node serves.
- `--ingest-url`: the media type's currently stored `alerta_api_url`.
- `--ca-file`/`--ca-path`: the store that daemon actually uses. By default that is the Ubuntu libcurl store.
  If the daemon runs in a private mount namespace with its own trust (as in the lab below), run the check inside
  that namespace, or pass the files mounted there.

Record all four with the result. READY is not transport approval, beta approval or a redirect check.

## The ingestion route (candidate)

The candidate is `deploy/ingress/alerta-ingest.nginx.conf.template`. It is an ingestion-specific route, not a
firewall rule on the monitoring server (the rest of the monitoring server's traffic is unaffected).

Hops:
1. **Zabbix HttpRequest → the route.** The webhook verifies the peer and host name against the Zabbix server's
   store.
2. **The route → Alerta.** A fixed upstream, verified with `proxy_ssl_verify` against a pinned CA and name.

What the route does:
- Only POST on the exact path is forwarded, once (`proxy_next_upstream off`).
- Every upstream 3xx is intercepted and answered with 502 (`proxy_intercept_errors` with `error_page … = @refused_redirect`).
  `proxy_redirect off` on its own would only disable rewriting; the interception is what refuses redirects.
- The route emits no redirect of its own: 404, 403, 421 and 400 instead.
- Another name on the same address gets no TLS session (`ssl_reject_handshake`).

This candidate says nothing about an installed NetScaler or Nginx. The actual deployment still needs its own
inspected route and a G1/G2 disposition.

## Real-engine acceptance (isolated x64 lab)

`lab/webhook_transport_acceptance.py` runs as root inside a private mount namespace on the isolated lab host:
- The test CA directory and the synthetic names are bind-mounted for that namespace only. The host's trust
  store and hosts file are not modified.
- The packaged `zabbix_server` 7.0.31 runs the imported media type through its alerter, driven by the
  media-type test request (`alert.send`).
- The route is nginx 1.24.0-2ubuntu7.18, extracted from the distribution package (whose SHA-256 matches the
  signed index), not installed.
- Receivers record connections, handshake failures, and for each request the method, path, body digest and
  whether the synthetic canary key arrived.

Results: **41 of 41 acceptance cases passed** at S2 `094fce4`, and again in a separate final run at
`643a0a4` (`results.json` SHA-256 `9461a90c…f25e`; the earlier run's is `227bf687…36de`).

| Group | Observed |
|---|---|
| Trusted certificate, direct | delivered once with the key |
| Untrusted, missing anchor, wrong name, expired | Zabbix TLS failure; **no HTTP request, body or key at the listener** |
| Route, valid | delivered once upstream, access log `POST /api/alert 201` |
| Route, upstream 301/302/303/307/308 to another origin, an alternate port, plain HTTP, HTTP to the route's port, or another path | route answers 502; Zabbix failure; **no second request anywhere** (25 cases) |
| Another name on the route's address | no TLS session; nothing forwarded |
| Plain HTTP to the route's TLS port | 400; nothing forwarded |
| Disabled and echo paths | `disabled_reference_artifact` and `suppressed_echo`; nothing sent |
| T7 no-redirect check (it passes only an initial 2xx whose body is at most 1,048,576 bytes and is a JSON object with `status: ok`; unit-tested in `tests/release/test_webhook_transport_t7.py`, including the exact 1,048,577-byte R4 body) | the route gives an initial 2xx with `status: ok`; a deliberate 302 fails the check; a redirecting upstream through the route gives 502 and fails the check |
| Precondition tool | the qualified executor gives READY; a missing trust anchor and an unqualified executor (7.0.27) give REFUSED |

Baseline without the route (characterisation, TLS verification on):
- **301, 302 and 303 to another origin or to plain HTTP:** re-sent as POST with an empty body. Zabbix reported
  **success**, because the target answered.
- **307 and 308:** re-sent with the full body, including over plain HTTP.
- **The key:** dropped on cross-origin hops, but sent to a same-origin path, repeatedly, until the
  30-redirect limit.

This is why requirement 4 exists.

**Not yet exercised on the real engine** (planned for the integrated run):
- non-2xx, malformed JSON, `status: error` and over-limit response bodies (the Node contract covers the first
  three);
- a gateway-to-upstream bad certificate or wrong name.

Until then these results qualify the webhook-to-route hop only, not both TLS hops.

## Integration dependency: the deployment lab's own delivery

The deployment lab delivers the webhook to `https://runner:58081/alert`. At S2 `094fce4`, before any lab trust
existed, a native `cycle` ended FAIL with `synthetic_webhook_delivery_failed`. That run's retained Zabbix log has no
explicit TLS error, so missing lab trust is an inference about its cause.

The candidate remedy is `docs/lab-trust.md`: a fixed root-owned unit that gives the lab's `zabbix_server` only the
lab CA, in a private mount namespace, after dropping privilege.
- **Pre-install validation passed** on the x64 lab (8 of 8; the host was unchanged).
- **Without it, a cycle now refuses before any service starts** (`layout_not_ready`).
- **Installation needs the owner's authorization.** The ordinary integrated cycle is pending that.

At `244cefc`, the extended real-engine acceptance passed **47 of 47**. It adds the four response-contract cases
(non-2xx, malformed JSON, `status: error`, an over-limit body; each refused by the script) and the route's own
upstream TLS (an untrusted certificate and one for another name; each answered 502, with no HTTP request at the
bad upstream).
