# 00 — Overview & High-Level Architecture

> **Status:** Locked design
>
> This is the anchor document. It states the problem, the questions the warehouse must answer, the technology choices and *why*, and the end-to-end shape of the system. Every other design doc refines a slice of what is declared here. If a later doc contradicts this one, this one wins until explicitly amended.

---

## 1. The problem

Build a Lakehouse that profiles **every suburb in Victoria, Australia** and lets an analyst answer six questions — ideally across as long a history as the data allows (target: ~50 years):

| # | Business question | Primary subject area |
|---|---|---|
| Q1 | How has each suburb grown in population, and what are its demographics (age, ancestry, language, income…)? | Demographics |
| Q2 | Which suburbs have good public-transport connectivity? | Transport |
| Q3 | Which suburbs have a low (overall) crime rate? | Crime |
| Q4 | Which suburbs have the best public schooling? | Education |
| Q5 | Which suburbs are affordable/cheapest **and** have growth potential? | Property |
| Q6 | Which suburbs are the most expensive? | Property |

This is a proof of concept, engineered to production standards: data quality, incremental loading, idempotency, lineage, RBAC, automated tests, config-driven deployment, and full documentation.

---

## 2. Non-negotiable engineering properties

These are the acceptance criteria for the build. Each must be physically demonstrable, not merely asserted:

1. **Idempotency & statefulness** — re-running any layer with no new data is a clean no-op; re-running after a partial failure converges to the correct state, never duplicates.
2. **Incremental loading** — new census years / new monthly extracts are processed without full reloads.
3. **SCD2 history** — suburb attributes that genuinely change over time (name, LGA assignment, boundary revisions) are historized, not overwritten. Implemented with **native Databricks functionality, not hand-rolled MERGE logic** (see `01-scd2-strategy.md`).
4. **Config / metadata driven** — adding a source entity is a YAML change, not a code change.
5. **Data quality** — declarative rules with `WARN` (quarantine bad rows) and `FATAL` (fail the run) severities.
6. **Lineage** — both *job lineage* and *data lineage* are queryable, sourced from Unity Catalog system tables, not manually maintained.
7. **Observability** — every pipeline run logs start/end/status/row-counts/errors to a metadata schema.
8. **Reusability & automation** — one bundle deploys `dev` / `qa` / `prod`.
9. **Least privilege / RBAC** — Unity Catalog grants per role; no broad ownership.
10. **Tested** — unit (generators, DQ, transforms via Databricks Connect), integration (pipeline end-to-end on dev), and reconciliation (row-count / no-op assertions).
11. **Documented** — runbooks, deployment guide, data dictionary.

---

## 3. Technology stack (and the rationale)

The project is built **entirely on Databricks, from ingestion through to dashboards** — no second cloud, no external orchestrator. Every choice below follows from that constraint.

| Concern | Choice | Why |
|---|---|---|
| Platform | **Databricks Lakehouse** | Unifies ingestion → transform → BI in one control plane. |
| Governance / catalog | **Unity Catalog** (3-level `catalog.schema.table`, managed tables) | Native RBAC plus data & job lineage with no extra tooling. |
| Storage format | **Delta Lake** | ACID, time travel, MERGE — the substrate for everything below. |
| Ingestion | **Auto Loader** (`cloudFiles`) | Native incremental file ingestion with checkpointing + schema evolution — the checkpoint *is* our ingestion watermark. |
| Transformation | **Lakeflow Declarative Pipelines (DLT)** | Declarative B→S→G; native streaming tables, expectations (DQ), and `APPLY CHANGES INTO` — gives SCD1/SCD2 as a declarative feature rather than hand-written logic. |
| Orchestration | **Lakeflow Jobs** (Databricks Workflows) | Native scheduler/DAG; emits job lineage to system tables. |
| Deployment | **Databricks Asset Bundles (DABs)** via `databricks` CLI | One `databricks.yml`, per-env targets, CI-deployable. |
| Reporting | **Databricks AI/BI Dashboards** (with a Databricks App as a stretch) | Reads Gold directly, no export step. |
| Synthetic data | **Python generator → Delta/Volumes** | For history/entities not publicly available (see `03-data-sourcing-and-synthetic-universe.md`). |
| Dev environment | IntelliJ 2026.1.3 + Databricks CLI + Databricks Connect, on **WSL Ubuntu 24.04 / Win 11 Pro** | Standardised local toolchain. |

### Key design decisions

- **Incrementality is driven by native state, not a watermark table.** Auto Loader checkpoints and DLT streaming tables track what has already been processed. The metadata schema records run observability only — it does not decide what gets processed.
- **SCD2 is declarative.** `APPLY CHANGES INTO ... STORED AS SCD TYPE 2` manages version close/open; there is no hand-written hash-diff or MERGE logic to maintain.
- **Ingestion is file-drop plus scheduled trigger.** Extractors land files in a UC Volume; a Lakeflow Job drives the pipeline on schedule or on demand. No external event bus.

---

## 4. Medallion architecture

```
 SOURCES                         BRONZE (raw)          SILVER (clean, SCD2)        GOLD (star)              SERVE
 ───────                         ────────────          ────────────────────        ───────────              ─────
 ABS Census API/DataPacks  ┐                                                     dim_suburb (SCD2)
 DataVic CKAN (property,   │   raw_demographics   →   demographics  ┐            dim_lga    (SCD2)
   rental, crime, GTFS,    ├─► raw_property       →   property      │            dim_year   (T1)      ┐
   school)                 │   raw_crime          →   crime         ├─ conformed dim_geo_quality(T1) │
 ACARA / VCAA (schools)    │   raw_transport_gtfs →   transport     │   keys →    ─────────────────    ├─► AI/BI
 Synthetic universe        ┘   raw_education      →   education     ┘            fact_suburb_demographics│   Dashboards
   (gap-fill / backcast)       (Auto Loader,          (DLT streaming tables,     fact_suburb_property   │   + App
                                schema-on-read,        DQ expectations,          fact_suburb_crime      │
                                + source_system,       APPLY CHANGES SCD2)       fact_suburb_transport  ┘
                                + batch_id)                                       fact_suburb_education
```

- **Bronze** — append-only, schema-light, faithful copy of source. Adds `source_system`, `batch_id`, `ingested_at` (UTC), `source_file`. Never edited.
- **Silver** — typed, deduplicated, DQ-validated, conformed keys (every row carries `sal_code` = ABS State Suburb code as the canonical suburb key). SCD2 applied here for slowly-changing reference entities.
- **Gold** — Kimball **fact constellation**: conformed dimensions (`dim_suburb`, `dim_lga`, `dim_year`) shared across one fact table per subject area (see `data-model/data-model.md`). One subject = one fact = each business question is a single-join query.
- **Serve** — AI/BI Dashboards over Gold; a Databricks App as the interactive layer.

---

## 5. Catalog & schema topology (Unity Catalog)

One catalog per environment; layer-per-schema. Names are config-substituted by the bundle.

```
vic_suburbs_<env>            -- catalog  (vic_suburbs_dev | _qa | _prod)
 ├─ 00_landing               -- UC Volume for raw files dropped by extractors
 ├─ 01_bronze                -- raw_* tables
 ├─ 02_silver                -- cleansed + SCD2 tables
 ├─ 03_gold                  -- dim_* / fact_* star schema
 ├─ 04_reporting             -- vw_* serving views aligned to Q1–Q6
 └─ 05_metadata              -- pipeline_run_log, dq_results, source_registry
```

RBAC roles (least privilege) — detailed in the deployment doc, summarized here:
`svc_ingest` (write Bronze), `svc_transform` (write Silver/Gold), `role_analyst` (read 03/04 only), `role_deployer` (DDL + bundle deploy).

---

## 6. End-to-end data flow (happy path)

1. **Extract** — a parameterized Python/DLT extractor pulls from a configured source (CKAN resource, ABS SDMX query, or the synthetic generator) and writes raw files into the `00_landing` Volume, stamped with one `batch_id` and `ingested_at`.
2. **Bronze** — Auto Loader incrementally picks up new files → `raw_*` streaming tables. Checkpoint tracks what's been consumed.
3. **Silver** — DLT applies schema, DQ expectations, conforms `sal_code`, and `APPLY CHANGES INTO` historizes SCD2 entities. Bad rows quarantined (WARN) or run fails (FATAL).
4. **Gold** — DLT builds/refreshes conformed dims and per-subject facts.
5. **Serve** — AI/BI Dashboards read `04_reporting` views.
6. **Observe** — the Job writes a business-level row to `05_metadata.pipeline_run_log`; UC system tables capture job + table/column lineage automatically.

A single `batch_id` is traceable from the landing file all the way to a Gold fact row, giving full lineage within the platform.

---

## 7. Out of scope

- Real-time/streaming source feeds (sources are periodic: census every 5 yrs, quarterly property, annual crime/school).
- ML/forecasting for "growth potential" — Q5's growth signal is a **deterministic YoY/CAGR measure**, not a model. (A forecasting mart is a noted future extension.)
- Authentication/multi-tenant access beyond UC roles.

---

## 8. Cross-references

- Data model & grain → `data-model/data-model.md`
- Native SCD2 → `01-scd2-strategy.md`
- Incremental & idempotency → `02-incremental-loading-strategy.md`
- Real vs synthetic sourcing → `03-data-sourcing-and-synthetic-universe.md`
