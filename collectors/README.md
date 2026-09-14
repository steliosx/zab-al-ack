# Read-only deployment evidence

These commands are generated for an authorised engineer. They were not run against any live system. They do not certify G1–G4. Each command selects its input or endpoint explicitly; there is no inventory discovery, default production destination or application configuration import. Output uses `O_EXCL`, refuses symlink inputs, is bounded, and is created with mode 0600. Errors report classes without DSNs or API content.

Use the existing package virtual environment; do not install anything globally. `local` uses AST literal parsing, Python/package metadata and no Alerta module import. Computed settings and effective routing are marked unverified. `postgres` requires Psycopg and uses a reviewed libpq service entry/credential file; it runs catalog-only queries in a read-only transaction, does not select alerts, API keys or capture evidence, and always rolls back. The `zabbix` collector accepts only `apiinfo.version`, `action.get`, `mediatype.get`, `trigger.get`, and `host.get`; object selections must be explicit. It never invokes an acknowledgement, script, export mutation, login mutation, role update or notification.

```sh
python collectors/collect.py --output /secure-review/g1-local.json local --config /reviewed/path/alertad.conf
python collectors/collect.py --output /secure-review/g1-database.json postgres --service reviewed_inventory --role zabalack_owner --role zabalack_worker --role zabalack_probe --role zabalack_operator
python collectors/collect.py --output /secure-review/g2-g4-zabbix.json zabbix --endpoint https://REVIEWED-HOST/api_jsonrpc.php --ca-file /reviewed/path/ca.pem --token-file /secure-review/readonly.token --action-id 123 --media-type-id 456 --trigger-id 789
python collectors/collect.py --output /secure-review/g2-problem.json payload --input /secure-review/problem.json
```

Replace numeric examples with explicitly selected IDs; no calls are made by reading these instructions. Use a separate read-only inventory token. The delivery token does not need configuration-read API methods added to it. `apiinfo.version` is unauthenticated version evidence only. Its success does not validate token rights.

The media inventory hashes scripts and redacts literal parameter values. Action summaries retain operation kinds, media IDs, counts and whether commands/custom messages exist; recipients, names, command bodies, messages and scripts are omitted. A restricted reviewer must still inspect the live export to prove every matching action and update sender is covered. A redacted summary cannot prove a suppression branch runs before side effects. The collector deliberately does not enumerate every private action automatically.

The static local collector does not read ingestion-key tables: provide a separately reviewed mapping of opaque credential references to customer references and scope lists, never keys. Supply signed-off plugin route traces, PgBouncer `SHOW POOLS/CLIENTS/SERVERS` summaries (without connection identities), UI revision hashes, service/process counts and correlation rules separately. PostgreSQL catalog function options and role names can contain internal labels: keep output restricted and review it before sharing.

Validate envelopes with `schemas/inventory.schema.json`; record the human conclusion with `schemas/reviewed_inputs.schema.json`. A recorded approval is evidence, not an automatic delivery-enablement command. Configuration imports must also pass the package config validator. See `docs/INPUTS_REQUIRED.md` for the single consolidated input register.

Source contract: Zabbix 7.0.0 commit `49955f1fb5c9168a8a24b053f7ade6b3d903143c`, `ui/include/classes/api/services/CMediatype.php` (`parameters` is an output field; there is no `selectParameters`) and `CAction.php` (separate operation selectors). The 7.0.0 media API returns an empty array for an ordinary user lacking its administrative read permission. Empty results are therefore not proof that selected objects are absent or that permissions pass. Use an approved separate inventory identity and keep delivery-token permissions narrow. Source paths/hashes are recorded in the reference manifest; the [7.0 mediatype.get documentation](https://www.zabbix.com/documentation/7.0/en/manual/api/reference/mediatype/get) is separately version-labelled documentation, not an installed-version assertion.
