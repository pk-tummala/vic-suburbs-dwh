# 03 — Data Sourcing & the Synthetic Universe

> **Status:** Locked design
>
> Maps each subject area to a real public source, states the honest coverage limits (especially the 50-year ambition), and defines the synthetic-universe generator that fills gaps. The governing rule: **real data where it exists, transparently-flagged synthetic data where it doesn't — never silently mixed.**

---

## 1. Source systems

| Subject | Source | Access | Geography / cadence |
|---|---|---|---|
| Demographics (Q1) | **ABS Census** — DataPacks + the `data.abs.gov.au` SDMX API | Bulk DataPacks (CSV) + REST/SDMX | SAL (State Suburb) / every 5 yrs |
| Property prices & rents (Q5, Q6) | **DataVic** — Victorian Property Sales Report + Rental Report | DataVic CKAN API (key + auth) | suburb / quarterly |
| Crime (Q3) | **Crime Statistics Agency Vic**, published via DataVic | DataVic CKAN API | suburb/LGA / annual |
| Public transport (Q2) | **PTV GTFS** timetable feed, published via DataVic | DataVic CKAN / GTFS zip | stop-level / periodic snapshot |
| Schools (Q4) | **ACARA / VCAA** school results + **DataVic** school zones/enrolments | DataVic CKAN + ACARA files | school → suburb / annual |

The DataVic open-data portal is CKAN-based and carries the housing (property sales, rental reports), GTFS public-transport, crime, and school datasets, reachable through an authenticated API — so the four DataVic-sourced subjects share one extractor pattern (CKAN `package_search` → `datastore_search` / resource download). ABS is a separate extractor (SDMX / DataPacks). Resource IDs are pinned in config rather than hard-coded in logic.

### Extractor pattern (config-driven, one engine)

A single parameterised extractor reads a source entry from `config/sources/*.yaml`:

```yaml
# config/sources/property.yaml
entity: property
source_system: DATAVIC
connector: ckan
ckan:
  base_url: https://discover.data.vic.gov.au
  resource_id: "<pinned-at-build-time>"
  page_size: 1000
landing_path: property/
effective_field: contract_quarter      # → effective_ts
key_fields: [suburb, postcode]          # → crosswalk to sal_code
```

Adding a source = adding a YAML file under `config/sources/` (metadata-driven; no code change).

---

## 2. Coverage limits

The target is ~**50 years** of suburb history. That depth does **not** exist cleanly in public data:

- **ABS** small-area census data is reliable from roughly the 1990s onward in convenient digital form; suburb (SAL) geography and topics are **not consistently comparable** back to the 1970s, and boundaries were redrawn repeatedly.
- **Crime** and **GTFS** histories are far shorter (years, not decades).
- **Property** quarterly series go back further but not uniformly per suburb.

This is why the project includes a **synthetic universe** to generate data for periods and entities not available publicly. Synthetic data is treated as first-class and always clearly labelled — it is never silently mixed with real data.

---

## 3. Synthetic-universe design (two-phase)

The generator uses a **seed-once / emit-repeatedly** pattern, keyed to suburbs.

```
┌────────────┐   build    ┌────────────────────┐   read    ┌────────────┐
│  seed.py   │──────────► │ synthetic_universe │ ────────► │  emit.py   │
│ (one-time) │            │   (Delta / SQLite) │           │ (repeated) │
└────────────┘            └────────────────────┘           └─────┬──────┘
   anchors on real                                               │ files →
   recent values                                          00_landing Volume
```

### Phase 1 — `seed.py` (build the universe once)
- Loads the **real list of Victorian suburbs** (SAL codes/names/LGAs from ABS geography) — the *spine is real*.
- For each suburb, takes the **latest real anchor values** that do exist (e.g. 2021 population, recent median price, current station count).
- **Back-casts** plausible history to the target horizon using documented, deterministic models:
  - population via a growth curve seeded per region (inner-Melbourne vs. regional differ),
  - prices via a CAGR with noise bounded to realistic ranges,
  - crime/schools/transport via slow drift around the anchor.
- Stores a `confidence_band` and `is_synthetic=true` on every generated point.
- Seedable RNG (`--seed 42`) → reproducible universe.

### Phase 2 — `emit.py` (produce batches)
- Emits CSV/Parquet batches into landing, stamped with one `batch_id`, `source_system=SYNTHETIC`, and the correct `effective_ts` per period.
- Supports modes: `--mode history` (back-fill the decades), `--mode update` (apply mutations so SCD2/CDC has something to capture — e.g. an LGA rename, a suburb boundary revision), `--mode mixed`.

### Mutation rules (so SCD2 is exercised)
`config/synthetic/mutation_rules.yaml`, e.g.:
```yaml
mutation_probabilities:
  suburb_lga_reassignment: 0.05    # amalgamation
  suburb_rename:           0.02
  boundary_revision:       0.10     # new asgs_edition
  price_shock:             0.15
```

---

## 4. Real ⊕ synthetic: the merge contract (never silently mixed)

1. Every row, real or synthetic, carries `source_system` and flows through `dim_geo_quality` (`is_synthetic`, `confidence_band`).
2. **Real always wins** where it exists: for a (suburb, period) present in both, the real row supersedes the synthetic one (synthetic is gap-fill only). Enforced by a priority rule in Silver (`source_system` ordering in the dedup/`SEQUENCE BY` tiebreak).
3. Dashboards **expose the flag**: every chart can filter or shade synthetic data, and a default banner states the synthetic horizon. No insight is presented as real when it isn't.
4. The crosswalk (`suburb_name+postcode → sal_code`) lives in Silver; rows that fail to map are **quarantined** by DQ, surfaced in `metadata.dq_results`, never dropped silently.

---

## 5. Conforming to `sal_code`

Non-ABS sources name suburbs as free text. The Silver crosswalk:
- Normalises case/whitespace, resolves known aliases, joins on (suburb_name, postcode, lga).
- Emits a match confidence; ambiguous matches (same suburb name in two LGAs) are disambiguated by postcode/LGA and flagged.
- Unmatched → quarantine table for manual review; the run still succeeds (WARN), unless unmatched share exceeds a configured FATAL threshold.

---

## 6. Licensing & provenance

- ABS and DataVic are open data under their respective CC-style licences; the repo stores **only derived/aggregated tables and config pointers**, not bulk re-publication of source files.
- `source_registry` in `05_metadata` records, per source: URL, resource id, licence, retrieved-at, row counts — provenance any reviewer can audit.

---

## 7. Testing

- **Unit:** seed determinism (same seed → identical universe); back-cast values stay within configured realistic bounds; crosswalk maps a known fixture set correctly.
- **Integration:** emit `history` then `update`; assert SCD2 opens new `dim_suburb` versions exactly on mutated attributes.
- **DQ:** quarantine path fires on a deliberately-unmappable fixture suburb.

---

## 8. Cross-references
- `source_system` / `dim_geo_quality` in the model → `data-model/data-model.md`
- `effective_ts` minting & `batch_id` → `02-incremental-loading-strategy.md`
- SCD2 mutation capture → `01-scd2-strategy.md`
