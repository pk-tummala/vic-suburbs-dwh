# Databricks notebook source
# ─────────────────────────────────────────────────────────────────────────────
# Unity Catalog bootstrap (idempotent).
# Creates the catalog, the six schemas, the landing Volume, and applies the
# least-privilege grants. Run on serverless compute by `vic_suburbs_bootstrap_job`
# (deployment/bootstrap.sh), or manually with the `catalog` widget set.
#
# Groups must already exist (deployment/bootstrap.sh creates them via the CLI);
# grants to a missing group are skipped with a warning rather than aborting.
# ─────────────────────────────────────────────────────────────────────────────

dbutils.widgets.text("catalog", "vic_suburbs_dev")  # noqa: F821
catalog = dbutils.widgets.get("catalog")  # noqa: F821

SCHEMAS = ["00_landing", "01_bronze", "02_silver", "03_gold", "04_reporting", "05_metadata"]

# ── Catalog / schemas / volume ───────────────────────────────────────────────
spark.sql(f"CREATE CATALOG IF NOT EXISTS {catalog} COMMENT 'Victoria Suburbs Profiler'")  # noqa: F821
for s in SCHEMAS:
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.`{s}`")  # noqa: F821
spark.sql(  # noqa: F821
    f"CREATE VOLUME IF NOT EXISTS {catalog}.`00_landing`.files "
    f"COMMENT 'Landing Volume for extracted + synthetic files'"
)

# ── Least-privilege grants (group-based) ─────────────────────────────────────
GRANTS = [
    # analysts & stewards: read serving layers only
    f"GRANT USE CATALOG ON CATALOG {catalog} TO `role_analyst`",
    f"GRANT USE CATALOG ON CATALOG {catalog} TO `role_steward`",
    f"GRANT USE SCHEMA, SELECT ON SCHEMA {catalog}.`03_gold` TO `role_analyst`",
    f"GRANT USE SCHEMA, SELECT ON SCHEMA {catalog}.`04_reporting` TO `role_analyst`",
    f"GRANT USE SCHEMA, SELECT ON SCHEMA {catalog}.`03_gold` TO `role_steward`",
    f"GRANT USE SCHEMA, SELECT ON SCHEMA {catalog}.`04_reporting` TO `role_steward`",
    f"GRANT USE SCHEMA, SELECT ON SCHEMA {catalog}.`05_metadata` TO `role_steward`",
    # ingest identity: write landing + bronze only
    f"GRANT USE SCHEMA, MODIFY ON SCHEMA {catalog}.`00_landing` TO `svc_ingest`",
    f"GRANT USE SCHEMA, MODIFY ON SCHEMA {catalog}.`01_bronze` TO `svc_ingest`",
    f"GRANT READ VOLUME, WRITE VOLUME ON VOLUME {catalog}.`00_landing`.files TO `svc_ingest`",
    # transform identity: read bronze, write silver/gold/metadata
    f"GRANT USE SCHEMA, SELECT ON SCHEMA {catalog}.`01_bronze` TO `svc_transform`",
    f"GRANT USE SCHEMA, MODIFY ON SCHEMA {catalog}.`02_silver` TO `svc_transform`",
    f"GRANT USE SCHEMA, MODIFY ON SCHEMA {catalog}.`03_gold` TO `svc_transform`",
    f"GRANT USE SCHEMA, MODIFY ON SCHEMA {catalog}.`05_metadata` TO `svc_transform`",
    # deployer (CI service principal): catalog DDL
    f"GRANT ALL PRIVILEGES ON CATALOG {catalog} TO `role_deployer`",
]

applied, skipped = 0, 0
for g in GRANTS:
    try:
        spark.sql(g)  # noqa: F821
        applied += 1
    except Exception as e:  # missing group, etc. — don't abort the whole bootstrap
        skipped += 1
        print(f"  ⚠ skipped: {g}\n      reason: {e}")

print(f"✓ bootstrap complete for catalog '{catalog}' — grants applied={applied}, skipped={skipped}")
