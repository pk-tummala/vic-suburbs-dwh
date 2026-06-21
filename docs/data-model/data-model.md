# Data Model — Gold Star Schema (Fact Constellation)

> **Status:** Locked design
>
> Defines the Gold layer: grain, conformed dimensions, per-subject facts, SCD2 placement, surrogate-key strategy, and the mapping from each fact back to the six business questions. Silver feeds this; reporting views in `04_reporting` sit on top of it.

---

## 1. Modelling approach

The six questions span **heterogeneous subject areas** (demographics, transport, crime, schools, property) that share *who* (the suburb) and *when* (the year) but nothing else. Forcing them into one wide fact would create a sparse, unmaintainable monster.

The correct Kimball pattern is a **fact constellation (galaxy schema)**: several fact tables, one per subject area, joined to a small set of **conformed dimensions**. Because the dimensions are conformed (identical keys and meaning everywhere), an analyst can compare across subjects ("cheap suburbs that also have low crime and good schools") by joining facts through the shared `dim_suburb` / `dim_year`.

**Universal grain rule:** every fact is grained at **one row per `suburb × period`**. The period unit differs by source cadence (census year, calendar year, or quarter) and is always resolved to a `year_sk`.

---

## 2. The canonical suburb key

Everything conforms on the **ABS State Suburb (SAL) code** — `sal_code` (e.g. Vermont = a specific SAL). Rationale:

- It is the ABS-authoritative small-area unit used in Census suburb profiles.
- It is stable enough to join across sources, yet its **name and boundary can change between ASGS editions** — which is exactly why `dim_suburb` is SCD2 (see §4).
- Non-ABS sources (DataVic property/crime/GTFS, ACARA schools) are keyed by suburb *name* + postcode + LGA; a **crosswalk** in Silver maps those to `sal_code`. Unmapped rows are quarantined by DQ, never silently dropped.

---

## 3. Surrogate keys & conventions

- Every dimension has a meaningless integer **surrogate key** `<dim>_sk` (generated, e.g. via `GENERATED ALWAYS AS IDENTITY` on the Delta table or a hash of business key + `valid_from`).
- Facts hold surrogate keys only — never natural keys — so SCD2 history resolves correctly ("what was true in that year").
- All timestamps stored **UTC**; the display layer renders Australia/Melbourne. (Most data here is annual, but ingestion/audit timestamps follow the rule.)
- A reserved **`-1` "Unknown" member** exists in every dimension so facts are never orphaned by a late/missing dimension row.
- `source_system` (`ABS` | `DATAVIC` | `ACARA` | `SYNTHETIC`) is carried into every fact so real vs. synthetic is always distinguishable downstream.

---

## 4. Conformed dimensions

| Dimension | Type | Grain | Key attributes | Why this SCD type |
|---|---|---|---|---|
| **`dim_suburb`** | **SCD2** | one version per suburb per attribute-change | `sal_code` (bk), `suburb_name`, `postcode`, `lga_sk`, `region` (Greater Melbourne / Regional Vic), `area_sqkm`, `asgs_edition` | Names, boundaries, and LGA assignment change across ASGS editions; 50-yr analysis needs "what was true then." |
| **`dim_lga`** | **SCD2** | one version per LGA per change | `lga_code` (bk), `lga_name`, `lga_type` (City/Shire/Rural City) | LGAs are amalgamated/renamed over decades. |
| **`dim_year`** | Type 1 | one row per year | `year` (bk), `decade`, `is_census_year`, `census_cycle` | Calendar facts don't change. |
| **`dim_geo_quality`** | Type 1 | one row per quality flag combo | `is_synthetic`, `confidence_band`, `boundary_revision_flag` | Lets dashboards transparently filter real vs. modelled data. |

`dim_year` (not a full `dim_date`) is sufficient because **every fact's grain is annual or coarser**. A `dim_date` is unnecessary weight here.

---

## 5. Fact tables (one per subject → one per question)

All facts share: `suburb_sk`, `year_sk`, `geo_quality_sk`, `source_system`, `batch_id`, `gold_loaded_at`. Listed below are the *additive/semi-additive measures* unique to each.

### `fact_suburb_demographics` → **Q1**
Grain: suburb × census year.
Measures: `population_total`, `population_male`, `population_female`, `median_age`, `pop_0_14`, `pop_15_24`, `pop_25_44`, `pop_45_64`, `pop_65_plus`, `median_household_income_weekly`, `pct_born_overseas`, `pct_english_only_home`, `dwelling_count`, `pct_owned_outright`, `pct_renting`.
Note: ancestry/language/religion breakdowns that explode in cardinality go to a **bridge** table `bridge_suburb_ancestry` (suburb × year × ancestry_sk × count) rather than hundreds of columns.

### `fact_suburb_property` → **Q5 & Q6**
Grain: suburb × year (rolled up from quarterly source).
Measures: `median_house_price`, `median_unit_price`, `median_rent_weekly`, `sales_volume`, `price_yoy_pct`, `price_cagr_5yr`, `rental_yield_pct`, `affordability_index` (median price ÷ state median).
- **Q5 (affordable + growth):** low `affordability_index` AND positive `price_cagr_5yr`.
- **Q6 (costliest):** high `median_house_price`.

### `fact_suburb_crime` → **Q3**
Grain: suburb × year.
Measures: `offence_count_total`, `offence_rate_per_100k`, plus division rollups (`crimes_against_person`, `property_crime`, `drug_offences`). Rate (not raw count) is the comparable measure — normalised by `population_total` from the demographics fact.

### `fact_suburb_transport` → **Q2**
Grain: suburb × snapshot year.
Measures: `train_station_count`, `tram_stop_count`, `bus_stop_count`, `nearest_station_dist_km`, `weekday_services_count`, `connectivity_score` (a documented composite of stop density + service frequency + mode diversity).

### `fact_suburb_education` → **Q4**
Grain: suburb × year.
Measures: `govt_school_count`, `govt_primary_count`, `govt_secondary_count`, `mean_icsea`, `naplan_band_avg`, `vce_median_study_score`, `total_enrolments`. (Government schools only — Q4 says "public schooling.")

---

## 6. Star diagram

<div align="center">
  <img src="er-fact-constellation.svg" alt="Fact constellation ER diagram" width="100%">
</div>

Facts map to the six questions: `demographics` → **Q1**, `property` → **Q5/Q6**,
`crime` → **Q3**, `transport` → **Q2**, `education` → **Q4**.

---

## 7. How each question becomes a query (single-join proof)

| Q | Shape of the query |
|---|---|
| Q1 | `fact_suburb_demographics` ⋈ `dim_suburb` ⋈ `dim_year`, trend `population_total` by year. |
| Q2 | `fact_suburb_transport` ⋈ `dim_suburb`, rank by `connectivity_score`. |
| Q3 | `fact_suburb_crime` ⋈ `dim_suburb`, rank by `offence_rate_per_100k` ascending. |
| Q4 | `fact_suburb_education` ⋈ `dim_suburb`, rank by `mean_icsea` / `vce_median_study_score`. |
| Q5 | `fact_suburb_property` filtered `affordability_index < 1` AND `price_cagr_5yr > 0`, ranked. |
| Q6 | `fact_suburb_property`, rank by `median_house_price` descending. |
| Cross | "cheap + safe + good schools" = property ⋈ crime ⋈ education on (`suburb_sk`,`year_sk`). The conformed dims make this trivial. |

Each of these ships as a curated `04_reporting.vw_q*` view so the dashboard never embeds raw SQL.

---

## 8. SCD2 placement summary

| Layer | SCD2 | Type 1 / insert-only |
|---|---|---|
| Silver | `demographics`, `property`, `crime`, `transport`, `education` reference attributes; the **suburb & LGA reference entities** | measure snapshots |
| Gold dims | `dim_suburb`, `dim_lga` | `dim_year`, `dim_geo_quality` |
| Gold facts | — | all facts are **insert/append by (suburb, year)**; a corrected year is a *restatement row* with `is_restated`, never an in-place update |

Insert-only facts follow standard dimensional practice: a measured value for (suburb, 2016) is a historical fact; corrections are appended and flagged, which preserves the audit trail.

---

## 9. Cross-references
- SCD2 mechanics → `design/01-scd2-strategy.md`
- Conformed-key crosswalk & quarantine → `design/03-data-sourcing-and-synthetic-universe.md`
- Incremental refresh per fact → `design/02-incremental-loading-strategy.md`
