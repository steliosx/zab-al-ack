# Reviewed deployment procedure

**a9 namespace change:** read [RENAME.md](RENAME.md) before installation or rollback. This release uses a fresh `zabalack` database namespace and refuses an existing Ackline schema.

The supplied request authorised development and isolated lab work only. None of these procedures has been applied to production. The reference package has external writes disabled and no production cohort/endpoints. Gate evidence and commands run are recorded separately; inspect `acceptance_matrix.csv`, `deployment_gates.csv`, `compatibility_manifest.json`, `reference_manifest.json` and `SPEC_AMENDMENTS.md` before selecting targets.

## 1. Package and inventory

Verify `dist/SHA256SUMS` against the exact wheel/archive delivered. Use the repository Python/dependency pins, build command and test runner in README/AGENTS. Supply the consolidated inputs in `INPUTS_REQUIRED.md`. Run only the explicitly selected, authorised read-only collectors. A static configuration parser cannot establish effective runtime values or application/probe routing; capture runtime evidence separately. Do not install an unrecognised Alerta/Zabbix version or broaden grants automatically.

Stage a reviewed private Ansible inventory, an offline wheelhouse and the actual config/secret references. Keep all production URLs and cohorts out of distributed samples. The installed `zabalack --config PATH config-check` command parses configuration without resolving endpoint secrets or contacting targets. Secret files must be regular owner-controlled files with mode 0600 or stricter; the worker/service user must be their owner or able to read a root-owned protected file through an explicitly reviewed delivery mechanism. Do not use world/group-readable copies to work around this check.

`deploy/ansible/01_install_shadow.yml` selects no hosts by default, requires `zabalack_install_opt_in=true` and a change reference, stages the supplied wheel and config, and copies an inactive unit. It creates a dedicated worker virtual environment and never installs or upgrades Alerta. Dependencies must match the reviewed lock/wheelhouse. Alerta plugin installation goes into each existing Alerta runtime environment using the same wheel with `--no-deps`; first verify that its compatible pinned runtime dependencies are already present. No active service is restarted by the install playbook.

## 2. Database preparation and separate trigger authority

Choose the actual `alerts` owner and the authorised role-provisioning identity from G1. `sql/install/00_roles.sql` is reviewed one-time role/schema provisioning, not a runtime command; it contains no passwords. Existing roles require explicit comparison instead of rerunning or auto-broadening them. Confirm `zabalack_owner` neither owns `alerts` nor inherits its owner role, and has only the documented underlying projection-column grants. Record under G1 which role owns `alerts` and whether the Alerta application connects as that owner: `sql/migrations/005_readiness.sql` grants `EXECUTE` on `zabalack.capture_ack()` and `zabalack.seal_installation()` to the observed `alerts` owner, so where the application runs as the owner (the upstream default) the runtime role can also re-seal the readiness manifest. Prefer a dedicated installation principal that owns `alerts` and a separate login role for the application; if that split is not possible, treat the application role as inside the trusted administrative boundary and audit `seal_installation` calls.

Drain or fence all eligible acknowledgement paths during readiness changes; invalidate every probe cache and keep delivery disabled. Zab-al-ack migrations run as `zabalack_owner`, separately from trigger administration:

```sh
# ZABALACK_MIGRATION_DSN contains a reviewed libpq service reference, not a literal password.
ZABALACK_MIGRATION_DSN='service=reviewed_zabalack_migration' python sql/migrate.py --set-role zabalack_owner
# A5 stage two: the Alerta application role's single entry point into zabalack, granted by the schema owner.
# alert_app_role is the role the application connects as (the alerts owner under the upstream default).
psql -X --no-password --set ON_ERROR_STOP=1 --dbname 'service=reviewed_zabalack_migration' \
  --set alert_app_role=alerta_app --command 'SET ROLE zabalack_owner' --file sql/install/12_application_grant.sql
# Run with the separately authorised alerts-owner/installer service. On a fresh install this seals the manifest;
# on an upgrade of an already-sealed installation run sql/install/13_reseal.sql as zabalack_owner instead.
psql -X --no-password --set ON_ERROR_STOP=1 --dbname 'service=reviewed_trigger_installer' --file sql/install/10_trigger.sql
```

`12_application_grant.sql` grants `USAGE` on `zabalack` and `EXECUTE` on `zabalack.stage_generation(text,uuid,jsonb)` to the application role and nothing else; the role holds no table privilege there and no other function. That is safe only while no function in the schema is executable by `PUBLIC` and the generation fence admits no grantee but its owner, both of which `capture_health()` asserts under `functions_ok`. The grant changes ACLs the readiness manifest hashes, so it precedes sealing. The application refuses an acknowledgement with `zabalack_generation_unavailable` (503) while the grant is absent, so a plugin deployed ahead of it fails closed rather than staging without generation protection.

The migration runner checks migration SHA-256 records; an applied checksum mismatch aborts instead of replacing definitions. `02_zabalack_migrations.yml` and `03_trigger_install.yml` are independently opt-in, select no hosts by default and do not start workers. Use role-specific test assertions after installation. A DBA/superuser provisioning success is not evidence for T35/T38/T39. A principal with only a TRIGGER grant is insufficient for physical disable/enable; use the actual table-owner authority for that separately controlled step.

Before unfencing, verify catalog attachment/mode, function identity/signatures, ownership/grants, metadata deployment identity, schema version and behavior under exact caller roles. Execute rollback/capture-failure branches in staging: PL/pgSQL branch validation cannot be proved by function creation alone. The health probe checks its own connection route; the application's committed canary capture establishes alignment with the separately pooled Alerta connection.

## 3. Shadow plugin rollout

The baseline installs all eight migrations into the fresh `zabalack` schema.
It includes the late-response fencing (006), settled-expiry (007) and generation-fence (008, A5 stage two) corrections.
Do not apply these files to an Ackline database. Historical Ackline a3→a4/a5
and a4/a5→a6 upgrade instructions are retained in the original a8 handoff;
they are not a9 transition instructions. See [RENAME.md](RENAME.md).

### Rolling installation

Keep `external_writes_enabled=false` in package config and the database write-disable policy. Load only reviewed pilot cohorts; never auto-populate them from incoming alert IDs/hosts. Enable capture only after its DB readiness checks pass. Configure the Alerta plugin via `ZABALACK_CONFIG_FILE` (JSON config path) in the existing application configuration. Entry points are `zabalack_zabbix_normalise` and `zabalack_zabbix_ack`.

Preserve existing plugins, authentication and routing. Add the normalizer at the reviewed inbound position, and ensure the signer is **last in every effective routed status chain**. Do not replace the entire `PLUGINS` list with an example. Audit notifications, observers, tracing and error handlers for precommit text/proof exposure. Unsupported paths remain local-only; do not patch repeat-Ack 409 into success.

Install the required application adapter after the application's logging setup and before serving requests. For an ordinary Gunicorn factory deployment, use the target `zabalack.plugins:create_app()` in the reviewed service command; it invokes Alerta's actual factory and then `configure_app(app)`. If a custom factory already exists, preserve it and wrap its completed application with `zabalack.plugins.configure_app(existing_app)`. Do not bypass existing config/auth initialization. Without this adapter an eligible acknowledgement fails closed with propagated 503. Source-backed amendment A1 in `SPEC_AMENDMENTS.md` documents the reference logging/signal exposure being closed. Review any logging handlers or instrumentation added after the adapter; unreviewed status signal receivers cause routing exclusion. The rollout must include real failed-save and precommit observer tests on every serving path.

Drain one API node at a time, install the reviewed package/config with a separate restart approval, verify hook routing and probe health, run synthetic capture/409/customer/unsupported-route checks, then return the node to service. Repeat for every serving node/site before permitting any source write. Validate identical configuration revision, key IDs, bypass state and writer routing across the fleet. Mixed bypass nodes remain drained.

Review `zabbix/v1/media_type.reference.json` against the real export in isolated Zabbix. Import the reviewed a9 artifact to obtain the new name and neutral-comment format. Ackline artifacts before 0.1.0a8 also require the absent-cause macro correction (`SPEC_AMENDMENTS.md` A3). It is disabled and is not a drop-in replacement. Preserve existing event/resource/correlation identity and matching actions. Gate G3 covers **all** update operations and all senders, including native email/command exclusions and action=2's no-comment case. Do not send canaries to real customer destinations.

Start the worker in shadow only after the reviewed systemd unit, credential references and schema checks are in place. `systemctl daemon-reload`, service start/enable and Alerta restarts are explicit separate operator actions; no install hook runs them. Synthetic cohorts/credentials remain lab-only. Review held work and projections; shadow operation can capture/verify evidence but never claim an API write succeeded.

## 4. Controlled enablement

Resolve source-backed amendments affecting external writes; complete and sign G1–G8 for the approved cohort. Run staging `deployment_acceptance.md`, including measured 50/200 per-alert batches, every API node, tenant negatives, update-operation effects, failover and PgBouncer. Validate the dedicated Zabbix token with authenticated exact-event reads and an explicitly authorised synthetic acknowledgement. `apiinfo.version` alone proves neither authentication nor tenant permissions.

Pause every configured instance, account for in-flight attempts, and verify both config and DB switches remain disabled until the change window. Provide a reviewed config with `external_writes_enabled=true`; this does not itself resume an instance. Use the operator CLI's explicit `enable-delivery` guard after reading its versioned `--help`, specifying instance, accountable owner and change reference. Enable one approved pilot instance; keep others paused. Record the exact reviewed config/cohort/policy hashes. No example or service restart invokes enable-delivery.

Run a synthetic end-to-end capture/delivery/projection canary within approved destinations; inspect write response evidence and neutral reference. Observe queue age, validation/write latency, API/DB/PHP load and one-incident-per-instance failure routing. Expand only by a new approved cohort revision and its tests. Concurrency defaults of two per logical instance are unmeasured starting values; no claim of production p95 follows from lab results.
