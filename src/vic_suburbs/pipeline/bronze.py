"""Bronze builders: Auto Loader ingestion of landing files into ``01_bronze.raw_<entity>``.

Bronze is a faithful, append-only capture. The only added columns are lineage
(``ingested_at``, ``source_file``); ``source_system``, ``batch_id`` and ``effective_ts``
arrive inside the files. No filtering or business logic here.
"""

from __future__ import annotations

import dlt
from pyspark.sql import functions as F

from vic_suburbs.common.config import load_source
from vic_suburbs.pipeline._layers import fqn


def define_bronze(spark, entity: str, landing_volume: str, catalog: str):
    src = load_source(entity)
    landing_path = f"{landing_volume}/{src.get('landing_path', entity).rstrip('/')}/"
    checkpoint = f"{landing_volume}/_schemas/raw_{entity}"

    @dlt.table(
        name=fqn(catalog, "bronze", f"raw_{entity}"),
        comment=f"Bronze: faithful capture of {entity} landing files.",
        table_properties={"quality": "bronze"},
    )
    def _bronze():
        return (
            spark.readStream.format("cloudFiles")
            .option("cloudFiles.format", "csv")
            .option("header", "true")
            .option("cloudFiles.schemaLocation", checkpoint)
            .option("cloudFiles.schemaEvolutionMode", "addNewColumns")
            .load(landing_path)
            .withColumn("ingested_at", F.current_timestamp())
            .withColumn("source_file", F.col("_metadata.file_path"))
        )

    return _bronze
