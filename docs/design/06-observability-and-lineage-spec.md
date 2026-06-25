# 06 — Observability & Lineage Specification

> **Status:** Locked design
>
> Defines how every pipeline run is observable (run log, DQ metrics, event log) and how lineage — both *job lineage* and *data lineage* — is captured. The guiding choice: lean on **Unity Catalog system tables** and the **pipeline event log** for what the platform already records, and add a small business-level metadata schema only for what it doesn't.

---

## 1. Two questions this must answer

1. **Operational:** *Did the last run succeed? What did it process? Where did it fail?*
2. **Lineage:** *Where did this Gold number come from, and what depends on it if it changes?*

The platform answers most of (2) natively; we add a thin layer for (1) and for a business-friendly summary.

---

## 2. What the platform captures for free

| Signal | Source | Use |
|---|---|---|
| **Job runs** (start, end, status, duration, task graph) | `system.lakeflow.jobs`, `system.lakeflow.job_run_timeline` | Job lineage; run history; SLA tracking |
| **Pipeline events** (flow progress, row counts, expectation metrics) | DLT **event log** | Per-table throughput; DQ results |
| **Table lineage** (which table feeds which) | `system.access.table_lineage` | Upstream/downstream impact analysis |
| **Column lineage** | `system.access.column_lineage` | Trace a measure to its source columns |
| **Access audit** | `system.access.audit` | Who ran/queried what |

We do **not** re-implement any of these. The metadata schema below references and summarizes them; it is not a parallel source of truth.

---

## 3. The metadata schema (`05_metadata`)

Three small, durable tables plus health views. These hold the *business-level* run summary and the things system tables don't track (e.g. `batch_id` provenance, source registry).

### `pipeline_run_log`
One row per pipeline/job run.
```
pipeline_run_log(
  run_id STRING,            -- our id, also the Lakeflow job run id
  pipeline_name STRING, env STRING,
  status STRING,           -- RUNNING | SUCCESS | NO_OP | FAILED
  trigger STRING,          -- scheduled | manual | backfill
  batch_ids ARRAY<STRING>, -- batches consumed this run
  rows_read BIGINT, rows_written BIGINT,
  started_at TIMESTAMP, ended_at TIMESTAMP,
  error_message STRING, error_class STRING)
```

### `dq_results`
Per-rule outcomes, materialized from the DLT event log (schema in `05-data-quality-spec.md` §8).

### `source_registry`
Provenance of every extraction.
```
source_registry(
  batch_id STRING, source_system STRING, entity STRING,
  endpoint STRING, resource_id STRING, licence STRING,
  rows_extracted BIGINT, retrieved_at TIMESTAMP, is_synthetic BOOLEAN)
```

### Health views
- `vw_pipeline_health` — latest run per pipeline with status, rows, and any failing DQ rule.
- `vw_run_history` — runs over time for trend/SLA.
- `vw_lineage_for_table(table)` — convenience wrapper over the system lineage tables.

---

## 4. How a run is logged (the wrapper contract)

DLT itself is declarative and doesn't write business log rows. A thin **orchestration task** in the Lakeflow Job brackets the pipeline:

1. **Pre-task** — insert `pipeline_run_log` row: `status=RUNNING`, `trigger`, `run_id` (= job run id).
2. **Pipeline** — runs B→S→G; emits event-log metrics natively.
3. **Post-task** — read the event log for this update; compute `rows_read/written`; set `status` to `SUCCESS`, `NO_OP` (zero new rows everywhere), or `FAILED`; materialize `dq_results`.

`run_id` equals the Lakeflow job run id, so `pipeline_run_log` joins directly to `system.lakeflow.job_run_timeline` — our summary and the platform's job lineage share a key.

---

## 5. Cross-layer traceability (the `batch_id` thread)

`batch_id` is minted at extraction, written into landing files, and carried through every layer onto every Gold fact row. Combined with surrogate keys and `source_registry`, any output is traceable to its origin:

```sql
-- Gold number → source extraction
SELECT sr.*
FROM   gold.fact_suburb_property f
JOIN   metadata.source_registry sr USING (batch_id)
WHERE  f.suburb_sk = :sk AND f.year_sk = :yk;

-- Impact analysis: what breaks if dim_suburb changes?
SELECT * FROM system.access.table_lineage
WHERE  source_table_full_name = 'vic_suburbs_prod.03_gold.dim_suburb';
```

---

## 6. Alerting

Two complementary mechanisms, both native:

1. **Job-level** — Lakeflow Job notifications on failure (and on success/duration-SLA if desired) to email / webhook (Slack, PagerDuty). Catches hard failures.
2. **Data-level** — a scheduled **SQL alert** over `vw_pipeline_health`: fire if the latest run is `FAILED`, or any FATAL DQ rule tripped, or a WARN rule's pass-rate dropped below threshold, or a run hasn't landed within its expected window (freshness).

Alert routing/thresholds live in `config/pipeline/<env>.yaml` so dev stays quiet and prod is loud.

---

## 7. What "NO_OP" looks like in observability

A no-op run still logs a row: `status=NO_OP`, `rows_written=0`, DQ results empty. This makes idempotent re-runs visible and auditable rather than silent — and an unexpected `NO_OP` (when data was expected) is itself an alertable condition.

---

## 8. Retention & cost

- `pipeline_run_log`, `dq_results`, `source_registry` are small, append-mostly Delta tables; retain indefinitely (cheap, valuable history).
- System tables have their own platform-managed retention; health views read recent windows to stay cheap.
- The DLT event log is queried per-run by the post-task, not scanned broadly.

---

## 9. Testing observability

- **Run-log lifecycle:** assert a successful run writes exactly one row transitioning RUNNING→SUCCESS with correct counts.
- **Failure path:** force a FATAL DQ failure; assert `status=FAILED` with `error_class`/`error_message` populated and an alert fired (dev webhook).
- **No-op:** re-run with no new data; assert one `NO_OP` row, zero writes.
- **Lineage presence:** after a full run, assert `table_lineage` resolves Gold→Silver→Bronze for a sample fact.

---

## 10. Cross-references
- DQ metrics feeding `dq_results` → `05-data-quality-spec.md`
- `batch_id` minting → `02-incremental-loading-strategy.md` §3
- RBAC on `05_metadata` → `07-deployment-and-rbac-spec.md`
