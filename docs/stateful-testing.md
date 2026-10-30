# Generated safety tests

`tests/property/test_trust_boundary.py` generates signed packets, mutates
authority-bearing fields, and checks exact capture identity and lifetime
boundaries. These tests exercise the real verifier without a database or a
network transport.

`tests/postgres/test_stateful_ledger.py` runs Hypothesis rule-based sequences
against the actual PostgreSQL ledger through `worker_call` and `operator_call`.
Every command asserts the restricted `current_user`. Each example starts from
one of four explicit regression prefixes: abandoned late response, settlement
followed by a separate retry, expiry followed by a new action generation, or a
completed effect followed by a new generation and an old callback. Generated
operations then mix new actions, delivery claims, sends, responses, lease loss,
worker shutdown, pause/resume, expiry, retries, settlement and reconciliation.

The model independently records frozen source identities, original deadlines,
which synthetic attempts remain unresolved, whether a separate retry has been
authorised, and which effect generations have completed. It compares those
facts with SQL observations after every rule. It does not compute its expected
states by calling SQL transition helpers. Every prefix also submits a bad-MAC
capture with the genuine action's identity before admitting the genuine one,
and rejects a mismatched verifier result through the SQL facade.

| Profile | Pure examples per case | SQL examples per focus | Generated steps per example |
|---|---:|---:|---:|
| `pr` (default) | 40 | 8 | 20 |
| `nightly` | 200 | 75 | 60 |

The PR profile uses fixed seed `20260911`. Nightly exploration varies by default;
set `ACK_HYPOTHESIS_SEED` to an integer to replay an explicit seed. Hypothesis
also saves and shrinks examples under `build/private/hypothesis/examples`.
Fixed-seed runs may bypass the example database; any failing SQL run additionally
saves its minimized printable sequence and replay metadata to
`build/private/hypothesis/ledger-<focus>-<profile>-failure.txt`. The directory is
private, ignored by Git, and must remain outside public source/evidence bundles.
Replay the indicated profile, seed and focus; for an unseeded failure reuse the
same private example database or apply the printed `@reproduce_failure` blob to
the state-machine factory. Preserve the pinned Hypothesis version with a corpus.

The file itself never leaves the runner. When a CI run fails, the job log
receives facts rebuilt from it by `tools/ledger_failure_facts.py`, an allowlist
drawn from the test module itself (owner decision, 30 October 2026). The native
job writes them into `evidence/native-failure-summary.json`
(`ledger_counterexamples`), which it also prints, and the reference job prints
them after a failed `postgres-wasm` phase. They are:
the focus and profile from the file name; header fields of known shape (seed,
profile, focus, engine, Hypothesis version, test and migration hashes, and
whether those hashes match the current tree); each minimized step whose rule,
keyword and value the state machine declares, rebuilt rather than copied; the
innermost frame in this test module (function and line); the exception class
from the native summary's allowlist; and the `@reproduce_failure` blob. Messages,
paths, other traceback text and the example database are not exported;
dropped lines and header fields are counted. A run killed before the test's
exception handler writes no file; the failed focus then still appears in the
native summary's pytest classification and in the public JUnit properties.

Run the short pure profile with:

```sh
python -m pytest tests/property -q
```

Run sequential PostgreSQL WASM exploration only in the isolated lab:

```sh
python lab/pglite_exec.py --isolated-lab -- python -m pytest tests/postgres/test_stateful_ledger.py -q
ACK_HYPOTHESIS_PROFILE=nightly ACK_HYPOTHESIS_SEED=20260912 python lab/pglite_exec.py --isolated-lab -- python -m pytest tests/postgres/test_stateful_ledger.py -q
```

`tools/run_property_nightly.py --isolated-lab` records the combined nightly
profile (pure tests plus the WASM ledger sequences, fixed seed) as
`evidence/property-nightly-run.json` with a redacted JUnit, bound to the tree.

In CI the `nightly` profile runs unseeded wherever the run was started by a
schedule: the daily `ci.yml` run and the ledger soak, `.github/workflows/soak.yml`,
which calls `ci.yml` every three hours (`41 */3 * * *`). A called workflow takes
its caller's event and name, so the soak gets the nightly profile and scheduled
evidence collection, and its concurrency group (`Ledger soak-<ref>`) never meets
the push or daily runs. The soak exists because unseeded exploration finds rare
interleavings by volume: the 28 and 29 October 2026 defects (R30, R31) each took
roughly ten unseeded passes to reach. It uses only the free standard
GitHub-hosted runners of this public repository; by owner decision (29 October
2026) no paid Actions usage is enabled, and if GitHub stops starting these jobs
the soak moves to the Forgehold laptop lane instead. A failing focus and its
minimized sequence reach the job log as allowlisted facts, as above; the raw
counterexample file and the example database remain on the runner.

The reference runner includes the pure tests in its unit phase and SQL sequences
in the PostgreSQL WASM phase. The native runner uses the same `pg_lab` fixture and
test file. A missing isolated PostgreSQL fixture is an explicit environment skip.
JUnit properties retain the observed engine, sequential scope, profile and
regression focus. WASM results never establish native authentication, concurrency,
PgBouncer, cancellation, failover or Zabbix execution.

Examples use a single rollback-isolated transaction. Owner access is limited to
synthetic queue isolation, SQL observation and advancing stored deadlines; all
runtime and operator transitions use their restricted callable interfaces.
Expected SQL rejections are caught inside PostgreSQL subtransactions under the
same restricted role. This permits the WASM adapter to exercise the rejection
without making a claim about its wire-protocol error recovery. Automatic prepared
statements are disabled on both backends: prepared-statement lifetime across the
WASM adapter's multiplexed client reconnections is outside this evidence. No test sleeps
through the four-hour execution lifetime or sends an external request. Native
multi-session and fault tests are separate evidence.

During harness development, the first WASM attempts failed while recovering an
expected SQL rejection across the socket adapter. Expected rejection checks now
remain inside restricted-role PostgreSQL subtransactions. A subsequent nightly
run completed three focus cases and failed the completed-generation fixture:
successive WASM clock reads gave the new action the same timestamp as the old
completion. The fixture now explicitly establishes strictly later issuance with
bounded database clock reads. That failure did not establish a product defect;
coalescing at equal timestamps is consistent with the contract. These failed
harness runs are distinct from subsequent successful profile runs and from
unexecuted native deployment gates.

The extended sequence also found a product gap in migration 006: a fenced
no-effect settlement made an unexpired action `failed` but left its effect open;
expiry cleanup did not include that particular failed disposition. After the
original deadline passed, a fresh explicit action remained `held` behind
generation 1. The independent direct SQL regression
`test_T9_T31_settled_no_effect_expiry_allows_fresh_explicit_action` reproduces
that sequence without consulting the generated model. Forward migration 007
releases only known settled no-effect work after expiry, retains every
unresolved-send and lease guard, and does not enqueue a delivery on settlement.
The final short SQL profile on 007 passed all five cases. The pure PR and nightly
profiles each passed all 41 cases. The final combined nightly run on 007 passed
46 cases, with zero failures or skips, using seed `20260912`; its before/after
source and harness digests match. `evidence/property-nightly-run.json` and the
redacted `evidence/property-nightly.xml` record that run. These generated tests
remain supplemental to native multi-session and operator deployment evidence.
