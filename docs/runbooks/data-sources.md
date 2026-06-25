# Runbook — Data Sources (Synthetic & Real)

> **Status:** Reference doc · linked from README and the deployment guide
>
> Two ways to feed the warehouse: the built-in **synthetic universe** (zero external setup — the
> default for dry runs, demos, and CI) and **real Victorian open data** (what the final dashboard
> should answer from). This runbook explains what each covers, and gives an end-to-end path for
> both.

## Contents

- [Synthetic vs real — the short version](#synthetic-vs-real--the-short-version)
- [What real data actually exists (and how far back)](#what-real-data-actually-exists-and-how-far-back)
- [Path A — End-to-end on synthetic data (default)](#path-a--end-to-end-on-synthetic-data-default)
- [Path B — End-to-end on real data](#path-b--end-to-end-on-real-data)
  - [Step 1 — Decide the history window](#step-1--decide-the-history-window)
  - [Step 2 — Wire each source (config + connector)](#step-2--wire-each-source-config--connector)
  - [Step 3 — Extract, upload, run](#step-3--extract-upload-run)
  - [What changes, by file](#what-changes-by-file)
- [Per-entity source reference](#per-entity-source-reference)
- [Mixing synthetic and real](#mixing-synthetic-and-real)

---

## Synthetic vs real — the short version

The project runs **end-to-end on synthetic data out of the box** — no API keys, no source setup.
The synthetic universe is a deterministic ~50-year back-cast for every entity, so the SCD2/CDC
mechanics, the medallion flow, and the dashboards all work without touching an external system.

**Real data is what the POC ultimately answers from.** The six questions (population, transport,
crime, schooling, affordability, most-expensive) are meant to reflect *actual* Victorian suburbs.
Real public data exists for all six — but it does **not** reach a consistent 50-year suburb-level
history, so a real-data build covers the published window (roughly the last 10–15 years, deeper
for property) rather than the full back-cast. Synthetic can still fill the deep history if you
want the full shape; every row is tagged `source_system` so the two never get confused.

| | Synthetic | Real |
|---|---|---|
| External setup | none | per-source (pin config + implement connector) |
| History depth | ~50 years (deterministic back-cast) | varies by dimension (see below) |
| Use it for | dry runs, demos, CI, exercising SCD2/CDC | the real dashboard the POC answers |
| Tag | `source_system = SYNTHETIC` | `DATAVIC` / `ABS` |

---

## What real data actually exists (and how far back)

All six dimensions have a real, public, suburb- or LGA-level source. The limiting factor is
**history depth and cadence**, not availability:

| Entity | Real source | Grain | Realistic history |
|---|---|---|---|
| `suburb_ref` | ABS ASGS Ed. 3 — Suburbs & Localities (SAL) | suburb (SAL) | current + recent editions (2011/2016/2021); SAL codes change between editions |
| `lga_ref` | ABS ASGS — Local Government Areas | LGA | per edition; LGA boundaries change over time |
| `demographics` | ABS Census (DataPacks / Data by region / ABS Data API – SDMX) | SAL / SA2 / LGA | Census years only, 5-yearly; comparable suburb-level realistically 2011 → 2021 |
| `property` | data.vic — Valuer-General "Median House by Suburb (Time Series)" / "Guide to Property Values" | suburb | **~1988 → present** (the deepest real series here) |
| `crime` | Crime Statistics Agency Victoria — recorded offences by suburb/postcode/LGA | suburb | **2011 → present** |
| `transport` | data.vic / Transport Victoria Open Data — GTFS Schedule (`stops.txt`); PTV Timetable API | stop → geocoded to suburb | **current snapshot** (no long stop-count history) |
| `education` | data.vic — School Locations; ACARA School Profile (ICSEA, NAPLAN, suburb/LGA) | school → suburb | locations recent; ACARA profile **2008 → present** |

**Bottom line:** real, multi-dimension, suburb-level coverage overlaps from roughly **2011 to
present**, with property reaching back to the late 1980s and transport effectively a single
current snapshot. Plan the dashboard's time axis around that, or let synthetic carry the
pre-2011 history.

> Links: ABS ASGS — `abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs`
> · ABS Census DataPacks / Data API — `abs.gov.au/census` · data.vic — `discover.data.vic.gov.au`
> · Crime — `crimestatistics.vic.gov.au/.../download-data` · Transport — `opendata.transport.vic.gov.au`
> · ACARA — `acara.edu.au/contact-us/acara-data-access`. Verify dataset IDs at the source before
> pinning — they change between releases.

---

## Path A — End-to-end on synthetic data (default)

No source setup. This is the dry-run / demo / CI path.

```bash
make bootstrap ENV=dev     # first time per env (creates catalog/schemas/volume/grants/groups)
make deploy    ENV=dev     # push the bundle
make load      ENV=dev     # generate the synthetic universe locally + upload all entities
make run       ENV=dev     # pre_task -> DLT pipeline -> post_task
```

Everything is covered (`make load` writes all seven entities), so the run is fully populated.
Details and the make/CLI equivalents are in [`deployment-guide.md`](deployment-guide.md) § Deploy
and run.

---

## Path B — End-to-end on real data

Real loading is **per entity** — each source is identified, configured, and (for most) needs
connector code, because the four "CKAN" sources are not all CKAN datastore APIs and the ABS
connector is a stub.

### Step 1 — Decide the history window

Pick the period the dashboard will cover, based on the table above (e.g. 2011→present for full
six-dimension overlap, or 1988→present if property depth matters and you accept gaps elsewhere).
Decide whether synthetic back-fills the years a source doesn't publish, or whether the dashboard
simply starts where real data starts.

### Step 2 — Wire each source (config + connector)

For each entity you want real:

1. **Identify the dataset** at its portal and note its identifier (CKAN `resource_id`, ABS
   dataflow, GTFS feed URL, ACARA table).
2. **Pin it** in `config/sources/<entity>.yaml` (replace the `REPLACE_…` placeholder / add the
   query parameters).
3. **Make the connector real.** Today only the CKAN *datastore_search* path is implemented, and
   the ABS path raises `NotImplementedError`. The work per source:

| Entity | Source format | Connector work in `src/vic_suburbs/extract/run_extract.py` |
|---|---|---|
| `property` | data.vic CSV (file or datastore resource) | if the resource is datastore-active, just pin `resource_id`; if it's a file resource, add a CSV-download branch |
| `crime` | XLSX on crimestatistics.vic.gov.au (not data.vic CKAN) | add a small HTTP+XLSX downloader; select the suburb sheet/columns |
| `transport` | GTFS `.zip` (`stops.txt`) | add a GTFS connector: download the zip, read `stops.txt`, geocode stop lat/lon → SAL, aggregate stop counts per suburb |
| `education` | data.vic School Locations (CSV) + ACARA ICSEA | pin/adjust CKAN for locations; ICSEA via ACARA (application-gated) |
| `demographics` | ABS Census via ABS Data API (SDMX) | implement `AbsExtractor` SDMX query (population, age bands, income) by SAL |
| `suburb_ref` | ABS ASGS SAL (geography) | implement `AbsExtractor` geography: SAL code, name, LGA, region, area |
| `lga_ref` | ABS ASGS LGA (geography) | implement `AbsExtractor` geography: LGA code, name, type |

   **Each connector must emit the canonical landing columns** the Silver schema expects
   (`sal_code`, `period`/`effective_ts`, plus the entity's measures), and stamp `source_system`
   and a per-row `effective_ts`. That mapping from raw source columns → canonical columns lives in
   the connector — it is the real work, because raw source column names differ from the synthetic
   ones.

### Step 3 — Extract, upload, run

Once a source is wired, it loads with the same local→Volume pattern as synthetic:

```bash
# pin config/sources/property.yaml first, then:
make extract ENTITY=property        # python -m vic_suburbs.extract.run_extract property -> .local/landing/property/
make upload  ENV=dev                # push .local/landing/* to the Volume
make run     ENV=dev
```

Repeat `make extract ENTITY=<e>` for each wired entity (leave the rest on synthetic — see
[Mixing synthetic and real](#mixing-synthetic-and-real)).

### What changes, by file

A full switch to real data for an entity touches:

- **`config/sources/<entity>.yaml`** — real identifiers (resource_id / dataflow / feed URL).
- **`src/vic_suburbs/extract/run_extract.py`** — the connector implementation + raw→canonical
  column mapping (and `source_system`/`effective_ts` stamping).
- **`config/schemas/<entity>.yaml`** and **`config/dq_rules/<entity>.yaml`** — *only if* you change
  the canonical columns or want source-specific DQ thresholds. If the connector maps cleanly to the
  existing canonical columns, these don't change.
- **Time scope** — whichever target period you chose in Step 1; nothing in code hardcodes 50 years,
  so a shorter real window just produces fewer periods.

---

## Per-entity source reference

Quick pointers (confirm current IDs at the source):

- **`suburb_ref` / `lga_ref`** — ABS ASGS Edition 3, Suburbs & Localities (SAL) and LGA, under
  *Non-ABS Structures*; geospatial web services, linked-data API, and downloads.
- **`demographics`** — ABS Census DataPacks (SAL/SA2), "Data by region", or the ABS Data API
  (SDMX) for selected variables; 5-yearly.
- **`property`** — data.vic, Valuer-General Victoria: "Victorian Property Sales Report — Median
  House by Suburb (Time Series)" and "Guide to Property Values" (yearly suburb medians).
- **`crime`** — Crime Statistics Agency Victoria, *Download data*: recorded offences by
  suburb/postcode/LGA (XLSX), back to 2011.
- **`transport`** — data.vic / Transport Victoria Open Data: GTFS Schedule (`stops.txt`); the PTV
  Timetable API is an alternative for stop/route data.
- **`education`** — data.vic "School Locations" (annual); ACARA School Profile for ICSEA/NAPLAN by
  school (suburb/LGA), 2008+.

---

## Mixing synthetic and real

Synthetic and real rows coexist in the same tables, distinguished by `source_system`
(`SYNTHETIC` vs `DATAVIC`/`ABS`). Real data does **not** automatically evict synthetic rows for the
same grain — precedence is operational:

- **Switch an entity fully to real:** run `make extract ENTITY=<e>` (after `make generate`, it
  overwrites that entity's local folder), then `make upload` — the Volume gets real data for that
  entity and synthetic for the rest.
- **Keep synthetic for deep history, real for recent years:** load both; the periods don't
  overlap, so they simply concatenate. Shade or filter `source_system` in the reporting views.

A `source_system`-priority rule in the Silver dedup (real beats synthetic for an identical grain)
is a natural future enhancement if you ever load both for the *same* period.
