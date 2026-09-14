# Mutation testing

Mutation checks ask whether the tests reject a deliberately unsafe implementation.
They are a required bounded CI gate and a larger scheduled check. They currently
cover reviewed Python trust, policy, worker and RPC decisions using synthetic
unit tests and mocked database/source interfaces. They do not measure the entire
codebase or establish SQL lease authority, real service behaviour, tenant
permissions, native process races or G1–G8.

Use the reference virtual environment and run:

```sh
python tools/run_mutation_checks.py --profile pr
python tools/run_mutation_checks.py --profile nightly
```

The PR profile has 12 reviewed mutants. Nightly runs those plus eight further
route, source-ID, cohort, RPC write-scope, acknowledgement and cancellation cases.
Both use the same selected unit/mock tests. The default per-process limit is
90 seconds; `--timeout` accepts 5–300 seconds. A surviving mutant fails the gate.
There is no arbitrary percentage threshold or survivor allowlist.

The catalogue is executable and reviewable in `tools/mutation_catalog.py`.
Each entry states the source file, exact single substitution, independent safety
invariant and applicable acceptance IDs. Every substitution must match exactly
once and compile. Source refactors that invalidate an anchor require a review of
the mutant and its test; an invalid anchor is not a successful kill.

## How a run is protected

1. Write a new `RUNNING` report before preparing or launching any child, invalidating
   earlier successful evidence. Bind the run to current source and harness digests.
2. Copy only the required source, test, tool and configuration inputs into a private
   temporary tree. Source symlinks are rejected. The original checkout is never
   mutated, and all source imports are checked to resolve within that copy.
3. Pass the unchanged selected test suite first, with no errors or skips. Inherited
   pytest/lab overrides are removed and plugin autoload is disabled. Only the pinned
   asyncio plugin and the mutation observation plugin are loaded explicitly.
4. Apply one compile-checked mutation, rerun the same tests, restore that private
   file in `finally`, and then proceed to the next mutant. A killed child process
   group and a deleted private tree prevent a timeout or interruption from leaving
   mutated source behind.
5. Fail unless every selected mutant is killed by an assertion and the source and
   harness remain unchanged. A fresh report includes baseline and mutant counts,
   outcomes and the complete collected-case digest. The CI/export validator also
   requires the exact selected catalogue, matching case inventory and integer
   counts. Skipped, missing, extra or duplicated cases cannot improve the result.

The pinned pytest 9.0.3 source was inspected before implementing its hooks:
`_pytest/hookspec.py` defines `pytest_collection_modifyitems`,
`pytest_runtest_makereport` and `pytest_sessionfinish`; `_pytest/junitxml.py` writes
failure messages without an exception-type attribute. The observer therefore
uses the actual exception object in the call report. Only `AssertionError` and
pytest's explicit `DID NOT RAISE` expectation failure count as kills. Arbitrary
`pytest.fail`, import/setup/teardown failures, other runtime exceptions and
collection failures are errors. The selected case digest uses test file,
original function name and parameter indices so synthetic random UUID parameter
labels cannot disguise a missing case or invalidate an otherwise identical run.
JUnit and the observer's independent case/count summary must agree.

| Outcome | Meaning | Gate |
|---|---|---|
| `KILLED` | Matching baseline inventory; at least one assertion detects the mutation | Required for every selected mutant |
| `SURVIVED` | All selected tests still pass | Fail; inspect the missing invariant or equivalent mutation |
| `INVALID` | Anchor missing/ambiguous, unchanged mutation or syntax failure | Fail; review catalogue against source |
| `ERROR` | Test/runtime/import/collection error, skip or inconsistent evidence | Fail; repair the harness/test infrastructure |
| `TIMEOUT` | Child exceeded its bound | Fail; do not treat a hang as a kill |
| `NOT_RUN` | Baseline/preparation/interruption prevented execution | Fail; rerun after correction |

Only bounded IDs, counts, timing and digests are published in
`evidence/mutation-run.json`. Detailed pytest output and JUnit remain under
`build/private/mutation/run-*`, with private directory permissions. They may
contain synthetic signing proofs and must not be uploaded as public artifacts.
The disposable source tree is removed even on failure. The report is consistency
evidence; cryptographic release provenance is handled separately.

## What the first run improved

The initial expanded run killed 19 of 20 mutants. Removing the exact event/trigger
comparison from the mandatory pre-read survived: existing tests checked the
requested event ID and host membership, but did not assert that a valid-shaped
response naming a different event or trigger was rejected. Two independent
worker regressions now exercise those responses and require no attempt or write.
This was a test gap, not a change to the production validation code.

Additional guard tests cover unknown-outcome delivery, a missing durable attempt
identity, and a human-authorised cohort that is not one-to-one. The runner itself
has executable tests for false kills, stale/incomplete evidence, source-copy
cleanup and the actual pinned pytest callback, including runtime/import failures,
skips and a wrong-source import. Native and SQL mutation coverage remain future
work and must use disposable real databases with separate truthful evidence.
