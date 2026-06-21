-- Teardown (SQL alternative to deployment/destroy.sh step 2).
-- Drops the catalog and, by CASCADE, every schema, table, managed table, view, and Volume
-- (including Auto Loader checkpoints/schema locations stored in the landing Volume).
-- ${catalog} is substituted per target.  Run AFTER `databricks bundle destroy -t <env>`.
--
-- Account-level groups are intentionally not dropped here.

DROP CATALOG IF EXISTS ${catalog} CASCADE;
