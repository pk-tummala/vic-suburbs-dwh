"""Silver builders: type, validate (DQ), and produce either a cleansed measure table or a CDC
feed for SCD2. Tables publish to ``02_silver``; cross-schema reads of Bronze use fully-qualified
names (multi-schema publishing).

Boundary rule (see design/04-pipeline-pattern.md): Silver reads only Bronze. Measures already
carry ``sal_code`` from the generator, so Silver just dedups to one row per grain and types.
"""

from __future__ import annotations

import dlt
from pyspark.sql import functions as F

from vic_suburbs.common.config import load_dq_rules, load_entity_config, load_schema
from vic_suburbs.common.dq import build_expectation_exprs
from vic_suburbs.common.transforms import build_cast_plan, dedup_latest
from vic_suburbs.pipeline._layers import fqn

LINEAGE = ["source_system", "batch_id", "ingested_at", "effective_ts"]


def _select_typed(df, schema: dict, keep_lineage: bool = True):
    plan = build_cast_plan(schema)
    cols = [F.col(c).cast(t).alias(c) for c, t in plan]
    if keep_lineage:
        # Don't re-add a lineage column the schema already typed (e.g. reference schemas list
        # effective_ts so it's cast to timestamp for SCD2 sequencing). Re-adding it would create
        # a duplicate column name, which Delta rejects on table creation.
        planned = {c for c, _ in plan}
        cols += [F.col(c) for c in LINEAGE if c in df.columns and c not in planned]
    return df.select(*cols)


def define_silver_measure(spark, entity: str, catalog: str):
    schema = load_schema(entity)
    rules = load_dq_rules(entity)
    warn = build_expectation_exprs(rules, "WARN")
    fatal = build_expectation_exprs(rules, "FATAL")
    grain = load_entity_config(entity)["manifest"].get("grain", ["sal_code", "period"])

    @dlt.table(
        name=fqn(catalog, "silver", entity),
        comment=f"Silver: typed, validated {entity}.",
        table_properties={"quality": "silver"},
    )
    @dlt.expect_all_or_drop(warn)
    @dlt.expect_all_or_fail(fatal)
    def _silver():
        # Batch read -> this Silver measure is a materialized view. Dedup-latest needs to see
        # every version of a grain (incl. same-batch restatements) to pick the winner, which is a
        # non-time-window (ROW_NUMBER) operation that Structured Streaming does not support. Bronze
        # (raw_<entity>) stays a streaming Auto Loader table; only this dedup step is batch.
        df = spark.read.table(fqn(catalog, "bronze", f"raw_{entity}"))
        # Dedup to one row per grain BEFORE typing, while source_file is still available as a
        # deterministic tiebreak: within a single batch a restatement ("_upd" part-file) sorts
        # after the base file, so it wins instead of an arbitrary row when ingest times are equal.
        df = dedup_latest(df, keys=grain, order_col="ingested_at", tiebreak=["source_file"])
        return _select_typed(df, schema)

    return _silver


def define_silver_changes(spark, entity: str, catalog: str):
    """CDC feed for an SCD2 reference entity (suburb_ref, lga_ref)."""
    schema = load_schema(entity)

    @dlt.table(
        name=fqn(catalog, "silver", f"{entity}_changes"),
        comment=f"Silver: CDC feed (one row per observed state) for {entity}.",
        table_properties={"quality": "silver"},
    )
    def _changes():
        df = spark.readStream.table(fqn(catalog, "bronze", f"raw_{entity}"))
        return _select_typed(df, schema)

    return _changes
