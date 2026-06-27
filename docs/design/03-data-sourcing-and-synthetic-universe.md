# 03 — The Synthetic Universe

> **Status:** Locked design
>
> This project runs entirely on **synthetic data**. A small generator invents a complete, plausible
> 50-year history for a set of Victorian suburbs, writes it out as files, and the pipeline ingests
> those files like any other source. There are no external APIs, accounts, or keys — anyone can
> rebuild the exact same data from scratch. Every row is stamped `source_system = SYNTHETIC`.

---

## 1. What the generator produces

The generator covers all five subject areas plus the two reference tables the warehouse needs:

| Output | Feeds | Cadence |
|---|---|---|
| `demographics` | `fact_suburb_demographics` (Q1) | census years (every 5) |
| `property` | `fact_suburb_property` (Q5, Q6) | yearly |
| `crime` | `fact_suburb_crime` (Q3) | yearly |
| `transport` | `fact_suburb_transport` (Q2) | yearly |
| `education` | `fact_suburb_education` (Q4) | yearly |
| `suburb_ref` | `dim_suburb` (SCD Type 2) | initial version + later changes |
| `lga_ref` | `dim_lga` (SCD Type 2) | initial version + later changes |

Every measure row is keyed by `(sal_code, period)` — the suburb code and the year. Because the
generator already knows each suburb's code, it writes `sal_code` straight onto every row, so the
pipeline never has to match suburb names to codes.

---

## 2. The suburb spine

The starting point is one small file, `config/synthetic/suburb_seed.csv` — a list of 25 Victorian
suburbs with present-day "anchor" values. Each row holds the suburb's identity (code, name,
postcode, LGA, region, area) and a base value for each metric, for example:

```
sal_code, suburb_name, postcode, lga_code, ... , area_sqkm, base_population,
base_median_house_price, base_median_rent_weekly, base_offences,
base_train_stations, base_tram_stops, base_bus_stops, base_govt_schools, base_mean_icsea
```

These anchor values are the **only** numbers in the project that are hand-set; everything else is
derived from them. They are still invented — they're just realistic starting points the generator
projects history backward from.

---

## 3. How it works — build once, emit repeatedly

The generator has two phases:

```
┌────────────┐   build    ┌────────────────────┐   read    ┌─────────────┐
│  seed.py   │──────────► │ synthetic_universe │ ────────► │   emit.py   │
│  (build +  │            │      (SQLite)      │           │ (increments)│
│  baseline) │            └────────────────────┘           └─────┬───────┘
└────────────┘                                                   │ files →
                                                          00_landing Volume
```

**`seed.py` — build the universe and write the full baseline.** It reads the spine and the
back-cast settings, projects ~50 years of history for every suburb into a local SQLite database,
**and** writes that entire history out as landing files. So `make seed` gives you the complete
50-year starting point in one step.

**`emit.py` — add incremental batches on top.** It reads the same SQLite database and writes a
small new batch of files. It has three modes:

- `new` — the next year (the latest year + 1) for every measure, as brand-new rows.
- `update` — changes to data that already exists: suburb renames, LGA reassignments, boundary
  revisions, and price corrections. These are what the SCD Type 2 dimensions and CDC feeds capture.
- `mixed` — `new` and `update` together (the default).

`make generate` runs `seed` then `emit`; `make load` does the same and uploads the files to the
landing Volume.

---

## 4. The back-cast model

Every metric starts from its present-day anchor and is projected backward year by year. Growth
rates are drawn once per suburb (so each suburb has its own trajectory) from
`config/synthetic/seed_config.yaml`:

```yaml
growth:
  population_cagr: { mean: 0.012, sd: 0.006 }
  price_cagr:      { mean: 0.060, sd: 0.020 }
  rent_cagr:       { mean: 0.035, sd: 0.012 }
  crime_drift:     { mean: -0.010, sd: 0.030 }
  school_drift:    { mean: 0.002, sd: 0.010 }
  income_cagr:     { mean: 0.030, sd: 0.006 }
noise_pct: 0.03          # small year-to-year wobble
```

The numbers are shaped to look believable rather than random:

- **Population** grows along a per-suburb curve; **prices** and **rents** follow their own growth
  rates with a little noise.
- **Age structure** varies by suburb (denser, inner suburbs skew toward young adults; leafier ones
  toward children and mid-life) and ages gradually toward the present, mirroring the long national
  rise in median age. The five age bands always add up to the total population.
- **Income** rises with house price, so pricier suburbs have higher household incomes.
- **Crime** is a per-person rate applied to that year's population, so offence counts grow and
  shrink with the suburb rather than drifting on their own.
- **Sales volume** scales with the number of dwellings; **unit prices** track house prices at a
  per-suburb ratio; **school counts** grow as a suburb grows; **school ICSEA** stays within a
  realistic band.

The same seed always produces the same universe, so results are fully reproducible.

---

## 5. Mutations — giving SCD2 and CDC something to capture

`emit --mode update` applies occasional changes so the history-tracking machinery is genuinely
exercised. Probabilities live in `config/synthetic/mutation_rules.yaml`:

```yaml
mutation_probabilities:
  suburb_lga_reassignment: 0.05   # amalgamation  -> new dim_suburb version
  suburb_rename:           0.02   # name change   -> new dim_suburb version
  boundary_revision:       0.10   # new geography edition
  price_shock:             0.15   # a corrected property figure (a restatement)
```

A rename or reassignment lands a new `suburb_ref` version with a later effective date, which
`APPLY CHANGES` turns into a new SCD Type 2 row in `dim_suburb`. A price shock re-emits a recent
property row with a new value, which the Silver dedup keeps as the winning (latest) row.

---

## 6. One stable suburb key

Every suburb has one identifier — its State Suburb code, `sal_code` — and it never changes, even
when the suburb is renamed. The generator stamps `sal_code` onto every row it writes, so:

- facts join `dim_suburb` by a stable key with no name-matching step, and
- a `not_null` check on `sal_code` (a FATAL data-quality rule) guards that join.

This is what keeps a suburb's history connected across a rename: the name changes, the key doesn't.

---

## 7. Testing

- **Determinism:** building the universe twice with the same seed produces identical tables.
- **Invariant:** the five age bands always sum to the population total.
- **Emit modes:** `new` produces exactly the next year for every measure; `update` produces at
  least one dimension change or restatement.

These run as fast unit tests (no Spark required) — see `tests/unit/test_generator.py`.

---

## 8. Cross-references

- How `sal_code`, `batch_id`, and `effective_ts` flow through the layers → `02-incremental-loading-strategy.md`
- How dimension changes become SCD Type 2 history → `01-scd2-strategy.md`
- Column-by-column definitions → `../data-model/data-dictionary.md`
