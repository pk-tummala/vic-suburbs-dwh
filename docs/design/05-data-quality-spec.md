# 05 — Data Quality Specification

> **Status:** Locked design
>
> Defines how data quality is declared (config-driven YAML), enforced (native DLT expectations), classified (WARN vs. FATAL), and observed (expectation metrics surfaced to the metadata schema). DQ runs in Silver, on every row, on every load.

---

## 1. Principles

1. **Declarative, not procedural.** Rules are data (`config/dq_rules/*.yaml`), enforced by native DLT expectations — not hand-written `if` checks scattered through transforms.
2. **Two severities, clear contracts.** `WARN` quarantines bad rows and lets the run succeed; `FATAL` fails the run. Nothing in between.
3. **Bad rows are never silently lost.** Dropped (WARN) rows and the reason are recorded, queryable in `05_metadata.dq_results` and the quarantine table.
4. **Quality is measured every run.** Pass/fail counts per rule are emitted automatically and trend over time.

---

## 2. Where DQ runs

DQ is a **Silver** responsibility. Bronze captures faithfully (no filtering); Gold trusts Silver. Running DQ at the Silver boundary means every downstream table — dims, facts, reporting — is built only from validated rows.

A small set of **Gold-level reconciliation checks** also exist (row-count parity, no orphan facts, no overlapping SCD2 intervals) — see §7 — but these assert *modelling* correctness, not raw-data quality.

---

## 3. Rule types

A fixed vocabulary keeps rules declarative and the engine simple. Each maps to a generated expectation.

| Rule type | Meaning | Example use |
|---|---|---|
| `not_null` | Column must be present | `sal_code`, `period` |
| `unique` | Column (or key set) unique within the batch | `sal_code + period` |
| `in_set` | Value within an allowed set | `source_system ∈ {ABS,DATAVIC,ACARA,SYNTHETIC}` |
| `value_range` | Numeric within `[min,max]` | `median_age ∈ [0,120]` |
| `regex_match` | String matches pattern | `postcode ~ ^3\d{3}$` (Victoria) |
| `row_count_min` | Batch has at least N rows | guard against empty loads |
| `cross_field` | Relationship between columns holds | `pop_total = sum(age bands)` (tolerance) |
| `crosswalk_resolved` | Non-ABS key mapped to a `sal_code` | property/crime/school suburb → SAL |

`crosswalk_resolved` is specific to this warehouse: rows whose suburb name+postcode could not be mapped to a canonical `sal_code` are the most common real-world defect, so they get a first-class rule.

---

## 4. Rule configuration

```yaml
# config/dq_rules/property.yaml
entity: property

rules:
  - name: sal_code_not_null
    type: not_null
    column: sal_code
    severity: FATAL

  - name: suburb_crosswalk_resolved
    type: crosswalk_resolved
    column: sal_code
    severity: WARN              # unmapped rows quarantined, run continues

  - name: postcode_is_victorian
    type: regex_match
    column: postcode
    pattern: '^3\d{3}$'
    severity: WARN

  - name: median_price_sane
    type: value_range
    column: median_house_price
    min: 0
    max: 50000000
    severity: WARN

  - name: minimum_batch_size
    type: row_count_min
    min: 1
    severity: FATAL
```

A FATAL escalation threshold can be attached to a WARN rule (e.g. *fail if > 20 % of rows are unmapped*) to stop pathological loads:

```yaml
  - name: crosswalk_resolved_rate
    type: crosswalk_resolved
    column: sal_code
    severity: WARN
    fail_if_violation_pct_above: 20    # WARN per-row, FATAL in aggregate
```

---

## 5. Mapping rules to native expectations

The build generates DLT expectations from the YAML. Severity decides the decorator:

| Severity | DLT mechanism | Effect |
|---|---|---|
| `WARN` | `@dlt.expect_all_or_drop({...})` | Violating rows dropped from the output, counted in metrics, copied to quarantine |
| `FATAL` | `@dlt.expect_all_or_fail({...})` | Pipeline update fails; nothing is published |
| (informational) | `@dlt.expect_all({...})` | Recorded only; row kept (used sparingly for monitoring) |

```python
# generated from config at build time
warn_exprs  = build_expectation_exprs(DQ["property"], severity="WARN")
fatal_exprs = build_expectation_exprs(DQ["property"], severity="FATAL")

@dlt.table(name="property")
@dlt.expect_all_or_drop(warn_exprs)
@dlt.expect_all_or_fail(fatal_exprs)
def property():
    ...
```

`build_expectation_exprs` translates each rule type into a boolean SQL expression keyed by rule name, so the DLT event log records pass/fail counts under the rule's name.

---

## 6. Quarantine (where dropped rows go)

WARN-dropped rows are not discarded blindly. A parallel expectation-free view captures violators with the failing rule attached, written to `05_metadata.dq_quarantine`:

```
dq_quarantine(
  batch_id, entity, sal_code_raw, suburb_raw, postcode_raw,
  failed_rules ARRAY<STRING>, source_system, ingested_at, quarantined_at)
```

This makes "which suburbs failed the crosswalk this run, and why" a one-query answer, and feeds the crosswalk-improvement loop.

---

## 7. Gold reconciliation checks

Run after Gold loads (as DLT expectations on reconciliation views, or as a post-job SQL test):

- **Fact↔dimension integrity:** every `fact_*.suburb_sk` resolves to a `dim_suburb` row (no `-1` Unknown beyond an allowed threshold).
- **SCD2 sanity:** exactly one current version per business key; no overlapping `[valid_from, valid_to)` intervals.
- **Grain uniqueness:** one row per (`suburb_sk`, `year_sk`, `source_system`) per fact.
- **Population sanity for rates:** `fact_suburb_crime.offence_rate_per_100k` only computed where a matching `population_total` exists.

---

## 8. DQ observability

Expectation metrics are emitted to the **pipeline event log** automatically. A metadata flow reads that event log and materializes `05_metadata.dq_results`:

```
dq_results(
  run_id, batch_id, entity, rule_name, severity,
  rows_evaluated, rows_passed, rows_failed, pass_rate, evaluated_at)
```

`vw_pipeline_health` joins this with run logs so a single view shows, per run: status, rows in/out, and any rule below its pass-rate threshold. Alerting hooks off this view (see `06-observability-and-lineage-spec.md`).

---

## 9. Testing DQ

- **Unit:** feed fixtures that violate each rule type; assert WARN drops + quarantines the row and FATAL raises.
- **Aggregate escalation:** feed a batch exceeding a `fail_if_violation_pct_above` threshold; assert the run fails.
- **Pass-through:** clean fixture produces zero drops and 100 % pass rates.

---

## 10. Cross-references
- Where DQ sits in the flow → `04-pipeline-pattern.md` §4
- Crosswalk to `sal_code` → `03-data-sourcing-and-synthetic-universe.md` §5
- Metrics surfacing → `06-observability-and-lineage-spec.md`
