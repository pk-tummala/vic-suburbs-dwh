-- Least-privilege grants (groups, not individuals). ${catalog} substituted per target.

-- Everyone who reads needs USE CATALOG.
GRANT USE CATALOG ON CATALOG ${catalog} TO `role_analyst`;
GRANT USE CATALOG ON CATALOG ${catalog} TO `role_steward`;

-- Analysts: serving layers only. No raw / intermediate access.
GRANT USE SCHEMA, SELECT ON SCHEMA ${catalog}.`03_gold`       TO `role_analyst`;
GRANT USE SCHEMA, SELECT ON SCHEMA ${catalog}.`04_reporting`  TO `role_analyst`;

-- Stewards: analyst + observability.
GRANT USE SCHEMA, SELECT ON SCHEMA ${catalog}.`03_gold`       TO `role_steward`;
GRANT USE SCHEMA, SELECT ON SCHEMA ${catalog}.`04_reporting`  TO `role_steward`;
GRANT USE SCHEMA, SELECT ON SCHEMA ${catalog}.`05_metadata`   TO `role_steward`;

-- Ingest identity: write landing + bronze only.
GRANT USE SCHEMA, MODIFY ON SCHEMA ${catalog}.`00_landing`    TO `svc_ingest`;
GRANT USE SCHEMA, MODIFY ON SCHEMA ${catalog}.`01_bronze`     TO `svc_ingest`;
GRANT READ VOLUME, WRITE VOLUME ON VOLUME ${catalog}.`00_landing`.files TO `svc_ingest`;

-- Transform identity: read bronze, write silver/gold/metadata.
GRANT USE SCHEMA, SELECT ON SCHEMA ${catalog}.`01_bronze`     TO `svc_transform`;
GRANT USE SCHEMA, MODIFY ON SCHEMA ${catalog}.`02_silver`     TO `svc_transform`;
GRANT USE SCHEMA, MODIFY ON SCHEMA ${catalog}.`03_gold`       TO `svc_transform`;
GRANT USE SCHEMA, MODIFY ON SCHEMA ${catalog}.`05_metadata`   TO `svc_transform`;

-- Deployer (CI service principal): DDL on the catalog.
GRANT ALL PRIVILEGES ON CATALOG ${catalog} TO `role_deployer`;
