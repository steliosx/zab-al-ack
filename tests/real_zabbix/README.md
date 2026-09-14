# Optional real Zabbix 7.0.x contracts and end-to-end case

Executable suites: `test_contract.py` (API contract) and `test_end_to_end.py`
(full path). Follow `lab/real_zabbix.md` and `lab/real_zabbix_fixture.example.json`
for the manifest and opt-in variable names. Both fixtures validate the manifest
and the exact declared loopback API (`lab/real_zabbix_safety.py`) before any
HTTP client exists or a token file is opened, and require a correlated JSON-RPC
2.0 `apiinfo.version` response reporting 7.0.x. The contract suite then uses
real `event.get`/`host.get`, the outside-scope negative read (positive existence
and group association proved first through a separate read-only witness token;
NOT_RUN without one), and one separately enabled acknowledgement of a fresh
synthetic problem. The end-to-end case additionally needs the disposable
PostgreSQL lab (`ACK_TEST_ADMIN_DSN`) and a second fresh problem (`end_to_end`
in the fixture): a real Alerta HTTP acknowledgement is captured by the trigger,
verified and delivered by the production worker, client and endpoint registry
to the real Zabbix API, then read back from the ledger, the alert projection
and Zabbix.

Runs are recorded with `tools/record_real_zabbix.py --run`, which owns the
pytest execution, redacts the report, binds the record to the source/harness
digests of the tree that ran and refuses a report naming the fixture or a token
file. Consult `evidence/real-zabbix-run.json` for this candidate's actual status;
an unavailable fixture is NOT_RUN, never a pass. The supplied a13 archive records
six passes on its disposable Zabbix 7.0.10 instance, including the A4 excluded-action
denial and end-to-end case. That historical record is preserved in the complete
handoff's previous-release archive and is not rebound to a14. It does not
establish production permissions. Media macro expansion, update filtering and
recovery require separate deployment-path observations; inspect the current
`evidence/deployment-e2e-run.json` before claiming them. Neither suite ever
unacknowledges an event to reset itself; generate a new synthetic occurrence for
each run.
