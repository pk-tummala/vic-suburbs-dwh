# Data Model — Gold Star Schema (Fact Constellation)

> **Status:** Locked design
>
> Defines the Gold layer: grain, shared dimensions, per-subject facts, SCD2 placement, the
> surrogate-key strategy, and how each fact maps back to the six business questions. Silver feeds
> this; the `04_reporting` views sit on top of it.

---

## 1. Modelling approach

The six questions span different subject areas (demographics, transport, crime, schools, property)
that share *who* (the suburb) and *when* (the year) but nothing else. Forcing them into one wide
table would create a sparse, unmaintainable mess.

The Kimball pattern for this is a **fact constellation**: several fact tables, one per subject area,
all joined to a small set of **shared dimensions**. Because the dimensions are shared (same keys and
meaning everywhere), an analyst can compare across subjects ("cheap suburbs that also have low crime
and good schools") by joining facts through the common `dim_suburb` / `dim_year`.

**Grain rule:** every fact is one row per **suburb × period**, where the period is always resolved to
a `year_sk`.

---

## 2. The suburb key

Everything keys on the **State Suburb code**, `sal_code`. The synthetic generator writes `sal_code`
onto every row it produces, so:

- facts join `dim_suburb` by a stable key, with no name-matching step, and
- a `not_null` check on `sal_code` (a FATAL rule) guards that join.

The key is stable, but a suburb's **name, postcode, region, LGA, and boundary edition can change**
over time — which is exactly why `dim_suburb` is SCD Type 2 (see §4). The name changes; the key
doesn't, so a suburb's history stays connected across a rename.

---

## 3. Surrogate keys & conventions

- Each SCD2 dimension carries a surrogate key `<dim>_sk = xxhash64(business_key, __START_AT)`, so a
  new historical version gets its own key. Facts resolve and store that surrogate key.
- Facts hold surrogate keys, never natural keys, so history resolves correctly ("what was true in
  that year").
- A reserved **`-1` "Unknown" member** exists so a fact is never orphaned by a missing dimension row.
- All audit timestamps are stored **UTC**; the display layer renders Australia/Melbourne.
- `source_system` is carried into every fact. In this project it is always `SYNTHETIC`.

---

## 4. Shared dimensions

| Dimension | Type | Grain | Key attributes | Why this type |
|---|---|---|---|---|
| **`dim_suburb`** | **SCD2** | one version per suburb per change | `sal_code` (bk), `suburb_name`, `postcode`, `lga_code`, `region`, `asgs_edition`, `area_sqkm` | Name, postcode, LGA, region, and boundary edition change over time; a 50-year view needs "what was true then." Tracks `suburb_name, postcode, lga_code, region, asgs_edition`. |
| **`dim_lga`** | **SCD2** | one version per LGA per change | `lga_code` (bk), `lga_name`, `lga_type` | LGAs are renamed and amalgamated over the decades. Tracks `lga_name, lga_type`. |
| **`dim_year`** | Type 1 | one row per year | `year` (bk) + calendar helpers | Calendar facts don't change. |

`dim_year` (not a full `dim_date`) is enough because every fact's grain is annual.

---

## 5. Fact tables (one per subject → one per question)

Every fact carries the same housekeeping columns: `suburb_sk`, `year_sk`, `source_system`,
`batch_id`, `ingested_at`, `effective_ts`, `gold_loaded_at`. The measures below are what each fact
adds. Derived metrics (growth %, a connectivity score, per-capita rates) are **not** stored in the
facts — they are computed in the `04_reporting` views, so the facts stay simple and re-derivable.

### `fact_suburb_demographics` → **Q1**
Grain: suburb × census year.
Measures: `population_total`, `median_age`, `pop_0_14`, `pop_15_24`, `pop_25_44`, `pop_45_64`,
`pop_65_plus`, `median_household_income_weekly`.

### `fact_suburb_property` → **Q5 & Q6**
Grain: suburb × year.
Measures: `median_house_price`, `median_unit_price`, `median_rent_weekly`, `sales_volume`.
- **Q5 (affordable + growth):** `vw_q5_affordable_growth` pairs the current median price with a
  growth percentage computed from the history.
- **Q6 (most expensive):** `vw_q6_most_expensive` ranks by `median_house_price`.

### `fact_suburb_crime` → **Q3**
Grain: suburb × year.
Measures: `offence_count_total`. `vw_q3_low_crime` ranks suburbs by this (lower is better).

### `fact_suburb_transport` → **Q2**
Grain: suburb × year.
Measures: `train_station_count`, `tram_stop_count`, `bus_stop_count`, `train_freq_peak`,
`tram_freq_peak`, `bus_freq_peak`, `train_coverage`, `tram_coverage`, `bus_coverage`.
`vw_q2_transport_connectivity` combines stop counts, peak frequency, and coverage into a
`connectivity_index`.

### `fact_suburb_education` → **Q4**
Grain: suburb × year.
Measures: `govt_school_count`, `mean_icsea`. (Government schools only — Q4 asks about *public*
schooling.) `vw_q4_top_schools` plots school count against mean ICSEA.

---

## 6. Star diagram

<div align="center">
  <img src="er-fact-constellation.svg" alt="Fact constellation ER diagram" width="100%">
</div>

Facts map to the six questions: `demographics` → **Q1**, `property` → **Q5/Q6**,
`crime` → **Q3**, `transport` → **Q2**, `education` → **Q4**.

---

## 7. How each question becomes a query

| Q | Shape of the query |
|---|---|
| Q1 | `fact_suburb_demographics` ⋈ `dim_suburb` ⋈ `dim_year`, trend `population_total` by year. |
| Q2 | `fact_suburb_transport` ⋈ `dim_suburb`, rank by the derived `connectivity_index`. |
| Q3 | `fact_suburb_crime` ⋈ `dim_suburb`, rank by `offence_count_total` ascending. |
| Q4 | `fact_suburb_education` ⋈ `dim_suburb`, plot `govt_school_count` against `mean_icsea`. |
| Q5 | `fact_suburb_property`, pair the current median price with its growth %, then rank. |
| Q6 | `fact_suburb_property`, rank by `median_house_price` descending. |
| Cross | "cheap + safe + good schools" = property ⋈ crime ⋈ education on (`suburb_sk`, `year_sk`). The shared dims make this a simple join. |

Each ships as a curated `04_reporting.vw_q*` view, so the dashboard never embeds raw SQL.

---

## 8. SCD2 placement summary

| Layer | SCD2 | Type 1 / insert-only |
|---|---|---|
| Silver | the **suburb & LGA reference entities** | measure snapshots |
| Gold dims | `dim_suburb`, `dim_lga` | `dim_year` |
| Gold facts | — | all facts are **insert/append by (suburb, year)**; a corrected year is appended as a new row, never edited in place |

Insert-only facts follow standard practice: a measured value for (suburb, 2016) is a historical
fact; corrections are appended, which preserves the audit trail.

---

## 9. Cross-references
- SCD2 mechanics → `design/01-scd2-strategy.md`
- The synthetic universe → `design/03-data-sourcing-and-synthetic-universe.md`
- Incremental refresh per fact → `design/02-incremental-loading-strategy.md`
