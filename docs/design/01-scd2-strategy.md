# 01 — SCD Type 2 Strategy (Native Databricks)

> **Status:** Locked design
>
> Defines how SCD2 is implemented using **native Databricks functionality** — `APPLY CHANGES INTO` in Lakeflow Declarative Pipelines (DLT) — rather than hand-written hash-diff + dual-MERGE logic. States which attributes are tracked, the canonical declaration every SCD2 table follows, and the rules for resolving "what was true in year N."

---

## 1. Approach

SCD2 is implemented with native Databricks functionality — `APPLY CHANGES INTO` in Lakeflow Declarative Pipelines (DLT) — rather than hand-written change detection or MERGE logic. Databricks manages version close/open, ordering, and validity columns, so the project carries no bespoke CDC code to maintain.

---

## 2. What SCD2 buys us here

A 50-year suburb warehouse must answer "what was true *then*," not "what is true now." Concrete cases this project must get right:

- A suburb's **boundary/name** is revised between ASGS census editions → 2006 demographics must point to the 2006 version of the suburb, not today's.
- An **LGA amalgamation** reassigns a suburb to a different council → historical crime/property rows keep their original LGA context.
- Without SCD2, every historical comparison silently uses present-day geography — a correctness bug, not a cosmetic one.

SCD1 (overwrite) would corrupt the entire historical premise of the project.

---

## 3. Where SCD2 applies

| Object | Strategy | Reason |
|---|---|---|
| `02_silver` reference entities (suburb, lga) | **SCD2** | Slowly-changing geography/identity. |
| `02_silver` measure feeds (demographics, property, crime, transport, education) | append, dedup-latest per (suburb, period) | Periodic snapshots; the *period* is the version. |
| `03_gold.dim_suburb`, `dim_lga` | **SCD2** | Shared historized dimensions. |
| `03_gold.dim_year` | Type 1 | Stable. |
| `03_gold.fact_*` | insert/append, restatement rows | Facts are immutable events (see data-model §8). |

---

## 4. The native declaration (canonical template)

Every SCD2 dimension is produced by a single `APPLY CHANGES INTO` against a CDC source flow. Databricks manages the version-close/version-open and the validity columns automatically.

**SQL (Lakeflow Declarative Pipeline):**

```sql
-- 1) The target streaming table that will hold SCD2 history
CREATE OR REFRESH STREAMING TABLE dim_suburb;

-- 2) Let Databricks manage the SCD2 mechanics
APPLY CHANGES INTO live.dim_suburb
FROM stream(live.silver_suburb_changes)          -- the CDC feed (one row per observed state)
  KEYS (sal_code)                                -- business key
  SEQUENCE BY effective_ts                       -- ordering that defines "newer"
  COLUMNS * EXCEPT (effective_ts, _ingest_meta)  -- attributes to carry
  STORED AS SCD TYPE 2;                          -- <-- native SCD2; no manual MERGE
```

**Python (equivalent, when logic needs to be programmatic):**

```python
import dlt

dlt.create_streaming_table("dim_suburb")

dlt.apply_changes(
    target="dim_suburb",
    source="silver_suburb_changes",
    keys=["sal_code"],
    sequence_by="effective_ts",
    stored_as_scd_type=2,
    except_column_list=["effective_ts", "_ingest_meta"],
    # track_history_column_list=[...]  # optional: only version on these columns
)
```

What Databricks does for us automatically:
- Detects whether an incoming row is **new**, an **update to tracked columns** (→ close current, open new), or **unchanged** (→ no-op).
- Maintains validity columns `__START_AT` / `__END_AT` (we surface them as `valid_from` / `valid_to`, current = `valid_to IS NULL`).
- Orders correctly even if CDC rows arrive out of order, via `SEQUENCE BY`.

> **Runtime note:** `APPLY CHANGES INTO` is the stable SCD interface; a newer `AUTO CDC` / `create_auto_cdc_flow` alias also exists. Pin to whichever is GA on the target workspace runtime — the pattern in this doc is the same either way.

---

## 5. Tracked vs. untracked attributes

To avoid spurious version churn, we version **only** on attributes whose change is historically meaningful. Use `track_history_column_list` (or `COLUMNS` selection) to pin these.

| Dimension | Versioned ON (a change here opens a new row) | Carried but NOT versioned |
|---|---|---|
| `dim_suburb` | `suburb_name`, `postcode`, `lga_sk`, `region`, `asgs_edition` | `area_sqkm` derived recompute, audit cols |
| `dim_lga` | `lga_name`, `lga_type` | audit cols |

Rule: if changing the value should make *old facts keep the old value*, it's versioned. Otherwise it's an overwrite-in-place attribute.

---

## 6. The CDC feed contract (`silver_*_changes`)

`APPLY CHANGES` needs a CDC-shaped source: one row per **observed state** of the key, with a monotonic `effective_ts`. We build it in Silver:

1. Read Bronze for the entity (incremental).
2. Type-cast and DQ-validate (`sal_code` is already on every row).
3. Deduplicate to one row per (key, effective period).
4. Stamp `effective_ts` (the census/edition date the observation belongs to — **not** ingestion time, so history is anchored to *when it was true*, not *when we loaded it*).

This separation — *business effective time* drives SCD2, *ingestion time* drives incrementality — is the single most important subtlety and is enforced in `02-incremental-loading-strategy.md`.

---

## 7. Resolving "what was true in year N" (the join rule)

Facts join to the dimension **version valid at the fact's period**:

```sql
SELECT f.*, s.suburb_name, s.lga_sk
FROM   gold.fact_suburb_property f
JOIN   gold.dim_suburb s
  ON   s.sal_code = f.sal_code
 AND   f.period_date >= s.valid_from
 AND   f.period_date <  COALESCE(s.valid_to, TIMESTAMP '9999-12-31')
```

In practice the ETL resolves `suburb_sk` *at load time* by this same predicate, so analysts join on `suburb_sk` directly and never write the temporal predicate themselves.

---

## 8. Idempotency interaction

`APPLY CHANGES` is inherently idempotent on its CDC source: re-processing the same change rows produces no new versions (unchanged rows are no-ops). Combined with DLT's checkpointed streaming source, **re-running a pipeline with no new data creates zero new dimension versions** — a clean no-op.

---

## 9. Testing SCD2

- **Unit (Databricks Connect):** feed a synthetic change sequence (insert → tracked-col update → unchanged → untracked-col update) and assert exactly one new version on the tracked update, none on the others.
- **Reconciliation:** assert `COUNT(*) WHERE is_current` = count of distinct business keys; assert no overlapping validity intervals per key.
- **No-op:** re-run; assert version count unchanged.

---

## 10. Cross-references
- Dimension definitions → `data-model/data-model.md` §4
- Effective-time vs ingestion-time → `02-incremental-loading-strategy.md`
- Building the `*_changes` feed from raw → `03-data-sourcing-and-synthetic-universe.md`
