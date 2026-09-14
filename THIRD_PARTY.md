# Third-party material and dependency licensing

Project-owned code and documentation use Apache-2.0. This does not relicense
third-party dependencies or imply that their obligations have been discharged.

| Material | Origin | Handling |
|---|---|---|
| `lab/upstream/alerta-9.1.0-schema.sql` | Alerta 9.1.0, commit `f4312727783dad64198b62ed778bbca98a5144a3`, `alerta/sql/schema.sql` | Unmodified; Apache-2.0; retain `lab/upstream/LICENSE-Alerta` and NOTICE |
| PostgreSQL and PgBouncer lab software | Public pinned releases; see reference manifest and Dockerfile | Downloaded when explicitly building/running the lab; not copied into this source release |
| Alerta, Python packages and PGlite packages | Exact package locks | Installed as dependencies, not vendored project source; their own licences apply |
| Zabbix media script and synthetic fixtures | Written for this integration against inspected API/macro interfaces | Project-owned implementation; no Zabbix server source bundled |

`tools/dependency_inventory.py` records installed package metadata, dependency
relationships, SPDX expressions when declared and hashes of packaged licence
files. It also inventories the exact Node lock. Missing or ambiguous metadata
is reported for review, not silently assigned a licence. The generated CycloneDX
SBOM is an inventory, not a legal opinion or vulnerability clearance.

Before uploading a wheelhouse, container image or other third-party binary
bundle, review that distribution's complete notices/source obligations as a
separate deliverable. This alpha source release does not redistribute the
previous development wheelhouse. Confirm the publishing organisation's rights
in project contributions and the intended repository/package ownership before
first publication.
