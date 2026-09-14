# Ackline → Zab-al-ack (0.1.0a9)

The public name is **Zab-al-ack**, pronounced “Zabalack”: Zabbix, Alerta and
acknowledgements. `zabalack` is the unhyphenated technical identifier. This
is an independent integration, not an official Alerta or Zabbix product.

## Compatibility contract

| Surface | Ackline a8 and earlier | Zab-al-ack a9 |
|---|---|---|
| Distribution, Python import, CLI | `ackline` | `zabalack` |
| Alerta normalisation entry point | `ackline_zabbix_normalise` | `zabalack_zabbix_normalise` |
| Alerta signing entry point | `ackline_zabbix_ack` | `zabalack_zabbix_ack` |
| Alerta application factory | `ackline.plugins:create_app()` | `zabalack.plugins:create_app()` |
| Alerta config key | `ACKLINE_CONFIG_FILE` | `ZABALACK_CONFIG_FILE` |
| Migration DSN environment key | `ACKLINE_MIGRATION_DSN` | `ZABALACK_MIGRATION_DSN` |
| Database schema / role prefix | `ackline` / `ackline_*` | `zabalack` / `zabalack_*` |
| Capture trigger | `ackline_capture` | `zabalack_capture` |
| Health schema-version field | `ackline_schema_version` | `zabalack_schema_version` |
| Unit prefix, service account, installation paths | `ackline` | `zabalack` |
| History marker namespace | `[ackline:v1 …]` | `[zabalack:v1 …]` |
| HMAC domain (NUL separators) | `ackline\0v1\0` | `zabalack\0v1\0` |
| Neutral source comment prefix | `[ackline]` | `[zabalack]` |

Alerta's `zabbix_ack_sync*` projections, source-contract version 1 and `zabbix_*`
source facts remain stable. These identify the integration contract rather
than its brand. `ACK_TEST_*`, `ACK_LAB_*`, `ack_test.*` and upstream Alerta roles
remain lab/upstream interfaces. Database schema version remains 3 inside the
**new namespace**; it does not imply cross-namespace compatibility.

There are no legacy import, executable, configuration or proof aliases.
Old signed captures are rejected before target access, including a proof whose
visible prefix was changed without recomputing its HMAC. Never rewrite old
markers or re-sign historical captures. New and old proof strings are redacted;
old reserved producer attributes are stripped. The media artifact recognises
both exact neutral comment formats solely to suppress echoes before endpoint
or credential access. This does not accept an old signed action for delivery.

## Existing installations: retain, fence, then requalify

This release **does not implement an in-place upgrade from Ackline**. The role
installer, migration runner, baseline migration and trigger attach/enable scripts
refuse a database containing an `ackline` schema. Do not drop, rename or hide that
schema to bypass the guard. Do not copy its checksum ledger into `zabalack`.
The existing Ackline release and its immutable SQL remain in the historical
handoff and Git parent `aaadf4919f837b4a003a8fc57c469f70ca98d08c`.

The following is an engineer-reviewed transition procedure, not an automatic
production action or an executed deployment claim:

1. Inventory any existing Ackline API hooks, workers, reconcilers, queues, grants,
   signing-key references, media operations and targets using the old release's
   read-only collectors. Retain protected backups and verify a restore in a lab.
2. Under a separately approved operational change, pause old delivery, fence all
   old workers and API signing hooks, account for in-flight requests and resolve
   unknown outcomes through the old release's read-only reconciliation. An empty
   queue alone does not establish that an already-sent request was cancelled.
3. Retain the old database, ledger, signed history, versioned deployment files
   and verification-key references according to the records policy. Do not move
   unresolved work into the new namespace or run both writers for one cohort.
4. Install a9 against a **fresh, isolated Alerta database** and dedicated
   `zabalack` identities. Start with `config/production.example.json`, which has
   no enabled integration, external writes, endpoints or cohorts. This release
   does not supply an Alerta data-transfer or database-repointing procedure.
5. Review the new plugin order/factory, protected secret references, all role
   grants, media export and monitoring labels. Supply new configuration
   explicitly; an old Alerta config key leaves the new plugin disabled.
6. Repeat the native, real-Zabbix and deployment-path checks on the intended
   installation and complete G1–G8 before any pilot enablement. Restore tests,
   cutover ownership and an approved old/new target exclusion are required.

For an existing installation needing continuity in the same Alerta database,
remain on its current reviewed Ackline release until a dedicated, tested
state-preserving upgrade is designed and separately approved. A brand rename is
not sufficient justification to discard operational evidence.

## New-install commands and rollback

Use the exact a9 source release with its a9 wheel. See `docs/deployment.md` for
the separate role installer, schema owner and Alerta trigger principal; the
worker never gains installation authority. For local evaluation:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --require-hashes -r requirements-quality.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
npm ci --prefix lab --legacy-peer-deps --ignore-scripts
.venv/bin/zabalack --config config/production.example.json config-check
.venv/bin/python tools/quickstart.py --isolated-lab
```

`deploy/rollback.py` defaults to a **plan**, retains capture/ledger data and never
unacknowledges a Zabbix event. Follow `docs/rollback.md` to hold a9 and retain
read-only investigation. Returning to Ackline requires its original artifacts,
configuration and database; do not point its binaries at the new schema. Neither
branch can safely undo a source acknowledgement already sent by the other.

## Evidence and publication

`docs/rename_manifest.json` records old/new SQL hashes and unchanged dependency
pins. `ALPHA_REPORT.md` and `evidence/` contain newly executed a9 results only.
Historical a8 and earlier reports remain historical; none are rebound to a9.
The website is informational. Package/repository ownership, maintainer/contact
and licence review remain in `config/publication.pending.json`; a namespace
lookup does not establish trademark clearance or reserve a package name.
