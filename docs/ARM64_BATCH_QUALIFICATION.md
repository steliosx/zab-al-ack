# Native ARM64 batch observations — 18 September 2026

The four documented 50/200-alert populations passed on the unchanged installed
`0.1.0a16` wheel: **500 actions reached `ok`; 250 fresh-event writes and matching
reference comments; zero additional writes/comments for 250 already-acknowledged
events**. All 500 source events were distinct, and every fresh effect was bound
to its ledger public reference. API errors and HTTP 409 responses were zero in
the successful run. This is supplemental local evidence, not full T30/G6,
deployment acceptance, Actions execution or native x64 qualification.

## Identity and environment

- PR #24 head/tested local checkout:
  `7ae972e8488ab9602c60a4895a98e1204d534207`; base
  `013fbc6f99c171cc484cbbaafcec4de418b4dfe7`.
- Qualified installed wheel SHA-256:
  `1f072f0443a2499cecb27d45ffcee7401c3d5c22b5dd7c3ae5be627ff8463774`.
  No wheel/image rebuild, pull, runtime edit, dependency change or SQL change.
  Embedded helper, lockfile and all SQL bytes were checked against the candidate;
  installed Python bytes were checked against the specified wheel.
- The candidate's unmodified `tests/performance/run_batch.py` was invoked with
  count 50/200, API concurrency 8, protected human token reference, loopback
  HTTPS and projection timeout 600 seconds. A separately retained private
  controller generated fixtures, observed source/ledger state and collected
  metrics. It is supplemental harness code, not a changed candidate harness.
- Native Linux ARM64, two vCPUs, 3.81 GiB RAM; Python 3.12.10, PostgreSQL 18.3,
  pinned Alerta 9.1.0 and Zabbix 7.0.10 with its pinned PostgreSQL 16 image.
  Existing cached ARM64 images were used, with no emulation. There was no
  PgBouncer in this existing deployment-path stack.
- Fresh isolated services, one customer/instance/cohort, no enabled notification
  actions or user media, no published ports. Ordinary human login and scoped
  ingestion credentials; installed application/worker processes and native SQL
  roles. Alerts were directly ingested as synthetic fixtures. Previously retained
  media/macro qualification is separate and was not repeated here.

## Successful warmed-fixture results

All times below are seconds, rounded; populations used fresh, distinct events.
The source ID allocator was initialized serially as disclosed below.

| Population | API p50/p95/max | Capture-to-source-completion p50/p95/max | Observed projection p50/p95/max | Product writes |
|---|---|---|---|---:|
| 50 fresh | 0.056 / 0.145 / 0.151 | 6.000 / 8.168 / 8.337 | 6.421 / 8.387 / 8.404 | 50 |
| 50 already acknowledged | 0.054 / 0.080 / 0.084 | 3.132 / 4.412 / 4.510 | 3.592 / 4.756 / 4.791 | 0 |
| 200 fresh | 0.059 / 0.078 / 0.083 | 14.458 / 23.577 / 24.486 | 16.297 / 23.880 / 24.748 | 200 |
| 200 already acknowledged | 0.061 / 0.088 / 0.104 | 10.353 / 15.104 / 15.679 | 12.208 / 15.482 / 15.981 | 0 |

Capture-to-completion is the stored capture timestamp to ledger effect completion;
it includes queueing and is not an exact transaction-commit-to-source timestamp.
Projection observations include polling delay. Write-attempt-to-completion p95
was 0.106 seconds for 50 fresh and 0.138 for 200 fresh; that interval is not the
complete validation-plus-write duration. These batch results do not establish
the design's single-alert delivery target.

Database samples every approximately 0.5 seconds covered every population and a
five-second baseline before each. Active delivery slots sampled at most two in
all batches. Largest sampled pending ages were 8.069, 4.201, 24.312 and 15.325
seconds respectively. Largest observed nonterminal action depths were 50, 34,
133 and 104. A single waiting lock appeared in the 200-fresh samples; other
batches sampled none. Database deadlock and temporary-byte counters stayed zero.
Sampling does not prove uninterrupted lease or lock bounds.

Thirty-five host/container samples had no collector errors. Observed CPU peaks
were PostgreSQL 97.69%, Zabbix DB 40.08%, Zabbix web 65.96%, Zabbix server 1.96%
and measurement/application/worker container 89.90% (Docker percentage, where
100% represents one CPU). Minimum sampled host available RAM was about 2.62 GiB.
Raw activity/wait categories, queue/outbox states, DB counters, memory and load
samples are retained privately. Service CPU is not PHP-FPM queue measurement.

Unmeasured: PHP-FPM status/queue/max-children, PgBouncer pools, exact validation
start/commit-to-source timing, multiple-tenant/site fairness, failover and
production topology. Flask's development server served this contained lab.
Configured limits and all required deployment observations still need review.
Full T30 remains `MANUAL_GATE_PENDING`.

## Original attempts and open source finding

1. `batch-01`: setup failed before batch execution because the supplemental
   wrapper omitted the existing runner's read-only synthetic password-file mode
   for the pinned non-root Zabbix images. Replay matched the candidate's existing
   setup under its owner-only parent; the candidate was not changed.
2. `batch-02`: the completely fresh Zabbix database rejected one of its first
   concurrent acknowledgements. Its log records an `ids_pkey` collision for
   `(acknowledges, acknowledgeid)` during initial counter insertion. One product
   write succeeded, one action failed, and the circuit held 48. Read-only event
   and host visibility succeeded. The stalled poll was deliberately interrupted
   with SIGINT, preserving diagnostics/collector callbacks; no circuit reset or
   blind source-write retry was attempted.
3. `batch-03`: after explicit serialized counter initialization, a warm-up and
   first batch sample shared a timestamp. Only 49 new events appeared; the
   cardinality guard timed out before batch actions. The next generator revision
   used distinct nanosecond values. This incomplete attempt remains a failure.
4. `batch-04`: all four fresh populations passed after one explicitly recorded,
   serialized synthetic source acknowledgement. That warm-up event is excluded
   from the 500 batch events and ledger rows. No event was unacknowledged/reset
   or reused to manufacture a successful replay.

The cold-start source finding is **not closed by the warmed pass**. Qualification
of a brand-new installed source needs a disposition for its first concurrent
acknowledgements and appropriate review. The proposed installed-account canary
procedure exercises a permitted acknowledgement serially, but that is not proof
the upstream race has been fixed. No runtime retry, concurrency limit or source
database correction was introduced. This finding and the fixture corrections do
not require relabeling or repeating the already completed broad candidate suites.

## Durable private evidence

All raw application/source/database logs, fixture records, controller versions,
failed/interrupted attempts, metrics and cleanup records are retained separately
in the existing private Linux and Windows stores. Archive hashes and every file
were verified on both hosts; no bundled command was executed during receipt.
All four attempts stopped their services and removed only their verified owned
empty network. Stopped containers/volumes remain retained; all other resources
were checked unchanged.

| Attempt | Archive SHA-256 | Files | Bytes | Attempt outcome |
|---|---|---:|---:|---|
| 01 | `dc1578d0697121904409891ca778fa5afaaade1143473f307fcae1a45c79ee48` | 56 | 316076 | setup failed |
| 02 | `88edf1bac58e8fa661736f3698850ff0f0126a2d7521db81829e296822809c37` | 84 | 461072 | source failure; interrupted poll |
| 03 | `e9ad8460ba6f3e5598d6f7faf6fdbda4e66398e7286657e5db48725a9130c3a8` | 81 | 455776 | fixture cardinality failed |
| 04 | `201f60f266d109dadb5fd762cf93b22ab7bf0a8ebe35b1a740095bbda401b533` | 105 | 585639 | batch/ledger observations passed |

Own execution validates implementation. Separate Codex scrutiny of the
supplemental controller/documentation is bounded technical review, not formal
GitHub approval or an application-security review. Native x64 remains deferred.
See [ledger assessment](RETAINED_LEDGER_ASSESSMENT.md),
[installed-account procedure](INSTALLED_ACCOUNT_QUALIFICATION.md) and
[remaining beta requirements](BETA_READINESS.md).
