# PostgreSQL tuning benchmark, Zabbix 7.0.25 to Alerta lab

Status: exploratory study, one run per row, published at the owner's request on 26 October 2026.
Numbers are derived figures from the operator's benchmark harness records; full per-row records are retained
privately and were not independently re-derived. The wording of this page was reviewed by the Codex agent under
a bounded document-only review. Nothing here is a tuning recommendation.

## 1. What was run

One guest per attempt (SmolVM, Zabbix 7.0.25 reference at Zabalack commit 7f78a3a7, Alerta, PostgreSQL, PgBouncer).
Every row: restore the sealed snapshot, apply ONE setting (or none for a control), warm up, barrier, ingest 48 000 simulated
Zabbix alerts (12 000 measured identities after 500 warm-up identities; closed-loop, 4 serial writer threads, no rate cap; ~41 alerts/s achieved in every control), then the service-desk persona (count/topn/list/detail/status_ack
/bulk ack/event.get, and set A = 100 Zabalack acknowledgements written back to Zabbix). One run per row; exploratory.
The benchmark harness is not part of this repository.

| attempt | partition | rows | date | result |
|---|---|---|---|---|
| A1  | A         | 9 | 20 Oct 2026 | PASSED 9/9, 6 RANKED |
| B2  | B (first) | 9 | 20 Oct 2026 | rows 0-6 PASSED, row 7 FAILED in persona (harness), row 8 NOT_RUN |
| BT1 | B tail    | 5 | 20 Oct 2026 | PASSED 5/5, 2 RANKED + 1 TRADEOFF_CLASS |
| C1  | C         | 7 | 20 Oct 2026 | PASSED 7/7, 4 RANKED |
| R1  | R (repeat) | 10 | 26 Oct 2026 | PASSED 10/10, 6 RANKED |

R (repeat) re-ran, at their exact original values and classes, the six treatments that carried a material improving
flag in A1, B2 or C1, in blocks of two treatments closed by a control (four controls). It is an independent attempt, ranked
only against its own controls; its purpose was to see whether the single-run screens repeat under their own brackets.

An earlier attempt at partition B (B1) passed its first two rows and failed at the start of the first PgBouncer row because
of a harness ordering defect, fixed before B2. B1's two passed rows are retained but not reported here; B2 re-ran the whole
partition and supersedes them.

## 2. Method note (same harness code path in every attempt)

- Each treatment is compared with the median of its two NEAREST passed controls (one before, one after). Rows without a
  control on both sides are UNBRACKETED/NOT_RANKABLE.
- control_spread per metric = max - min over ALL passed controls of that attempt (one difference with 2 controls, a range
  with 3). Spread is therefore not equally strict across attempts.
- A metric is flagged "material" only when the change is in the improving direction AND |t - ref| > 5 % of ref AND
  |t - ref| > control_spread. (The 5 % floor is why e.g. B2 random_page_cost event_exact -4.3 % was not flagged although it
  exceeded B2's tiny 0.16 ms spread.) Ranking order = number of material flags, then ingest delta.
- Autovacuum rows and maintenance_work_mem are RANKABLE only if autovacuum/autoanalyze activity was observed in the
  measured window (pg_stat_user_tables counters reset at the barrier). It was, in every such row. measurement_qualified=true
  for every passed row in all five attempts.
- Persona probe sizes are the same in every attempt: detail 100 requests per row; list, count,
  status_ack and event_exact 10 requests per row (the harness marks these medians small_sample). Every list/status_ack/
  event_exact median cell in sections 3-4, in every attempt, is therefore a 10-request median; a flag on one of them is
  a screen on a small sample, in A1/B2/C1 exactly as in R1.
- Durability class (synchronous_commit) is TRADEOFF_CLASS: reported, never ranked.
- Brackets are never merged across attempts. Control baselines drifted across the day (ingest ~41.8-41.9 in A1, ~41.6 in
  B2, ~41.58 in BT1, ~41.04-41.27 in C1 alerts/s) and R1, six days later, ran its controls at 42.02-42.39 alerts/s, the
  highest of the study. Host CPU temperature varied within and between attempts (informational reads only; the launcher's
  thermal admission and sustained-heat guard were never changed and never tripped). Both are unverified possible contributors to the drift; they are the concrete reason
  only within-attempt deltas are compared, and why R1 is compared with the earlier attempts by FLAG outcome only (section
  4a), never by value.
- control_spread is attempt-dependent (bullet 2), so the same delta can be flagged in one attempt and not in another: B2's
  event_exact spread was 0.16 ms (2 controls), R1's 4.9 ms (4 controls). Section 4a reports direction and size beside the
  flag outcome for that reason.
- A1 and B2 ran an earlier harness revision whose persona wait set used invented action states (fixed before BT1). That
  defect could only produce a false STOP, never a false PASS, so every PASSED row remains a valid observation. The B2 row 7
  failure is source-consistent with that harness defect; exclusivity is not proven.

## 3. Per-attempt rankings (treatment vs bracket median; "+" means improving, "-" means worsening)

### A1 (3 controls; spread: ingest 0.14/s, detail p95 5.2 ms, list median 1.9 ms, status_ack median 2.9 ms)
| rank | row (setting) | ingest/s t vs b | detail p95 ms | list median ms | status_ack median ms | material |
|---|---|---|---|---|---|---|
| 1 | effective_cache_size = 3GB | 41.66 vs 41.90 | 52.0 vs 59.2 (+12.1 %) | 52.0 vs 56.3 (+7.6 %) | 56.7 vs 52.2 (-8.6 %) | detail_p95, list_median |
| 2 | max_wal_size = 4GB | 41.89 vs 41.83 | 49.7 vs 57.5 (+13.7 %) | 58.2 vs 55.3 (-5.2 %) | 56.5 vs 53.6 (-5.3 %) | detail_p95 |
| 3 | shared_buffers = 1536MB | 41.74 vs 41.90 | 51.8 vs 59.2 (+12.5 %) | 54.6 vs 56.3 | 57.4 vs 52.2 (-10.0 %) | detail_p95 |
| 4 | wal_buffers = 64MB | 41.87 vs 41.83 | 54.1 vs 57.5 | 65.0 vs 55.3 (-17.4 %) | 55.1 vs 53.6 | - |
| 5 | work_mem = 16MB | 41.80 vs 41.83 | 54.5 vs 57.5 | 53.8 vs 55.3 | 58.4 vs 53.6 (-8.9 %) | - |
| 6 | jit = off | 41.80 vs 41.90 | 56.5 vs 59.2 | 62.8 vs 56.3 (-11.5 %) | 57.5 vs 52.2 (-10.2 %) | - |

### B2 first block (2 controls; spread: ingest 0.03/s, detail p95 1.6 ms, list median 1.6 ms, event_exact 0.16 ms)
| rank | row | ingest/s t vs b | detail p95 ms | list median ms | event_exact median ms | material |
|---|---|---|---|---|---|---|
| 1 | autovacuum_analyze_scale_factor = 0.01 (activity 118) | 41.42 vs 41.63 | 58.3 vs 51.3 (-13.5 %) | 54.7 vs 55.1 | 31.2 vs 35.1 (+11.1 %) | event_exact_median |
| 2 | pgbouncer max_client_conn = 200 | 41.63 vs 41.63 | 52.3 vs 51.3 | 53.0 vs 55.1 | 37.1 vs 35.1 (-5.9 %) | - |
| 3 | random_page_cost = 1.1 | 41.54 vs 41.63 | 51.8 vs 51.3 | 54.1 vs 55.1 | 33.6 vs 35.1 (+4.3 %, under the 5 % floor) | - |
| - | maintenance_work_mem = 512MB | PASSED, UNBRACKETED in B2 (closing control not run); ranked in BT1 |
| - | pgbouncer default_pool_size = 20 | PASSED, UNBRACKETED in B2; ranked in BT1 |
| - | synchronous_commit = off | FAILED in persona (harness wait set); ingest-only observation 66.38/s, p50 55.1, p95 92.5 ms; unranked class anyway |
| - | b-c02-control | NOT_RUN (series stopped) |

### BT1 (2 controls; spread: ingest 0.03/s, detail p95 2.8 ms, list median 2.3 ms, status_ack median 0.3 ms)
| rank | row | ingest/s t vs b | detail p95 ms | list median ms | status_ack median ms | material |
|---|---|---|---|---|---|---|
| 1 | pgbouncer default_pool_size = 20 | 41.40 vs 41.58 | 54.1 vs 51.0 (-6.1 %) | 57.7 vs 53.3 (-8.3 %) | 59.1 vs 52.0 (-13.6 %) | - |
| 2 | maintenance_work_mem = 512MB (activity 117) | 41.29 vs 41.58 | 55.6 vs 51.0 (-9.2 %) | 61.0 vs 53.3 (-14.5 %) | 55.9 vs 52.0 (-7.6 %) | - |
| TRADEOFF_CLASS | synchronous_commit = off (never ranked) | 66.52 vs 41.58 (+60 %) ; p50 54.7 vs 90.3 ms ; p95 91.9 vs 140.8 ms | 44.9 | 47.4 | n/a | reported only |

### C1 (3 controls; spread: ingest 0.23/s, detail p95 6.5 ms, list median 6.0 ms, status_ack median 3.4 ms)
| rank | row | ingest/s t vs b | detail p95 ms | list median ms | status_ack median ms | material |
|---|---|---|---|---|---|---|
| 1 | autovacuum_vacuum_scale_factor = 0.02 (activity 129) | 41.33 vs 41.15 | 52.8 vs 53.8 | 64.6 vs 57.7 (-11.9 %) | 52.2 vs 56.1 (+7.0 %) | status_ack_median (3.9 ms vs 3.4 ms spread: near threshold) |
| 2 | checkpoint_timeout = 15min | 41.27 vs 41.15 | 53.3 vs 53.8 | 59.8 vs 57.7 | 52.3 vs 56.1 (+6.8 %) | status_ack_median (3.8 ms vs 3.4 ms spread: near threshold) |
| 3 | effective_io_concurrency = 200 | 41.27 vs 41.09 | 53.7 vs 50.6 (-6.1 %) | 55.5 vs 55.9 | 54.4 vs 55.8 | - |
| 4 | autovacuum_naptime = 15s (activity 309, ~2.5x the others) | 41.13 vs 41.15 | 55.8 vs 53.8 | 53.9 vs 57.7 (+6.7 %) | 58.4 vs 56.1 (-4.1 %) | - |

### R1, repeat (4 controls; spread: ingest 0.37/s, detail p95 4.4 ms, list median 5.5 ms, status_ack median 4.2 ms, event_exact median 4.9 ms)
Controls (ingest alerts/s; list median ms): c00 42.02; 58.9 | c01 42.31; 57.2 | c02 42.25; 59.3 | c03 42.39; 62.7. Blocks: rows 1-2 bracketed by c00/c01, rows 4-5 by c01/c02, rows 7-8 by c02/c03.
| rank | row (original flag, attempt) | ingest/s t vs b | detail p95 ms | list median ms | status_ack median ms | event_exact median ms | material (R1) |
|---|---|---|---|---|---|---|---|
| 1 | checkpoint_timeout = 15min (status_ack, C1; activity 122) | 42.39 vs 42.32 | 54.4 vs 55.3 | 51.0 vs 61.0 (+16.4 %) | 53.5 vs 53.4 | 49.4 vs 33.7 (-46.5 %) | list_median |
| 2 | autovacuum_analyze_scale_factor = 0.01 (event_exact, B2; activity 127) | 42.13 vs 42.28 | 53.0 vs 53.8 | 52.5 vs 58.2 (+10.0 %) | 59.5 vs 51.3 (-15.9 %) | 31.9 vs 35.4 (+9.8 %, 3.5 ms against a 4.9 ms spread) | list_median |
| 3 | autovacuum_vacuum_scale_factor = 0.02 (status_ack, C1; activity 134) | 42.47 vs 42.32 | 57.4 vs 55.3 | 61.8 vs 61.0 | 59.5 vs 53.4 (-11.4 %) | 35.0 vs 33.7 | - |
| 4 | shared_buffers = 1536MB (detail_p95, A1) | 42.34 vs 42.28 | 52.7 vs 53.8 | 58.8 vs 58.2 | 54.6 vs 51.3 (-6.3 %) | 41.8 vs 35.4 (-18.2 %) | - |
| 5 | max_wal_size = 4GB (detail_p95, A1) | 42.19 vs 42.17 | 55.7 vs 51.7 (-7.9 %) | 57.0 vs 58.1 | 54.5 vs 52.1 | 43.7 vs 37.4 (-16.6 %) | - |
| 6 | effective_cache_size = 3GB (detail_p95 + list_median, A1) | 41.90 vs 42.17 (-0.6 %) | 56.3 vs 51.7 (-9.0 %) | 55.0 vs 58.1 (+5.3 %, 3.1 ms against a 5.5 ms spread) | 52.3 vs 52.1 | 40.7 vs 37.4 (-8.6 %) | - |

R1 notes: ingest 41.90-42.47 alerts/s over the ten rows, no ingest flag; effective_cache_size declined 0.27/s against its
bracket, under both the 0.37/s control spread and the 5 % floor, so R1 does not repeat A1's beyond-spread decline (reported
movement, not a flag in either direction). The checkpoint_timeout row's event_exact median
(49.4 vs 33.7 ms) is the largest single-run worsening of a persona metric in the study. The closing control c03 carries the
highest list median of the series (62.7 ms) and sits in the checkpoint_timeout bracket.

## 4. Cross-attempt view by class (within-attempt deltas only; no absolute values compared across attempts)

| class | setting (attempt) | ingest material flag | other material flags | reading (single-run screen; no flag is not proof of no effect) |
|---|---|---|---|---|
| setting | effective_cache_size 3GB (A1) | none (-0.6 %, a decline above the 0.14/s spread but under the 5 % floor) | detail p95 +12 %, list median +8 % | strongest read-latency screen; status_ack worsened 8.6 % in the same row |
| setting | max_wal_size 4GB (A1) | none | detail p95 +14 % | list median and status_ack worsened ~5 % |
| setting | shared_buffers 1536MB (A1) | none | detail p95 +12 % | status_ack worsened 10 % |
| setting | checkpoint_timeout 15min (C1) | none | status_ack median +6.8 % (marginal) | 3.8 ms gain against 3.4 ms spread |
| setting | wal_buffers, work_mem, jit=off (A1); random_page_cost (B2); maintenance_work_mem (BT1); effective_io_concurrency (C1) | none (maintenance_work_mem -0.7 %, a decline above BT1's 0.03/s spread) | - | no material improving flag under this screen; several rows worsened a persona metric |
| pooling | pgbouncer max_client_conn 200 (B2), default_pool_size 20 (BT1) | none | - | no material improving flag; default_pool_size 20 worsened status_ack 13.6 % in its single run |
| autovacuum | autovacuum_analyze_scale_factor 0.01 (B2) | none (-0.5 %) | event_exact median +11 % | detail p95 worsened 13.5 % in the same row |
| autovacuum | autovacuum_vacuum_scale_factor 0.02 (C1) | none | status_ack median +7 % (marginal) | list median worsened 12 % |
| autovacuum | autovacuum_naptime 15s (C1) | none | - | 2.5x autovacuum activity, no material improving flag on any metric |
| durability (never ranked) | synchronous_commit off (BT1; B2 ingest-only) | +60 % ingest (66.5 vs 41.6/s), p95 92 vs 141 ms; two attempts agree | n/a | the only large contrast in the study, consistent with a commit/durability contribution (mechanism not isolated); trades crash durability of the last commits |

### 4a. Repeat outcome: original flag vs R1 on the same metric (flag outcomes compared; values and brackets never merged across attempts)

| setting | original flag (attempt; delta vs that attempt's spread) | R1, same metric (delta vs R1 spread) | R1 flag on that metric | R1 new flag |
|---|---|---|---|---|
| effective_cache_size 3GB | detail_p95 +12.1 % and list_median +7.6 % (A1; 7.2 ms vs 5.2, 4.3 ms vs 1.9) | detail_p95 -9.0 % (worsened, 4.6 ms vs 4.4); list_median +5.3 % (3.1 ms vs 5.5, under the spread) | no (neither) | none |
| max_wal_size 4GB | detail_p95 +13.7 % (A1; 7.8 ms vs 5.2) | detail_p95 -7.9 % (worsened, 4.0 ms vs 4.4) | no | none |
| shared_buffers 1536MB | detail_p95 +12.5 % (A1; 7.4 ms vs 5.2) | detail_p95 +2.0 % (1.1 ms vs 4.4, under the 5 % floor and the spread) | no | none |
| autovacuum_analyze_scale_factor 0.01 | event_exact_median +11.1 % (B2; 3.9 ms vs 0.16) | event_exact_median +9.8 % (3.5 ms vs 4.9, under the spread) | no: same direction and near the same size, not flagged because R1's spread on this metric is 30x B2's | list_median +10.0 % (5.8 ms vs 5.5) |
| checkpoint_timeout 15min | status_ack_median +6.8 % (C1; 3.8 ms vs 3.4, marginal) | status_ack_median -0.3 % (0.1 ms) | no | list_median +16.4 % (10.0 ms vs 5.5) |
| autovacuum_vacuum_scale_factor 0.02 | status_ack_median +7.0 % (C1; 3.9 ms vs 3.4, marginal) | status_ack_median -11.4 % (worsened, 6.1 ms vs 4.2) | no | none |

Reading of 4a (screen outcomes, not effect claims): none of the six original flags produced a material improving flag on
its own metric in R1. Four of the six show a negative R1 delta on the originally flagged metric: three larger reported
worsenings (effective_cache_size detail_p95 -9.0 %, max_wal_size detail_p95 -7.9 %, autovacuum_vacuum_scale_factor
status_ack -11.4 %) and checkpoint_timeout's near-zero status_ack delta (-0.3 %, about 0.1 ms), which is reported movement,
not a demonstrated worsening. shared_buffers kept the improving direction at +2.0 %, under the 5 % floor and the spread.
One, autovacuum_analyze_scale_factor on event_exact_median, repeated in direction and
size (+11.1 % then +9.8 %) and fell short of the flag only on the attempt-dependent spread test. Two rows carry a new
material flag in R1 on a metric that was not flagged originally (list_median, a 10-request median in every attempt);
each is a single-run screen of the same kind as the originals, with no repeat of its own, and is reported exactly as the
originals were: a flag under the screen, not a demonstrated improvement.

## 5. Conclusion (writer's, exploratory single runs)

1. No rankable non-durability treatment showed a MATERIAL improving ingest effect under the stated within-attempt
   screening rule, in any of the five attempts. Some treatments declined by more than their attempt's control spread (A1 effective_cache_size -0.24/s
   against 0.14/s; BT1 maintenance_work_mem -0.29/s against 0.03/s; R1 effective_cache_size -0.27/s is under its 0.37/s spread and is not one of them); those are not improving effects and are under the
   5 % floor, but they are not zero movement. The ingest phase is closed-loop (48 000 alerts over 4 serial writer threads,
   no pacing or rate cap), which fixes the workload topology, not
   the bottleneck. Achieved throughput and end-to-end request latency co-varied in these runs (controls
   ~1164 s at p50 ~91 ms, 41.3/s; synchronous_commit=off 723 s at p50 55 ms, 66.5/s); the large synchronous_commit=off
   contrast is consistent with a commit/durability contribution, but does not isolate the bottleneck or establish an
   exclusive causal mechanism.
2. The latency flags that exist are single-run exploratory screens, not causal recommendations. Their size relative to
   the control spread varies widely (B2 event_exact 3.9 ms against a 0.16 ms spread; C1 status_ack 3.8-3.9 ms against
   3.4 ms) and neither ratio is statistical significance: effective_cache_size 3GB (detail p95,
   list median), max_wal_size 4GB and shared_buffers 1536MB (detail p95), autovacuum_analyze_scale_factor 0.01
   (event_exact median), checkpoint_timeout 15min and autovacuum_vacuum_scale_factor 0.02 (status_ack median, marginal).
   Each of those rows also worsened at least one other persona metric in the same run. The repeat attempt R1 (26 Oct,
   section 4a) re-ran all six under their own brackets: none produced a material improving flag on its original metric;
   four showed a negative delta on that metric (three larger reported worsenings and checkpoint_timeout's near-zero
   -0.3 %, reported movement, not a demonstrated worsening); shared_buffers kept its direction under the floor;
   autovacuum_analyze_scale_factor 0.01 repeated
   in direction and size on event_exact_median (+11.1 % then +9.8 %) and missed the flag only on R1's wider spread. R1
   produced two new single-run flags on list_median (checkpoint_timeout +16.4 %, analyze_scale_factor +10.0 %), on a
   metric not flagged originally, with no repeat of their own. Under this screen, no setting in the study carries a
   material improving flag that repeated on the same metric across two attempts; none is a recommendation.
3. synchronous_commit=off is the only setting with a large, repeated effect (ingest +60 %, p95 -35 %, in two separate
   guests). It is a durability trade-off and is reported outside the ranking by design.
4. Autovacuum rows were qualified by observed activity in every case; naptime=15s produced ~2.5x the activity with no
   material improving flag under this screen (absence of a flag is not equivalence or proof of no effect).

## 6. Records

Full per-row records (summaries, row logs, durations, persona and ingest measurements, settings readbacks) for all five
attempts and the superseded B1 attempt are retained privately by the operator. Only derived figures appear on this page.
