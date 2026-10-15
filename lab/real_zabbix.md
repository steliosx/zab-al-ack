# Optional real Zabbix contract suite

The fresh reference server is defined in `compose.zabbix.yml`. Without Docker,
the same lab can be assembled by hand: build `zabbix_server` from the upstream
7.0.x source tarball (`--enable-server --with-postgresql --with-libpcre2`), load
`database/postgresql/{schema,images,data}.sql` into a dedicated PostgreSQL
cluster, serve `ui/` with PHP 8.x on `127.0.0.1:58080`, and run the server as an
unprivileged user with loopback-only listeners. The suite accepts any 7.0.x
patch release and records the observed version; the reference pin is 7.0.25.
No mocked result is permission, macro or notification evidence.

Start the explicit real-zabbix profile on an authorised development host. The
network is internal; the UI/API exposes `127.0.0.1:58080` only. Confirm this is a
fresh disposable instance, with no active external notification media/actions.
Use only synthetic default lab credentials; do not use a real account/token.

Prepare a monitored synthetic host in a dedicated host group, a trapper item and
a single-host trigger, then submit a problem value to generate an original
problem event. Keep that problem unacknowledged. For the negative test, create a
second synthetic host/group/event outside the restricted lab user's access.
Grant the lab user only the required source methods (`event.get`,
`event.acknowledge`, `host.get`; `apiinfo.version` needs no authentication) and
the acknowledge/comment actions; token creation remains an explicit lab operator
action. **Enumerate every action and UI rule explicitly and read the effective
role back** before creating the user or its token: on Zabbix 7.0 an omitted rule
keeps its enabled default, so `actions.default_access: 0` with a short `actions`
list leaves suppression, close, severity change, script execution and API-token
management enabled (amendment A4). `lab/zabbix_roles.py` builds the correct rule
set and `verify_role` checks the `role.get` result — the same helpers the
deployment lab uses:

```python
from lab.zabbix_roles import role_rules, verify_role
worker = role_rules()                                             # event.get/acknowledge/host.get + acknowledge/comment
witness = role_rules(methods=("event.get", "host.get"), actions=())  # read-only witness, no enabled action
```

Note that Zabbix maps acknowledgement and unacknowledgement to one role
capability: a token that may acknowledge may also unacknowledge, and only the
product client refuses action 16. Treat the lab token accordingly (A4).
`tests/real_zabbix/test_contract.py` proves the boundary on the real server by
requiring the specific upstream suppression-permission denial; a generic
"referred object" rejection comes from host rights and is not accepted. A trapper item plus `history.push` is the simplest way to raise the
synthetic problem; wait for the server's configuration cache to pick up a newly
created item before pushing. Record
actual host/trigger/problem IDs from this instance; examples do not claim IDs.

Copy `real_zabbix_fixture.example.json` to a private lab fixture and fill the
actual synthetic IDs. For the end-to-end case (`test_end_to_end.py`) prepare a
second trapper item and single-host trigger on the same in-scope host, raise a
separate problem on it and record both IDs under `end_to_end`; that case also
needs the disposable PostgreSQL lab variables from `README.md`. Set `isolated_lab` and `fresh_instance_no_notifications`
true only after checking them. Put its synthetic API token in a mode-0600 file;
store its path in `token_file`. For the scoped negative check (T11a), fill
`outside_scope_source` with `zabbix_event_id`, `group_ids` and
`witness_token_file`: a second, read-only lab user (API access limited to
`event.get` and `host.get`, every action rule explicitly disabled, read permission
on both the in-scope and the outside host group) whose mode-0600 token lets the
test prove first that the outside event exists, belongs to the outside group
and not to the cohort group, before it requires the restricted token's read of
that same event to come back empty. Without the witness the test reports
NOT_RUN: an empty read or a generic request rejection alone does not prove a
permission boundary. The witness token is never used for a write.

Every fixture check happens before any HTTP client or token file is touched;
the suites accept exactly `http://127.0.0.1:58080/api_jsonrpc.php`
(`lab/real_zabbix_safety.py`) and reject other hosts, ports, paths, userinfo,
query strings and fragments.

```bash
export ACK_TEST_REAL_ZABBIX_LAB=YES_ISOLATED_LAB
export ACK_TEST_REAL_ZABBIX_FIXTURE=/path/to/synthetic-fixture.json
python -m pytest tests/real_zabbix -ra
```

The read tests perform real version, exact event, approved-host and optional
negative visibility calls through the implementation client. They never infer
the latest event. To exercise one neutral-comment acknowledgement of the
explicit synthetic problem:

```bash
export ACK_TEST_REAL_ZABBIX_WRITE=ACKNOWLEDGE_SYNTHETIC_LAB_EVENT
export ACK_TEST_ADMIN_DSN='postgresql://lab_admin@127.0.0.1:55432/alerta'   # end-to-end case only
export ACK_TEST_DESTRUCTIVE_LAB=YES_ISOLATED_LAB
python -m pytest tests/real_zabbix -ra
```

The write test skips an already-acknowledged fixture; it never unacknowledges to
reset a test. Generate a new synthetic occurrence for another run. For recorded
evidence, let the recorder own the pytest execution. It selects only
`tests/real_zabbix`, enforces a 300-second deadline (adjustable with `--timeout`,
up to 1800 seconds), snapshots source/harness digests before launching pytest,
and refuses a tree change during the run. `--junit` imports are no longer
supported: an arbitrary report cannot establish which source produced it.

```bash
python tools/record_real_zabbix.py --run
python tools/record_real_zabbix.py --not-run "private operator explanation"  # when unavailable
```

The fixture comes from `ACK_TEST_REAL_ZABBIX_FIXTURE` or `--fixture` and must
pass the literal-loopback guard before token files are opened. The recorder
checks both the restricted and optional witness token paths and file contents
for leaks automatically; `--forbid TEXT` and `--forbid-file PATH` add further
private material to reject. Missing or unreadable inputs fail before execution.
Old public evidence is invalidated first. Fresh raw JUnit and console output
remain in a unique private `build/evidence-raw/real-zabbix-*/` attempt directory,
and sanitized XML is published only
after leak checks and tree/exit-status checks. The JSON record contains current
counts and observed API versions from this execution; it inherits no previous
claims or descriptions. Failed/skipped tests remain failed/skipped, and a
nonzero pytest exit cannot become PASS. A NOT_RUN record exports only a fixed
unavailability reason; the supplied private explanation is not retained.

Each executed attempt retains `run.xml`, `console.log`, any staged `public.xml`
and a private `manifest.json` with lifecycle times, the actual pytest command,
exit status when available, before/after source and harness digests, and hashes
of the available diagnostic files. Attempt directories are mode 0700 and files
mode 0600 even with a permissive caller umask. Symlinked storage, traversal and
linked diagnostic files are rejected; existing ancestor permissions are never
changed. The public XML is a separate atomic 0644 copy, so publication cannot
remove or broaden access to its private staging copy.

Success, assertion failure, timeout, interruption and leak rejection all keep
their raw records. An abruptly killed recorder or failed manifest write can
leave a RUNNING manifest; that is unfinished, regardless of any passing XML.
FINISHED records the retained execution result; it does not confirm public
publication. The manifest starts with `published: false`, and a final PUBLISHED
checkpoint sets it true only after both the public XML and JSON are written.
An interruption before that checkpoint leaves publication unconfirmed, even
when the retained execution status is PASS. PUBLISHED confirms export of the
recorded result, which can also be FAIL; it does not turn failures into passes.
Older manifests without this field likewise do not establish publication.
Public publication errors invalidate the current evidence while retaining the
attempt. Preflight refusals (for example an invalid timeout or missing lab
opt-in) also retain an attempt, without a child command or raw execution files;
these directories do not establish that tests ran.
No attempt overwrites an earlier one. Back up these private directories
before discarding a checkout, verify the archived bytes and retain failed and
successful attempts separately. They can contain synthetic tokens, fixture paths
and raw exceptions: never commit them or upload them to public GitHub records.
Retained pytest output does not confirm service cleanup or close deployment
gates; independently inspect the owned lab before any later execution.

`tools/update_acceptance.py` maps the record only when its digests match the
tree it is invoked on; edit a test, runner or lab file and the run must be
repeated before the acceptance matrix can be rebuilt.
The suite verifies its fixture's API contracts. Actual macro expansion, all
customer permissions, update-operation side effects, HA and deployment G1–G8
still need the full environment-specific acceptance procedures.
