# Operations runbook — verifying the warehouse

This is the single source of truth for **post-run data validation**: how to confirm that a build
is not just *green* but *correct*. The job reporting `SUCCESS` only means the flows ran without
raising — it does not, on its own, prove that rows made it through every layer or that the star
schema joins cleanly. The scripts in [`operations/`](../../operations) close that gap with an
independent check that runs outside the pipeline engine.

Three scripts:

| Script | Purpose | Exit semantics |
| --- | --- | --- |
| [`operations/verify_pipeline.sh`](../../operations/verify_pipeline.sh) | Pass/fail gate across the whole medallion | non-zero if any check **FAIL**s |
| [`operations/diagnose_fact_joins.sh`](../../operations/diagnose_fact_joins.sh) | Deep-dive into fact↔dimension joins | informational (always 0) |
| [`operations/diagnose_silver.sh`](../../operations/diagnose_silver.sh) | Explain an empty Silver measure (localize → crosswalk → DQ → ordering) | informational (always 0) |

## Prerequisites

- Authenticated Databricks CLI (the same profile used for `make deploy` / `make run`).
- `jq` and `awk` (preinstalled on Ubuntu/WSL); a running SQL warehouse.
- Warehouse selection order: `--warehouse-id <id>` → `$DATABRICKS_WAREHOUSE_ID` → the first
  warehouse returned by the CLI. This matches [`tools/dbsql.sh`](../../tools/dbsql.sh).

## Watching pipeline progress

There are two ways to run the pipeline, and they differ in what they stream to the console:

- **`make run ENV=dev`** runs the full job (`vic_suburbs_job`): `open_run_log → build_lakehouse →
  build_reporting → close_run_log`. This is the real run — it writes the business run log and rebuilds
  the serving views. The CLI shows **task-level** status, not the pipeline's inner flows.
- **`make run-pipeline ENV=dev`** runs the DLT pipeline (`vic_suburbs_pipeline`) **on its own** and
  streams **per-flow progress** to stdout — each Bronze/Silver/Gold flow as it goes
  `QUEUED → RUNNING → COMPLETED` with row counts. It skips the run-log pre/post tasks and
  `build_reporting`, so it is a fast **dev-iteration** loop, not a substitute for `make run`.

Use `run-pipeline` while iterating on transforms to watch the flows live; use `run` for an
end-to-end build, then `make verify`. (Bootstrap is a *job*, `vic_suburbs_bootstrap_job`, with no DLT
flows of its own, so it already streams task progress — there is no separate flow stream for it.)

## When to run

- **After every `make run`** — run `make verify`. This is the routine confirmation step.
- **When `verify` flags something** — run `make diagnose` to see *why*.
- **Future CD gate** — `verify_pipeline.sh` is exit-code driven, so it is the natural post-deploy
  gate in a CD workflow (a GitHub Action with workspace secrets, running after `deploy` + `run`).
  It is intentionally **not** part of the unit CI (`.github/workflows/ci.yml`), which has no
  workspace, and it is intentionally **not** a Lakeflow job task — the whole value is that it
  checks the result from *outside* the engine that produced it.

## Commands

```bash
make verify ENV=dev                       # full gate
make diagnose ENV=dev                      # all facts
make diagnose ENV=dev ENTITY=property      # one fact
make diagnose-silver ENV=dev               # why is a Silver measure empty? (defaults to property)
make diagnose-silver ENV=dev ENTITY=crime  # focus another measure

# direct invocation (identical behaviour)
./operations/verify_pipeline.sh [--warehouse-id <id>] dev
./operations/diagnose_fact_joins.sh [--warehouse-id <id>] dev [entity]
./operations/diagnose_silver.sh [--warehouse-id <id>] dev [entity]
```

## What `verify_pipeline.sh` checks

Output is two `spark.show()`-style grids — a **Row-flow** matrix (`entity, bronze, silver, fact,
status`) and a **Checks** results table (`check, result, detail`) — followed by a
`PASS=/WARN=/FAIL=` summary. The run exits non-zero if any check is `FAIL`. All the check logic
lives in two declarative SQL statements (one Row-flow, one Checks) rendered by a shared grid, so
the script is mostly SQL with a thin runner; it reads the settled per-layer schemas directly
(`01_bronze` / `02_silver` / `03_gold` / `04_reporting` / `05_metadata`).

- **Run health (`05_metadata`)** — the latest `pipeline_run_log` row is `SUCCESS` and wrote rows.
  Because a `FATAL` expectation aborts the update, a `SUCCESS` here already implies no fatal DQ
  rule fired.
- **Row flow** — `bronze → silver → fact` counts per measure. **Fails** when an upstream has rows
  but the downstream is empty. This is the check that catches a silently-empty Silver or Gold even
  when the job is green.
- **Dimension sanity** — surrogate keys are unique, and there is exactly one *open* SCD2 version
  (`__END_AT IS NULL`) per business key.
- **Fact grain** — no duplicate `(suburb_sk, year_sk)` per fact. This is the bloat / fan-out
  detector: a join that multiplies rows breaks grain uniqueness.
- **Referential integrity** — every non-`-1` fact key resolves to a dimension row (zero orphans);
  warns when the `-1` unknown-member rate exceeds 50%.
- **Serving layer** — each `04_reporting` view is queryable; headline views return rows.

## Reading `diagnose_fact_joins.sh`

Output is rendered as `spark.show()`-style grids:

- **`dim_suburb` coverage window** — version count, distinct suburbs, and the
  `[earliest_start, latest_end]` validity span. If the earliest start is *after* your oldest
  measure period, early facts cannot bind.
- **Per-year resolution** (one block per fact) — `year, rows, resolved, unknown, pct_resolved`.
  A run of years at `pct_resolved = 0` is a **temporal-join coverage gap** (the dimension does not
  cover those periods), not random data loss.
- **Key integrity** — `fact_rows, orphan_suburb_sk, orphan_year_sk, duplicated_grain`. All zero
  (besides intentional `-1` unknown members) is the healthy state.

## Diagnosing an empty Silver layer

Run `make diagnose-silver` when `verify` reports **`silver empty though bronze has N rows`** — the
job is green, Bronze ingested, but a Silver measure has zero rows. This is the highest-value
diagnostic because a silently-empty Silver is invisible to the engine: nothing *raised*, so the run
is `SUCCESS`. The script focuses one entity (`property` by default; pass `ENTITY=<name>`) and prints
four grids that narrow the cause from the outside in:

1. **Bronze → Silver localization** — `entity, bronze, silver, dropped, drop_pct` for every measure.
   Confirms which layer the rows vanish at, and whether it is one entity or all of them.
2. **Crosswalk resolution** — `crosswalk_rows, ref_changes_rows, bronze_keys, resolved, name_only,
   postcode_only`. This reconstructs the `conform_sal_code` join (suburb **and** postcode) so you can
   tell a *join miss* from a *DQ drop*:
   - `resolved ≈ bronze_keys` → the join is healthy; the loss is downstream (go to grid 3).
   - `resolved = 0` but `name_only > 0` → names match, **postcodes** don't.
   - `resolved = 0` but `postcode_only > 0` → postcodes match, **names** don't (normalization gap).
   - `crosswalk_rows = 0` → the crosswalk itself is empty; inspect `suburb_ref_changes`.
3. **DQ rules for the entity** — prints `config/dq_rules/<entity>.yaml`. If grid 2 shows the join is
   healthy but Silver is still empty, a **`WARN` expectation is dropping every row**. The classic
   offender is a `regex_match` rule whose pattern silently matches nothing — e.g. a metacharacter
   that was stripped before reaching Spark, so `^3\d{3}$` degrades to `^3d{3}$` and no postcode ever
   matches. A `WARN` drop does not fail the run, so 100% of rows can disappear while the job stays
   green. `value_range` bounds that exclude the real data are the other common case.
4. **Flow ordering** — the `flow_progress` event-log sequence for `suburb_ref_changes`,
   `suburb_crosswalk`, and the measure. Confirms the crosswalk finished **before** the measure ran;
   an out-of-order read would localize here.

Read the grids top-down: localization tells you *where*, the crosswalk tells you *join vs DQ*, the
rules tell you *which rule*, and the ordering rules out a dependency race. The `resolved` count uses
`upper(trim(...))` normalization; the live pipeline also collapses internal whitespace, so expect a
tiny discrepancy on messy names — it does not affect the diagnosis.

## Troubleshooting playbook

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `silver empty though bronze has N rows` | A read upstream of Silver returned nothing (e.g. an un-ordered cross-schema batch read) | Check the Silver flow's source read; confirm the dependency is tracked |
| `fact empty though silver has N rows` | Same class, one layer down (Gold reading Silver) | Check the fact's source read |
| High `-1` unknown-suburb rate / years at `pct_resolved = 0` | Temporal-join coverage gap — dimension effective dates start after those periods | Confirm the dimension's first version is effective from the start of the analytical window |
| `duplicated grain row(s)` | A join is fanning out (dimension not unique on the join key) | Inspect the offending join; ensure the dimension is 1 row per surrogate key |
| `orphan suburb/year` | Fact carries a key with no dimension row | Rebuild dims before facts, or check the surrogate-key derivation matches on both sides |

## Where this fits in observability

These scripts are the **independent-trust** layer, on top of the in-pipeline controls described in
[`docs/design/06-observability-and-lineage-spec.md`](../design/06-observability-and-lineage-spec.md):

1. **DLT expectations** — row-level DQ inside the pipeline (`WARN` drop / `FATAL` fail).
2. **Run log** (`05_metadata.pipeline_run_log`) and **event log**
   (`05_metadata.pipeline_event_log`, surfaced by `04_reporting`/`05_metadata.vw_pipeline_health`)
   — per-run status, counts, and lineage.
3. **`operations/` scripts** — an external check that the *result* is correct, run after the fact.

A future hardening (not yet implemented) is to assert the same grain/orphan rules *inside* the job
as a SQL task, so a bad build self-fails. That is deliberately separate from these host-side
scripts.
