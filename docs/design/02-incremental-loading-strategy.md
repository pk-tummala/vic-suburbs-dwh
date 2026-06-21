# 02 — Incremental Loading, Idempotency & Statefulness

> **Status:** Locked design
>
> Defines how new data is loaded *incrementally* and *idempotently* using native Databricks state (Auto Loader checkpoints + DLT), what "no-op" means here, how `batch_id` gives end-to-end traceability, the effective-time vs. ingestion-time distinction, and how backfills/reprocessing work.

---

## 1. Native state, not a watermark table

Incrementality is driven by state the platform already manages transactionally, rather than a hand-maintained watermark table. We use native mechanisms throughout:

| Incrementality concern | Native mechanism | Replaces |
|---|---|---|
| "Which source files are new?" | **Auto Loader (`cloudFiles`) checkpoint** | manual high-water file list |
| "Which source rows are new since last run?" | **DLT streaming table** (append-only incremental processing) | `loaded_at > watermark` filter |
| "Don't double-process" | checkpoint offsets + Delta transaction log | manual dedup |
| SCD2 change detection | `APPLY CHANGES` (idempotent on CDC source) | dual-MERGE |

We keep a metadata table (`05_metadata.pipeline_run_log`) **only for business-level observability** — it records what a run did; it does **not** drive whether the run processes a row. See `00-overview-and-architecture.md` §3.

---

## 2. The two clocks: effective time vs. ingestion time

This is the most important rule in the project. Every record carries two temporal stamps with different jobs:

| Stamp | Meaning | Drives |
|---|---|---|
| `effective_ts` / `period` | *When the fact was true in the real world* (census 2021, FY2023 crime, Q4-2025 property) | SCD2 sequencing, fact grain, "what was true then" |
| `ingested_at` (UTC) | *When we loaded it* | incremental processing, audit, reprocessing |

Confusing the two is the classic bug: ordering SCD2 by ingestion time means a late-loaded *old* census would wrongly appear as the newest version. We **always** `SEQUENCE BY effective_ts`, never by `ingested_at`.

---

## 3. `batch_id` — end-to-end traceability within one platform

Every extraction run mints one `batch_id` (UUID). It is written into the landing file(s), preserved verbatim through Bronze → Silver → Gold, and stored on every fact row. Given any output number on the dashboard, an operator can:

```sql
-- From a Gold fact row back to its source
SELECT batch_id, source_system, ingested_at, source_file
FROM   gold.fact_suburb_property
WHERE  suburb_sk = :sk AND year_sk = :yk;

-- Then find the run that produced it
SELECT * FROM metadata.pipeline_run_log WHERE batch_id = :batch_id;
```

Traceability runs as a single thread within the platform: the carried `batch_id` plus **Unity Catalog system-table lineage** (`system.access.table_lineage`, `system.access.column_lineage`, `system.lakeflow.job_run_timeline`) provide automatic job and data lineage.

---

## 4. Ingestion: Auto Loader

Bronze tables are streaming tables fed by Auto Loader from the landing Volume:

```python
@dlt.table(name="raw_property")
def raw_property():
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "csv")
        .option("cloudFiles.schemaLocation", f"{CHECKPOINT}/raw_property")
        .option("cloudFiles.schemaEvolutionMode", "addNewColumns")
        .load(f"{LANDING}/property/")
        .withColumn("ingested_at", F.current_timestamp())
        .withColumn("source_file", F.col("_metadata.file_path"))
        # batch_id & source_system arrive as columns in the file
    )
```

- The **schemaLocation checkpoint is the watermark** — Auto Loader will never re-read a file it has already committed, even across pipeline restarts.
- `schemaEvolutionMode=addNewColumns` lets a source add a column without breaking the run (new census topics, new DataVic fields).
- Re-dropping the *same* file is a no-op (already committed); a genuinely new file is picked up on the next trigger.

---

## 5. Silver & Gold: DLT incrementalization

- **Silver** reads Bronze as a stream → only new Bronze rows are processed. DQ expectations run here (see DQ spec). The `*_changes` CDC feed for SCD2 is produced here.
- **Gold dims** via `APPLY CHANGES` (incremental + idempotent by construction).
- **Gold facts** are appended by (suburb, period). To stay idempotent on re-run, fact loads use **`MERGE` keyed on (`suburb_sk`,`year_sk`,`source_system`)** so re-processing a period replaces rather than duplicates — or, where the source is strictly append-only, rely on the upstream streaming table's exactly-once guarantee.

---

## 6. What "no-op" means here

A pipeline run is a **no-op** when no new files have landed and no new source rows exist. Concretely, after a no-op run:

- Auto Loader commits no new offsets.
- Silver streams process 0 rows.
- `APPLY CHANGES` opens 0 new dimension versions.
- 0 new fact rows.
- `pipeline_run_log` records the run with `status='NO_OP'`, `rows_written=0`.

No-op-ness is an **explicitly asserted test**, not an assumption (run the pipeline twice with no new data; assert the second run wrote nothing). This is the property that proves the design is genuinely idempotent rather than merely "usually fine."

---

## 7. Backfill & reprocessing

Two supported operations, both first-class:

1. **Backfill a historical period** — drop the period's source files into landing with their correct `effective_ts`. Auto Loader ingests them; SCD2 sequences them into the *right* historical position (because we order by effective, not ingestion, time). No special path needed.
2. **Full reprocess of an entity** — DLT supports a **full refresh** of selected tables (clears checkpoint/state and rebuilds). This is the controlled "rebuild from Bronze" lever, gated to operators, documented in the reprocessing runbook. Bronze itself can be rebuilt from the immutable landing Volume.

Because effective time anchors history, late-arriving old data lands in the correct place rather than as spurious "latest" versions.

---

## 8. Idempotency checklist (acceptance)

A layer is idempotent iff, for the same input set, running it N times equals running it once:
- [ ] Re-running with no new files → `NO_OP`, zero writes.
- [ ] Re-running after a mid-pipeline failure → converges, no duplicates (Delta transactionality + checkpoints).
- [ ] Re-dropping an already-ingested file → ignored.
- [ ] Re-loading the same period into a fact → replaced via MERGE key, not duplicated.
- [ ] SCD2 unchanged rows → zero new versions.

---

## 9. Cross-references
- SCD2 sequencing → `01-scd2-strategy.md` §6–8
- `batch_id` minting at extraction → `03-data-sourcing-and-synthetic-universe.md`
- Run-log schema & lineage → observability spec (next doc set)
