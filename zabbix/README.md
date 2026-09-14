# Versioned Zabbix 7.0 reference artifacts

`v1/media_type.reference.json` is a JSON export shaped against Zabbix 7.0.0's pinned `templates/media/event_driven_ansible/media_event_driven_ansible.yaml`. Zabbix has no Alerta media type at that reference source path/tag. The new code is an independent reference implementation; no live media export was supplied. It is **disabled**, has no endpoint, key, customer or logical instance, and does not update existing actions. `v1/build_export.py` reproduces the JSON from `webhook.js`.

Source pin: Zabbix `7.0.0`, commit `49955f1fb5c9168a8a24b053f7ade6b3d903143c`; primary resolver: `src/libs/zbxexpression/expression.c`. The message branch supplies the original `event` to generic event macros, the recovery event separately to `get_recovery_event_value`, and `ack->message` only in update context. The macro reference is [Zabbix 7.0 supported macros](https://www.zabbix.com/documentation/7.0/en/manual/appendix/macros/supported_by_location). Actual deployed expansion is still G2 evidence.

| Parameter | Zabbix macro or reviewed mapping | Contract |
|---|---|---|
| `event_id` | `{EVENT.ID}` | Original problem ID on problem/update/recovery; never `EVENT.RECOVERY.ID` |
| `trigger_id` | `{TRIGGER.ID}` | Candidate cohort trigger |
| `event_recovery_id` | `{EVENT.RECOVERY.ID}` | Separate recovery ID required when `event_value=0` |
| `event_cause_id` | `{EVENT.CAUSE.ID}` | Positive symptom cause ID, otherwise null |
| `event_value` | `{EVENT.VALUE}` | 1 problem, 0 recovery |
| `event_update_status` | `{EVENT.UPDATE.STATUS}` | 1 update, 0 problem/recovery |
| `event_update_message` | `{EVENT.UPDATE.MESSAGE}` | Used only for early echo suppression; not copied into alert text |
| `alerta_resource`, `alerta_event` | Existing stable resource/family mappings | Engineer must preserve current identity and correlation; sample defaults are not a migration |
| `alerta_customer`, `zabbix_instance` | Reviewed static mapping per credential/action | Never inferred from a URL or host name |

All IDs stay strings to avoid JavaScript integer precision loss. Required unresolved macro IDs are rejected. An absent cause arrives as `*UNKNOWN*` (the server's rendering of an unresolvable macro, `expression.c`; see `docs/SPEC_AMENDMENTS.md` A3) or, if the macro is not expanded at all, as the literal `{EVENT.CAUSE.ID}`; both are converted to null and any other non-numeric text is rejected. A positive cause is retained. Recovery ID never substitutes for the original problem ID. Real problem, neutral-update, human-update and recovery deliveries have been observed once on the isolated deployment-path lab (`evidence/deployment-e2e-run.json`); a production-like instance remains G2.

Use a customer-bound, ingestion-only Alerta key with no `admin` or `admin:alerts` scope. The pre-receive plugin checks authenticated ingestion mapping against top-level customer; an `attributes.customer` value has no authority. API key owners do not become human actors. Credentials are operator-provisioned references, never synthetic fixtures copied into production.

The exact neutral write-back comment/reference pattern is suppressed before URL/key access and before constructing `HttpRequest`. This pattern is a routing convention, not cryptographic authentication. The Alerta ingestion identity remains an independent boundary. Ordinary human updates pass through the fact builder and remain subject to the existing Zabbix notification design. This webhook emits ingestion facts; it never invokes Alerta `/action` or imports source acknowledgement into Alerta workflow.

Every **other** webhook/script in matching update actions needs its own early suppression guard. Native email/command update operations cannot be made safe by this webhook: exclude affected triggers from the pilot or split actions under the approved G3 plan. `action=2` emits no comment marker; G3 must separately exclude or suppress its service-account acknowledgement update using tested routing. A successful JavaScript fixture is not evidence for T27; see `docs/deployment_acceptance.md`.

Review imported media in an isolated Zabbix first. Verify status is disabled, compare parameter mappings with the real export, preserve unrelated routing, configure CA trust on Zabbix, then exercise actual problem/update/recovery/symptom events. No artifact installs or changes a media type automatically. The repository never switches TLS verification off and does not accept event-supplied callback URLs.
