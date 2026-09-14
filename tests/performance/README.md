# T30 lab batch harness

`run_batch.py` uses the real pinned Alerta routes: `PUT /alert/<full UUID>/action` with `{"action":"ack","text":"Isolated synthetic T30 batch"}` and authenticated human Bearer context. It polls `GET /alert/<UUID>` for projection. API keys are not human actors. Source: Alerta v9.1.0 `alerta/views/alerts.py` and `alerta/auth/decorators.py` inspected during implementation.

Supply an isolated synthetic population with 50 or 200 distinct alerts, each open and mapped to a fresh original Zabbix problem event in the lab cohort. A manifest has this shape (repeat rows to the selected exact count):

```json
{"environment":"isolated_synthetic_lab","alerts":[{"alert_id":"10000000-0000-4000-8000-000000000001","tenant":"lab-customer","instance":"lab-zabbix","source_event_id":"10001","already_acknowledged":false}]}
```

Set `ZABALACK_LAB_BEARER_TOKEN` through a protected lab environment mechanism. The runner refuses DNS names, non-loopback addresses, embedded URL credentials, redirects and environment proxies. It neither creates source events nor sends customer notifications. Start workers separately after lab-only write opt-in.

```sh
python tests/performance/run_batch.py --confirm-isolated-lab --base-url http://127.0.0.1:8080 --manifest /tmp/lab-batch-50.json --count 50 --output evidence/batch-50.json
python tests/performance/run_batch.py --confirm-isolated-lab --base-url http://127.0.0.1:8080 --manifest /tmp/lab-batch-200.json --count 200 --output evidence/batch-200.json
```

Repeat with already-acknowledged original events. Use multiple tenants/instances to measure progress and fairness. The JSON report records each synthetic tenant/instance without credentials, responses or alert text. It deliberately keeps full T30 `MANUAL_GATE_PENDING`: API/projection timings alone cannot measure PHP-FPM, database load or all deployment clauses. Collect the additional metrics listed in `docs/deployment_acceptance.md#t30`. The exit status indicates only whether the observed lab actions returned HTTP 200 and reached `ok`. Do not map it to full T30 acceptance.

## Executed supplemental reference batches

The supported development backend also has executable `test_reference_batches.py`. It creates 50 and 200 distinct source events/Alerta alerts, sends actual individual Alerta 9.1 HTTP actions, then runs cryptographic verification, the PostgreSQL-backed worker and projection with a mocked source client. Half of each population is already acknowledged and produces no write. It executes against the real PostgreSQL 18.3 engine compiled to WASM through the official PGlite socket adapter. API/probe authentication, source responses and concurrency are explicitly labelled: the run is sequential and the probe success is injected.

```sh
ACK_TEST_DESTRUCTIVE_LAB=YES_ISOLATED_LAB PYTHONPATH=src ../.venv/bin/python lab/pglite_exec.py --isolated-lab -- ../.venv/bin/python -m pytest tests/performance/test_reference_batches.py -q --junitxml=evidence/performance-supplemental.xml
```

Results are written to `evidence/batch-50-supplemental.json` and `evidence/batch-200-supplemental.json`. Both batches were executed successfully during this build. All 250 actions were captured, verified and projected `ok`; 125 mocked source writes and 125 already-acknowledged observations occurred. The reports retain full T30 as `MANUAL_GATE_PENDING` and list PHP-FPM, native database concurrency/load, PgBouncer, actual Zabbix permissions/notifications, cross-site failover and multi-tenant fairness as unmeasured.
