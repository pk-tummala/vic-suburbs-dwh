"""DLT pipeline entry point.

Loaded by the Lakeflow Declarative Pipeline. Reads the entities manifest and wires the
generic Bronze/Silver/Gold builders for every registered entity — so the entire warehouse
is defined by configuration plus the shared builders, with no per-entity pipeline code.

Pipeline configuration (set in resources/pipelines/vic_suburbs.pipeline.yml) supplies:
  vic.catalog, vic.landing_volume, vic.config_dir, vic.env
"""

from __future__ import annotations

import os

from pyspark.sql import SparkSession

from vic_suburbs.common.config import load_entities
from vic_suburbs.pipeline import bronze, gold, silver

spark = SparkSession.getActiveSession()

# Pipeline config -> environment for the config loader
_conf = spark.conf
CATALOG = _conf.get("vic.catalog")
LANDING_VOLUME = _conf.get("vic.landing_volume")
CONFIG_DIR = _conf.get("vic.config_dir", None)
if CONFIG_DIR:
    os.environ["VIC_CONFIG_DIR"] = CONFIG_DIR

entities = load_entities()
measures = [e for e in entities if e["kind"] == "measure"]
references = [e for e in entities if e["kind"] == "reference"]

# ── Bronze: every entity ─────────────────────────────────────────────────────
for e in entities:
    bronze.define_bronze(spark, e["name"], LANDING_VOLUME)

# ── Silver: reference CDC feeds, crosswalk, then measures ────────────────────
for e in references:
    silver.define_silver_changes(spark, e["name"])
silver.define_suburb_crosswalk(spark)
for e in measures:
    silver.define_silver_measure(spark, e["name"])

# ── Gold: SCD2 dims, dim_year, facts ─────────────────────────────────────────
for e in references:
    gold.define_dim_scd2(spark, e["name"])
gold.define_dim_year(spark)
for e in measures:
    gold.define_fact(spark, e["name"])
