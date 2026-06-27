"""DLT pipeline entry point.

Loaded by the Lakeflow Declarative Pipeline. Reads the entities manifest and wires the generic
Bronze/Silver/Gold builders for every registered entity — so the entire warehouse is defined by
configuration plus the shared builders, with no per-entity pipeline code. Tables are published
across schemas (01_bronze / 02_silver / 03_gold) via fully-qualified names.

Pipeline configuration (set in resources/pipelines/vic_suburbs.pipeline.yml) supplies:
  vic.catalog, vic.landing_volume, vic.config_dir, vic.env
"""

from __future__ import annotations

import os
import sys

from pyspark.sql import SparkSession

spark = SparkSession.getActiveSession()

# Put the bundle-deployed package source on sys.path so `vic_suburbs` imports on the serverless
# pipeline. bundle.sourcePath is set in the pipeline configuration to ${workspace.file_path}/src —
# a /Workspace path FUSE-mounted on the pipeline compute.
_src = spark.conf.get("bundle.sourcePath", None)
if _src and _src not in sys.path:
    sys.path.insert(0, _src)

from vic_suburbs.common.config import load_entities  # noqa: E402
from vic_suburbs.pipeline import bronze, gold, silver  # noqa: E402

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

# ── Bronze: every entity -> 01_bronze ────────────────────────────────────────
for e in entities:
    bronze.define_bronze(spark, e["name"], LANDING_VOLUME, CATALOG)

# ── Silver: reference CDC feeds, then measures -> 02_silver ──────────────────
for e in references:
    silver.define_silver_changes(spark, e["name"], CATALOG)
for e in measures:
    silver.define_silver_measure(spark, e["name"], CATALOG)

# ── Gold: SCD2 dims (+ surrogate keys), dim_year, facts -> 03_gold ───────────
for e in references:
    gold.define_dim_scd2(spark, e["name"], CATALOG)
gold.define_dim_year(spark, CATALOG)
for e in measures:
    gold.define_fact(spark, e["name"], CATALOG)
