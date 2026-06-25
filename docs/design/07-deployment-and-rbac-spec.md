# 07 — Deployment & RBAC Specification

> **Status:** Locked design
>
> Defines how the project deploys to `dev` / `qa` / `prod` from one bundle via the Databricks CLI, and how Unity Catalog access control enforces least privilege. Deployment is config-driven and repeatable; access is role-based and minimal.

---

## 1. One bundle, three environments

The whole project — pipelines, jobs, schemas, grants, dashboards — is described once as a **Databricks Asset Bundle (DAB)** and deployed per environment by switching a target. No per-environment copies of code; only per-environment configuration.

```bash
databricks bundle validate -t dev
databricks bundle deploy   -t dev      # or qa / prod
databricks bundle run      vic_suburbs_pipeline -t dev
```

`databricks.yml` defines shared resources and three targets that override only what differs (catalog name, warehouse size, schedule, notification routing, run-as identity).

---

## 2. Bundle layout

```
databricks.yml                 # bundle name, targets, variables
resources/
 ├─ pipelines/
 │   └─ vic_suburbs.pipeline.yml     # the DLT (Lakeflow) pipeline: libraries, target catalog/schema, config
 ├─ jobs/
 │   └─ vic_suburbs.job.yml          # orchestration: pre-task → pipeline → post-task; schedule; notifications
 └─ dashboards/
     └─ suburb_insights.lvdash.yml   # AI/BI dashboard (added once Gold exists)
src/                           # pipeline + extractor + helper code (referenced by resources)
config/                        # sources / schemas / dq_rules / pipeline-per-env (see 04, 05)
```

Account-level identity (the RBAC groups), the catalog, schemas, volumes, and grants are provisioned by a small **bootstrap** — `deployment/bootstrap.sh` plus a UC bootstrap **job** in the bundle — run once per environment, so a fresh workspace reaches a known state from the bundle alone. The bootstrap runs in phases (account groups → catalog → bundle deploy → UC bootstrap job); see §4–§5 and the deployment runbook.

---

## 3. Per-environment variables

```yaml
# databricks.yml (excerpt)
variables:
  catalog: { description: "Target UC catalog" }
  warehouse_size: { description: "SQL warehouse size" }

targets:
  dev:
    mode: development           # pauses schedules, prefixes resources, isolates state
    variables: { catalog: vic_suburbs_dev,  warehouse_size: 2X-Small }
  qa:
    variables: { catalog: vic_suburbs_qa,   warehouse_size: Small }
  prod:
    mode: production            # enforces run-as service principal, no dev prefixes
    variables: { catalog: vic_suburbs_prod, warehouse_size: Medium }
    run_as: { service_principal_name: sp_vic_suburbs_prod }
```

`mode: development` keeps dev cheap and safe (schedules paused, resources namespaced per developer). `mode: production` enforces a service-principal run-as and rejects dev-only shortcuts.

---

## 4. Catalog & schema provisioning

The bootstrap step creates, idempotently, the topology from `00-overview-and-architecture.md` §5:

```sql
CREATE CATALOG IF NOT EXISTS ${catalog};
CREATE SCHEMA  IF NOT EXISTS ${catalog}.`00_landing`;
CREATE SCHEMA  IF NOT EXISTS ${catalog}.`01_bronze`;
CREATE SCHEMA  IF NOT EXISTS ${catalog}.`02_silver`;
CREATE SCHEMA  IF NOT EXISTS ${catalog}.`03_gold`;
CREATE SCHEMA  IF NOT EXISTS ${catalog}.`04_reporting`;
CREATE SCHEMA  IF NOT EXISTS ${catalog}.`05_metadata`;
CREATE VOLUME  IF NOT EXISTS ${catalog}.`00_landing`.files;   -- managed landing Volume
```

Managed tables and a managed Volume are used throughout (no external storage wiring for the POC), keeping the footprint inside Unity Catalog.

The catalog is created with `CREATE CATALOG` **SQL** executed through a serverless SQL warehouse that the bootstrap auto-provisions (`vic_suburbs_<env>_wh`) — not the CLI's `catalogs create`. On a **Default Storage** workspace there is no metastore storage root for the CLI path to bind to, so the SQL form (which inherits Default Storage) is the reliable one. The schemas, Volume, and grants are then applied by the UC bootstrap job. The warehouse is small and is removed again on teardown (§7).

---

## 5. RBAC — least privilege by role

Access is granted to **account-level groups**, never individuals, and each role gets the minimum it needs.

Unity Catalog grants only resolve **account-level** principals — workspace-local groups produce `PRINCIPAL_DOES_NOT_EXIST`. The five role groups are therefore created through the **account** API (not workspace-local `groups create`). To do this the bootstrap performs a one-time **account-level login** (`databricks auth login --account-id <ID>`, cached as the `vic-account` CLI profile); subsequent runs reuse the profile. Creating account groups requires **account-admin** rights — on a solo account you are the admin. Workspace OAuth (the dev login) is sufficient for everything *except* this group-creation step.

| Role (group) | Identity | Grants |
|---|---|---|
| `role_deployer` | CI service principal | `CREATE`/`MANAGE` on the catalog; deploys the bundle. The only role that runs DDL. |
| `svc_ingest` | pipeline run-as (ingest) | `WRITE` on `00_landing`, `01_bronze`; `READ` on `00_landing` Volume |
| `svc_transform` | pipeline run-as (transform) | `READ` on `01_bronze`; `WRITE` on `02_silver`, `03_gold`, `05_metadata` |
| `role_analyst` | human analysts / dashboard | `SELECT` on `03_gold`, `04_reporting` **only** |
| `role_steward` | data steward | `role_analyst` + `SELECT` on `05_metadata` (run logs, DQ, quarantine) |

Representative grants:

```sql
GRANT USE CATALOG ON CATALOG ${catalog} TO `role_analyst`;
GRANT USE SCHEMA, SELECT ON SCHEMA ${catalog}.`04_reporting` TO `role_analyst`;
GRANT USE SCHEMA, SELECT ON SCHEMA ${catalog}.`03_gold`      TO `role_analyst`;
-- analysts get NO access to 00_landing / 01_bronze / 02_silver

GRANT USE SCHEMA, MODIFY ON SCHEMA ${catalog}.`02_silver` TO `svc_transform`;
GRANT USE SCHEMA, SELECT ON SCHEMA ${catalog}.`01_bronze` TO `svc_transform`;
```

Principles enforced: analysts never see raw or intermediate layers; transform identity can't write Bronze; ingest identity can't write Gold; only the deployer holds DDL. Grants live in versioned SQL applied by the bootstrap, so access is reviewable in git and reproducible per environment.

---

## 6. CI/CD flow

```
PR  → validate (databricks bundle validate -t dev) + unit tests + lint
merge to main → deploy -t dev → integration test (run pipeline on dev fixtures)
tag/release   → deploy -t qa → smoke test → manual approval → deploy -t prod
```

The CI runner authenticates as `role_deployer` (service principal, OAuth). Secrets (tokens, source API keys) come from Databricks secret scopes or the CI secret store — never from `config/` files, which hold only non-sensitive settings and pointers. The one-time **account-group provisioning** (§5) is an account-admin operation run during environment bootstrap, separate from the per-deploy service-principal auth; in CI it is done once by an admin (or a principal granted account `group manager`) rather than on every pipeline run.

---

## 7. Promotion & rollback

- **Promotion** is re-deploying the same bundle to the next target; code is identical, only target variables change. QA and prod therefore can't drift from dev structurally.
- **Rollback** is re-deploying a previous bundle revision (git tag). Because Gold tables are Delta, data-level recovery uses **time travel** / `RESTORE` independently of code rollback.
- **Reprocessing** (full refresh of selected tables, backfills) is operator-gated and documented in the reprocessing runbook; it rebuilds from the immutable landing Volume.
- **Teardown** (`make destroy` / `deployment/destroy.sh`) reverses bootstrap in phases: bundle destroy + workspace bundle-folder cleanup → catalog delete (cascade) → remove the bootstrap-provisioned SQL warehouse → delete the account-level RBAC groups (via the `vic-account` profile). It is confirmation-guarded and idempotent, so a partially-created environment can always be cleaned up.

---

## 8. Environment isolation guarantees

- Separate catalog per env (`vic_suburbs_dev|qa|prod`) — no shared tables across environments.
- Separate run-as identities per env; prod uses a dedicated service principal.
- Dev schedules paused by `mode: development`; only prod runs on a trigger.
- Notification routing per env (dev → quiet/dev channel, prod → on-call).

---

## 9. Testing deployment

- **Dry validate:** `bundle validate -t <env>` in CI on every PR.
- **Fresh-workspace bootstrap:** assert a clean target reaches full topology + grants from the bundle alone.
- **Grant assertions:** post-deploy test confirms `role_analyst` can `SELECT` `04_reporting` and **cannot** read `01_bronze`.
- **Promotion parity:** assert dev and prod resource definitions differ only by declared variables.

---

## 10. Cross-references
- Catalog/schema topology → `00-overview-and-architecture.md` §5
- What runs inside the pipeline → `04-pipeline-pattern.md`
- Run-as identities and metadata writes → `06-observability-and-lineage-spec.md`
