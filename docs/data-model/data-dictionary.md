# Data Dictionary

> **Status:** Locked design
>
> Table- and column-level reference for every layer. Types are indicative (resolved precisely in
> `config/schemas/*.yaml`). Lineage columns common to many tables are defined once in §1 and not
> repeated.

---

## 1. Conventions & common columns

- Naming: `snake_case`; dims `dim_*`, facts `fact_suburb_*`, reporting `vw_*`.
- Suburb key: **`sal_code`** (State Suburb code). Surrogate keys: `<dim>_sk` (a hash of the business
  key and the version start, `xxhash64(business_key, __START_AT)`).
- Timestamps stored **UTC**; the display layer renders Australia/Melbourne.

**Lineage / audit columns** (present where noted):

| Column | Type | Meaning |
|---|---|---|
| `source_system` | string | Always `SYNTHETIC` in this project |
| `batch_id` | string | UUID minted by the generator; traceable end to end |
| `ingested_at` | timestamp | UTC load time (drives incrementality) |
| `effective_ts` | timestamp | When the row was true in the modelled world (drives SCD2 / grain) |
| `source_file` | string | Landing file path (Bronze) |
| `valid_from` / `valid_to` | timestamp | SCD2 validity; current row has `valid_to IS NULL` |
| `is_current` | boolean | SCD2 current-version flag |
| `created_by_run_id` / `closed_by_run_id` | string | Run that opened/closed an SCD2 version |

---

## 2. Bronze layer (`01_bronze`) — raw, append-only

One `raw_<entity>` per entity. Schema mirrors the landing file plus lineage columns; no typing
guarantees beyond what Auto Loader infers.

| Table | Holds | Key lineage cols |
|---|---|---|
| `raw_demographics` | synthetic demographics files | `source_system, batch_id, ingested_at, source_file` |
| `raw_property` | synthetic property files | same |
| `raw_crime` | synthetic crime files | same |
| `raw_transport` | synthetic transport files | same |
| `raw_education` | synthetic education files | same |
| `raw_suburb_ref` / `raw_lga_ref` | suburb / LGA reference files | same + `effective_ts` |

---

## 3. Silver layer (`02_silver`) — typed, validated

Two kinds of object: **measure tables** (append, dedup-latest) and **`*_changes` CDC feeds** (drive
Gold SCD2).

### `demographics`, `property`, `crime`, `transport`, `education`
Typed measure tables. All carry `sal_code`, `period`, the entity's typed measures, plus lineage
columns. Rows failing a FATAL DQ rule never appear; WARN-failed rows are dropped (and quarantined).

### `suburb_changes`, `lga_changes`
CDC feeds: one row per observed state of the key, ordered by `effective_ts`, consumed by
`APPLY CHANGES`.

| `suburb_changes` column | Type | Meaning |
|---|---|---|
| `sal_code` | string | Business key |
| `suburb_name` | string | Tracked (versioned) |
| `postcode` | string | Tracked |
| `lga_code` | string | Tracked |
| `region` | string | Greater Melbourne \| Regional Victoria (tracked) |
| `asgs_edition` | string | Geography edition (tracked) |
| `area_sqkm` | double | Carried, not versioned |
| `effective_ts` | timestamp | Sequence key |

---

## 4. Gold layer (`03_gold`) — Kimball fact constellation

### Shared dimensions

**`dim_suburb`** (SCD2)
| Column | Type | Notes |
|---|---|---|
| `suburb_sk` | bigint | PK (surrogate) |
| `sal_code` | string | Business key |
| `suburb_name`, `postcode`, `region`, `asgs_edition`, `lga_code` | string | Versioned attributes |
| `area_sqkm` | double | Carried, not versioned |
| `valid_from`, `valid_to`, `is_current` | — | SCD2 |

**`dim_lga`** (SCD2): `lga_sk` PK, `lga_code` BK, `lga_name`, `lga_type`, SCD2 cols.

**`dim_year`** (Type 1): `year_sk` PK, `year` (bk), plus calendar helper columns.

### Facts (all carry: `suburb_sk`, `year_sk`, `source_system`, `batch_id`, `ingested_at`, `effective_ts`, `gold_loaded_at`)

**`fact_suburb_demographics`** → Q1
`population_total, median_age, pop_0_14, pop_15_24, pop_25_44, pop_45_64, pop_65_plus, median_household_income_weekly`

**`fact_suburb_property`** → Q5, Q6
`median_house_price, median_unit_price, median_rent_weekly, sales_volume`

**`fact_suburb_crime`** → Q3
`offence_count_total`

**`fact_suburb_transport`** → Q2
`train_station_count, tram_stop_count, bus_stop_count, train_freq_peak, tram_freq_peak, bus_freq_peak, train_coverage, tram_coverage, bus_coverage`

**`fact_suburb_education`** → Q4
`govt_school_count, mean_icsea`

> Facts are insert/append by (`suburb_sk`, `year_sk`). A corrected period is appended as a new row,
> never edited in place. Derived metrics (growth %, a connectivity score) are computed in the
> reporting views, not stored on the facts.

---

## 5. Reporting layer (`04_reporting`) — question-shaped views

| View | Answers | Shape |
|---|---|---|
| `vw_q1_population_growth` | Q1 | demographics ⋈ suburb ⋈ year, population trend |
| `vw_q2_transport_connectivity` | Q2 | transport ⋈ suburb, ranked by a derived `connectivity_index` |
| `vw_q3_low_crime` | Q3 | crime ⋈ suburb, ranked by `offence_count_total` ascending |
| `vw_q4_top_schools` | Q4 | education ⋈ suburb, `govt_school_count` vs `mean_icsea` |
| `vw_q5_affordable_growth` | Q5 | property: current median price paired with a growth % |
| `vw_q6_most_expensive` | Q6 | property ranked by `median_house_price` descending |

The reporting views also format display-friendly columns (e.g. `*_fmt`) for the dashboard.

---

## 6. Metadata layer (`05_metadata`) — observability

| Table / view | Purpose |
|---|---|
| `pipeline_run_log` | One row per run: status, trigger, batch_ids, row counts, error |
| `dq_results` | Per-rule pass/fail counts per run |
| `dq_quarantine` | WARN-dropped rows with the failing rule names |
| `source_registry` | Provenance per `batch_id`: source_system, row counts, generated-at |
| `vw_pipeline_health` | Latest run per pipeline + any failing DQ rule |

Full column lists for these are in `design/06-observability-and-lineage-spec.md` and
`design/05-data-quality-spec.md`.

---

## 7. Cross-references
- Model rationale & grain → `data-model.md`
- SCD2 columns → `design/01-scd2-strategy.md`
- DQ rules → `design/05-data-quality-spec.md`
- Metadata schemas → `design/06-observability-and-lineage-spec.md`
