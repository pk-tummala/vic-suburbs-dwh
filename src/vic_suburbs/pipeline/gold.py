"""Gold builders: conformed SCD2 dimensions (native APPLY CHANGES), dim_year, and one
fact per measure entity. Surrogate keys are resolved against the dimension version valid
at the fact's period (temporal join), so history binds correctly.
"""

from __future__ import annotations

import dlt
from pyspark.sql import functions as F

from vic_suburbs.common.config import load_entity_config

# entity -> (target dim table)
_DIM_TARGET = {"suburb_ref": "dim_suburb", "lga_ref": "dim_lga"}


def define_dim_scd2(spark, entity: str):
    """Native SCD2 dimension from the entity's CDC feed."""
    scd = load_entity_config(entity)["manifest"]["scd"]
    target = _DIM_TARGET[entity]
    dlt.create_streaming_table(target, comment=f"Gold: SCD2 dimension {target}.")
    dlt.apply_changes(
        target=target,
        source=f"{entity}_changes",
        keys=scd["keys"],
        sequence_by=F.col(scd["sequence_by"]),
        stored_as_scd_type=2,
        track_history_column_list=scd.get("track"),
    )


def define_dim_year(spark):
    @dlt.table(name="dim_year", comment="Gold: calendar/census year dimension (Type 1).")
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
    """Temporal join: bind each measure row to the dim_suburb version valid at its period."""
    period_date = F.to_date(
        F.concat_ws("-", F.col("period").cast("string"), F.lit("07"), F.lit("01"))
    )
    cond = (
        (df["sal_code"] == dim_suburb["sal_code"])
        & (period_date >= dim_suburb["__START_AT"])
        & (period_date < F.coalesce(dim_suburb["__END_AT"], F.lit("9999-12-31").cast("timestamp")))
    )
    return df.join(
        dim_suburb.select("sal_code", "suburb_sk", "__START_AT", "__END_AT"), cond, "left"
    )


def define_fact(spark, entity: str):
    """Build fact_suburb_<entity> at grain suburb x year, with conformed SKs and lineage."""

    @dlt.table(
        name=f"fact_suburb_{entity}",
        comment=f"Gold: fact_suburb_{entity} (grain suburb x year).",
        table_properties={"quality": "gold"},
    )
    def _fact():
        s = dlt.read(entity)
        dim_suburb = dlt.read("dim_suburb")
        dim_year = dlt.read("dim_year")

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
