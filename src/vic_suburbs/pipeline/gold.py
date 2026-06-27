"""Gold builders: SCD2 dimensions with stable surrogate keys, dim_year, and one fact
per measure entity. Tables publish to ``03_gold``; same-pipeline reads use fully-qualified names.

APPLY CHANGES targets carry ``__START_AT`` / ``__END_AT`` but no surrogate key, so each dimension
is published in two parts: an internal SCD2 history table (``dim_*_scd``) and a consumable
dimension (``dim_*``) that derives ``<dim>_sk = xxhash64(business_key, __START_AT)``. Facts reuse
the same formula via a temporal join, so fact<->dim keys line up across history.
"""

from __future__ import annotations

import dlt
from pyspark.sql import functions as F

from vic_suburbs.common.config import load_entity_config
from vic_suburbs.pipeline._layers import fqn

# reference entity -> (consumable dim table, surrogate-key column)
_DIM = {"suburb_ref": ("dim_suburb", "suburb_sk"), "lga_ref": ("dim_lga", "lga_sk")}


def _surrogate_key(business_keys: list[str], start_col):
    """Deterministic SK per SCD2 version: hash(business key + version start)."""
    parts = [F.col(k).cast("string") for k in business_keys] + [start_col.cast("string")]
    return F.xxhash64(*parts)


def define_dim_scd2(spark, entity: str, catalog: str):
    """Native SCD2 history (``dim_*_scd``) + a consumable dimension (``dim_*``) carrying the SK."""
    scd = load_entity_config(entity)["manifest"]["scd"]
    dim_name, sk_col = _DIM[entity]
    scd_table = fqn(catalog, "gold", f"{dim_name}_scd")

    dlt.create_streaming_table(scd_table, comment=f"Gold: SCD2 history backing {dim_name}.")
    dlt.apply_changes(
        target=scd_table,
        source=fqn(catalog, "silver", f"{entity}_changes"),
        keys=scd["keys"],
        sequence_by=F.col(scd["sequence_by"]),
        stored_as_scd_type=2,
        track_history_column_list=scd.get("track"),
    )

    @dlt.table(
        name=fqn(catalog, "gold", dim_name),
        comment=f"Gold: SCD2 dimension {dim_name} (with surrogate key {sk_col}).",
        table_properties={"quality": "gold"},
    )
    def _dim():
        d = spark.read.table(scd_table)
        return d.withColumn(sk_col, _surrogate_key(scd["keys"], F.col("__START_AT")))

    return _dim


def define_dim_year(spark, catalog: str):
    @dlt.table(
        name=fqn(catalog, "gold", "dim_year"),
        comment="Gold: calendar/census year dimension (Type 1).",
        table_properties={"quality": "gold"},
    )
    def _dim_year():
        years = spark.range(1971, 2031).withColumnRenamed("id", "year")
        return years.select(
            F.col("year").cast("int").alias("year_sk"),
            F.col("year").cast("int").alias("year"),
            (F.floor(F.col("year") / 10) * 10).cast("int").alias("decade"),
            (F.col("year") % 5 == 1).alias("is_census_year"),
        )

    return _dim_year


def _resolve_suburb_sk(df, dim_suburb):
    """Temporal join: bind each measure row to the dim_suburb version valid at its period.

    ``dim_suburb`` already carries ``suburb_sk`` (derived in define_dim_scd2), so the fact reads it
    straight from the dimension. Unmatched rows keep NULL and fall through to the -1 unknown member.
    """
    dim = dim_suburb.select(
        dim_suburb["sal_code"].alias("_dim_sal_code"),
        dim_suburb["__START_AT"],
        dim_suburb["__END_AT"],
        dim_suburb["suburb_sk"],
    )
    period_date = F.to_date(F.concat_ws("-", df["period"].cast("string"), F.lit("07"), F.lit("01")))
    cond = (
        (df["sal_code"] == dim["_dim_sal_code"])
        & (period_date >= dim["__START_AT"])
        & (period_date < F.coalesce(dim["__END_AT"], F.lit("9999-12-31").cast("timestamp")))
    )
    return df.join(dim, cond, "left")


def define_fact(spark, entity: str, catalog: str):
    """Build fact_suburb_<entity> at grain suburb x year, with surrogate keys and lineage."""

    @dlt.table(
        name=fqn(catalog, "gold", f"fact_suburb_{entity}"),
        comment=f"Gold: fact_suburb_{entity} (grain suburb x year).",
        table_properties={"quality": "gold"},
    )
    def _fact():
        s = spark.read.table(fqn(catalog, "silver", entity))
        dim_suburb = spark.read.table(fqn(catalog, "gold", "dim_suburb"))
        dim_year = spark.read.table(fqn(catalog, "gold", "dim_year"))

        f = _resolve_suburb_sk(s, dim_suburb)
        f = f.join(dim_year.select("year_sk", "year"), s["period"] == dim_year["year"], "left")

        measure_cols = [
            c
            for c in s.columns
            if c
            not in (
                "sal_code",
                "suburb",
                "postcode",
                "period",
                *("source_system", "batch_id", "ingested_at", "effective_ts"),
            )
        ]
        return f.select(
            F.coalesce(F.col("suburb_sk"), F.lit(-1)).alias("suburb_sk"),
            F.coalesce(F.col("year_sk"), F.lit(-1)).alias("year_sk"),
            *[F.col(c) for c in measure_cols],
            F.col("source_system"),
            F.col("batch_id"),
            F.current_timestamp().alias("gold_loaded_at"),
        )

    return _fact
