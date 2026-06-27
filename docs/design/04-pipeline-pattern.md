# 04 — Pipeline Pattern (The Transform Contract)

> **Status:** Locked design
>
> Defines the single repeatable shape every Bronze→Silver→Gold transform follows, so a new entity is added by configuration plus a small, predictable amount of code — never by inventing a new pattern. This is the structural spine of the warehouse.

---

## 1. One pattern, applied everywhere

Every table in `01_bronze`, `02_silver`, and `03_gold` is produced by a **Lakeflow Declarative Pipeline (DLT)** flow that obeys the same contract:

1. **Declares** its target as a streaming table (incremental) or materialized view (full recompute), never imperative DML.
2. **Reads upstream incrementally** (`spark.readStream` for streaming tables) so only new data is processed.
3. **Carries lineage columns** end to end: `source_system`, `batch_id`, `ingested_at`, plus `effective_ts` for entities that feed SCD2.
4. **Applies data-quality expectations** declaratively (see `05-data-quality-spec.md`).
5. **Historizes** reference entities with `APPLY CHANGES INTO ... SCD TYPE 2` (see `01-scd2-strategy.md`).
6. **Emits run metrics** to the pipeline event log automatically; a thin wrapper summarizes them to `05_metadata` (see `06-observability-and-lineage-spec.md`).

If a transform cannot be expressed within this contract, that is a signal to revisit the design, not to bypass the pattern.

---

## 2. Layer responsibilities (what each flow may and may not do)

| Layer | Input | Allowed work | Forbidden |
|---|---|---|---|
| **Bronze** (`raw_*`) | Files in the landing Volume via Auto Loader | Faithful capture; add lineage columns; light typing only if free | Filtering, dedup, business logic, joins |
| **Silver** (`*`, `*_changes`) | Bronze streaming tables | Type-cast, DQ, dedup-latest, build CDC feed, SCD2 | Aggregation that belongs in Gold; cross-subject joins |
| **Gold dims** (`dim_*`) | Silver `*_changes` feeds | `APPLY CHANGES` (SCD2) or simple load (Type 1) | Measures, fact grain logic |
| **Gold facts** (`fact_*`) | Silver measures + Gold dims (for SK lookup) | Resolve surrogate keys, compute measures, append by (suburb, year) | In-place mutation of historical rows |
| **Reporting** (`vw_*`) | Gold | Curated, question-shaped views | New business logic not traceable to Gold |

A flow reaches **only one layer up**. Gold never reads Bronze; reporting never reads Silver. This keeps lineage legible and the medallion boundaries real.

---

## 3. The Bronze flow shape

```python
@dlt.table(
    name="raw_property",
    comment="Bronze: faithful capture of property source files.",
    table_properties={"quality": "bronze"},
)
def raw_property():
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "csv")
        .option("cloudFiles.schemaLocation", f"{CHECKPOINT}/raw_property")
        .option("cloudFiles.schemaEvolutionMode", "addNewColumns")
        .load(f"{LANDING}/property/")
        .withColumn("ingested_at", F.current_timestamp())
        .withColumn("source_file", F.col("_metadata.file_path"))
        # batch_id, source_system, effective_ts arrive as columns in the file
    )
```

Bronze never fails on content (only on unreadable files). Validation is Silver's job.

---

## 4. The Silver flow shape

Silver does four things in a fixed order, then either appends (measures) or emits a CDC feed (reference entities).

```python
# 4a. cleansed + DQ
@dlt.table(name="property", comment="Silver: typed, validated property.")
@dlt.expect_all_or_drop(DQ["property"]["warn"])     # WARN  → drop bad rows
@dlt.expect_all_or_fail(DQ["property"]["fatal"])    # FATAL → fail the run
def property():
    df = dlt.read_stream("raw_property")
    df = cast_to_schema(df, SCHEMA["property"])      # config-driven typing
    # sal_code is already on every row (stamped by the generator) — no key-matching step
    return dedup_latest(df, keys=["sal_code", "period"])
```

For SCD2 reference entities, Silver additionally builds the `*_changes` feed (one row per observed state, stamped with `effective_ts`) that `APPLY CHANGES` consumes. The two outputs — measure tables and `*_changes` feeds — are the only things Silver hands to Gold.

The helper functions (`cast_to_schema`, `dedup_latest`) are **shared, entity-agnostic**, and driven by `config/schemas/*.yaml`. Adding an entity adds a YAML schema and a DQ rule file, not new transform logic.

---

## 5. The Gold flow shape

**Dimensions** (SCD2) are pure `APPLY CHANGES` declarations (template in `01-scd2-strategy.md`). Type-1 dims are a trivial overwrite load.

**Facts** follow one shape: resolve surrogate keys against the dimensions valid at the row's period, compute measures, and upsert by the fact's business grain so re-runs replace rather than duplicate.

```python
@dlt.table(name="fact_suburb_property", comment="Gold: property facts, grain suburb×year.")
def fact_suburb_property():
    s = dlt.read("property")
    return (
        s.transform(resolve_suburb_sk)      # temporal SK lookup vs dim_suburb
         .transform(resolve_year_sk)
         .transform(compute_property_measures)
         .select(FACT_COLUMNS["property"])
    )
```

Surrogate-key resolution always uses the dimension version valid at the fact's `period_date` (the temporal predicate in `01-scd2-strategy.md` §7), so history binds correctly.

---

## 6. Configuration surface (what makes this reusable)

```
config/
 ├─ sources/<entity>.yaml      # connector, landing path, effective field, keys
 ├─ schemas/<entity>.yaml      # column → type, nullability
 ├─ dq_rules/<entity>.yaml     # rules with severity (WARN | FATAL)
 └─ pipeline/<env>.yaml        # catalog, paths, pipeline + job settings
```

The pipeline code reads these at build time. **Adding a new subject area** = add four YAML files + register the entity in the pipeline manifest. No bespoke per-entity transform code beyond declaring its table function from the shared helpers.

---

## 7. Streaming vs. materialized choice

| Use a **streaming table** when | Use a **materialized view** when |
|---|---|
| Source is append-mostly and incrementality matters (Bronze, Silver measures, CDC feeds) | Output is a full recompute over a small dimension or a derived rollup |
| SCD2 targets (required by `APPLY CHANGES`) | Reporting aggregates that must always reflect the full Gold state |

Default to streaming for the B→S→G path; reserve materialized views for `04_reporting` and small Type-1 dims.

---

## 8. The single invariant

> Every row in Gold can be traced — by `batch_id` and surrogate keys — back through Silver and Bronze to the exact landing file that produced it, and forward to the dimension version that was valid when it occurred.

If a change to a transform would break that statement, the change is wrong.

---

## 9. Adding a new entity (checklist)

1. `config/sources/<e>.yaml`, `schemas/<e>.yaml`, `dq_rules/<e>.yaml`.
2. Register `<e>` in the pipeline manifest.
3. Bronze `raw_<e>` (copy the shape; only the path/format differ).
4. Silver `<e>` (+ `<e>_changes` if SCD2).
5. Gold `dim_<e>` and/or `fact_suburb_<e>`.
6. Reporting `vw_*` if it answers a question.
7. Tests: schema, DQ fixtures, SCD2 sequence, no-op (see other specs).

---

## 10. Cross-references
- SCD2 declaration → `01-scd2-strategy.md`
- Incrementality & no-op → `02-incremental-loading-strategy.md`
- DQ expectations → `05-data-quality-spec.md`
- Run metrics & lineage → `06-observability-and-lineage-spec.md`
