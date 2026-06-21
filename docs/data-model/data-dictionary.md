# Data Dictionary

> **Status:** Locked design
>
> Table- and column-level reference for every layer. Types are indicative (resolved precisely in `config/schemas/*.yaml`). Lineage columns common to many tables are defined once in §1 and not repeated.

---

## 1. Conventions & common columns

- Naming: `snake_case`; dims `dim_*`, facts `fact_suburb_*`, reporting `vw_*`.
- Canonical suburb key: **`sal_code`** (ABS State Suburb code). Surrogate keys: `<dim>_sk` (integer).
- Timestamps stored **UTC**; display layer renders Australia/Melbourne.

**Lineage / audit columns** (present where noted):

| Column | Type | Meaning |
|---|---|---|
| `source_system` | string | `ABS` \| `DATAVIC` \| `ACARA` \| `SYNTHETIC` |
| `batch_id` | string | UUID minted at extraction; traceable end to end |
| `ingested_at` | timestamp | UTC load time (drives incrementality) |
| `effective_ts` | timestamp | When the fact was true in the real world (drives SCD2/grain) |
| `source_file` | string | Landing file path (Bronze) |
| `valid_from` / `valid_to` | timestamp | SCD2 validity; current row has `valid_to IS NULL` |
| `is_current` | boolean | SCD2 current-version flag |
| `created_by_run_id` / `closed_by_run_id` | string | Run that opened/closed an SCD2 version |

---

## 2. Bronze layer (`01_bronze`) — raw, append-only

One `raw_<entity>` per source entity. Schema mirrors the source plus lineage columns; no typing guarantees beyond what Auto Loader infers.

| Table | Holds | Key lineage cols |
|---|---|---|
| `raw_demographics` | ABS census suburb extracts | `source_system, batch_id, ingested_at, source_file` |
| `raw_property` | DataVic property sales + rental | same |
| `raw_crime` | CSA crime by suburb/LGA | same |
| `raw_transport_gtfs` | PTV GTFS stops/routes snapshots | same |
| `raw_education` | ACARA/VCAA + DataVic school data | same |
| `raw_suburb_ref` / `raw_lga_ref` | ABS geography (suburb/LGA reference) | same + `effective_ts` |

---

## 3. Silver layer (`02_silver`) — typed, validated, conformed

Two kinds of object: **measure tables** (append, dedup-latest) and **`*_changes` CDC feeds** (drive Gold SCD2).

### `demographics`, `property`, `crime`, `transport`, `education`
Conformed measure tables. All carry `sal_code`, `period` (year/quarter resolved), the entity's typed measures, plus lineage columns. Rows failing FATAL DQ never appear; WARN-failed rows are quarantined.

### `suburb_changes`, `lga_changes`
CDC feeds: one row per observed state of the key, ordered by `effective_ts`, consumed by `APPLY CHANGES`.

| `suburb_changes` column | Type | Meaning |
|---|---|---|
| `sal_code` | string | Business key |
| `suburb_name` | string | Tracked (versioned) |
| `postcode` | string | Tracked |
| `lga_code` | string | Tracked (resolves to `lga_sk`) |
| `region` | string | Greater Melbourne \| Regional Victoria (tracked) |
| `asgs_edition` | string | ABS geography edition (tracked) |
| `area_sqkm` | double | Carried, not versioned |
| `effective_ts` | timestamp | Sequence key |

---

## 4. Gold layer (`03_gold`) — Kimball fact constellation

### Conformed dimensions

**`dim_suburb`** (SCD2)
| Column | Type | Notes |
|---|---|---|
| `suburb_sk` | bigint | PK (surrogate) |
| `sal_code` | string | Business key |
| `suburb_name`, `postcode`, `region`, `asgs_edition` | string | Versioned attributes |
| `lga_sk` | bigint | FK → `dim_lga` |
| `area_sqkm` | double | Non-versioned |
| `valid_from`, `valid_to`, `is_current` | — | SCD2 |

**`dim_lga`** (SCD2): `lga_sk` PK, `lga_code` BK, `lga_name`, `lga_type` (City/Shire/Rural City), SCD2 cols.

**`dim_year`** (Type 1): `year_sk` PK, `year`, `decade`, `is_census_year`, `census_cycle`.

**`dim_geo_quality`** (Type 1): `geo_quality_sk` PK, `is_synthetic`, `confidence_band`, `boundary_revision_flag`.

**`bridge_suburb_ancestry`**: `suburb_sk`, `year_sk`, `ancestry_sk`, `person_count` — resolves the high-cardinality ancestry/language breakdowns out of the demographics fact.

### Facts (all: `suburb_sk`, `year_sk`, `geo_quality_sk`, `source_system`, `batch_id`, `gold_loaded_at`)

**`fact_suburb_demographics`** → Q1
`population_total, population_male, population_female, median_age, pop_0_14, pop_15_24, pop_25_44, pop_45_64, pop_65_plus, median_household_income_weekly, pct_born_overseas, pct_english_only_home, dwelling_count, pct_owned_outright, pct_renting`

**`fact_suburb_property`** → Q5, Q6
`median_house_price, median_unit_price, median_rent_weekly, sales_volume, price_yoy_pct, price_cagr_5yr, rental_yield_pct, affordability_index`

**`fact_suburb_crime`** → Q3
`offence_count_total, offence_rate_per_100k, crimes_against_person, property_crime, drug_offences`

**`fact_suburb_transport`** → Q2
`train_station_count, tram_stop_count, bus_stop_count, nearest_station_dist_km, weekday_services_count, connectivity_score`

**`fact_suburb_education`** → Q4
`govt_school_count, govt_primary_count, govt_secondary_count, mean_icsea, naplan_band_avg, vce_median_study_score, total_enrolments`

> Facts are insert/append by (`suburb_sk`,`year_sk`,`source_system`). A corrected period is a restatement row flagged `is_restated`, never an in-place update.

---

## 5. Reporting layer (`04_reporting`) — question-shaped views

| View | Answers | Shape |
|---|---|---|
| `vw_q1_population_growth` | Q1 | demographics ⋈ suburb ⋈ year, population trend |
| `vw_q2_transport_connectivity` | Q2 | transport ⋈ suburb, ranked by `connectivity_score` |
| `vw_q3_low_crime` | Q3 | crime ⋈ suburb, ranked by `offence_rate_per_100k` asc |
| `vw_q4_best_schools` | Q4 | education ⋈ suburb, ranked by `mean_icsea` / VCE |
| `vw_q5_affordable_growth` | Q5 | property filtered `affordability_index<1` & `price_cagr_5yr>0` |
| `vw_q6_most_expensive` | Q6 | property ranked by `median_house_price` desc |
| `vw_suburb_scorecard` | cross | property ⋈ crime ⋈ education ⋈ transport on (`suburb_sk`,`year_sk`) |

All reporting views expose `is_synthetic` / `confidence_band` so consumers can filter or shade modelled data.

---

## 6. Metadata layer (`05_metadata`) — observability

| Table / view | Purpose |
|---|---|
| `pipeline_run_log` | One row per run: status, trigger, batch_ids, row counts, error |
| `dq_results` | Per-rule pass/fail counts per run |
| `dq_quarantine` | WARN-dropped rows with failing rule names |
| `source_registry` | Provenance per `batch_id`: endpoint, licence, rows, synthetic flag |
| `vw_pipeline_health` | Latest run per pipeline + any failing DQ rule |
| `vw_run_history` | Runs over time (trend / SLA) |

Full column lists for these are in `design/06-observability-and-lineage-spec.md` and `design/05-data-quality-spec.md`.

---

## 7. Cross-references
- Model rationale & grain → `data-model.md`
- SCD2 columns → `design/01-scd2-strategy.md`
- DQ rules → `design/05-data-quality-spec.md`
- Metadata schemas → `design/06-observability-and-lineage-spec.md`
