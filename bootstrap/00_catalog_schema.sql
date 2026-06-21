-- Idempotent topology. Run by the deploy bootstrap. ${catalog} substituted per target.
CREATE CATALOG IF NOT EXISTS ${catalog}
  COMMENT 'Victoria Suburbs Profiler';

CREATE SCHEMA IF NOT EXISTS ${catalog}.`00_landing`  COMMENT 'Raw landing files (Volume)';
CREATE SCHEMA IF NOT EXISTS ${catalog}.`01_bronze`   COMMENT 'Raw, append-only';
CREATE SCHEMA IF NOT EXISTS ${catalog}.`02_silver`   COMMENT 'Cleansed, conformed, SCD2 feeds';
CREATE SCHEMA IF NOT EXISTS ${catalog}.`03_gold`     COMMENT 'Star schema: dims + facts';
CREATE SCHEMA IF NOT EXISTS ${catalog}.`04_reporting`COMMENT 'Question-shaped serving views';
CREATE SCHEMA IF NOT EXISTS ${catalog}.`05_metadata` COMMENT 'Run log, DQ results, provenance';

CREATE VOLUME IF NOT EXISTS ${catalog}.`00_landing`.files
  COMMENT 'Landing Volume for extracted + synthetic files';
