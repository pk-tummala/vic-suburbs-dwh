# Runbook — Deployment

Deploying the warehouse to a Databricks workspace via the Asset Bundle.

## Prerequisites
- Databricks CLI authenticated (`databricks auth login`, or `DATABRICKS_HOST` +
  `DATABRICKS_TOKEN` for CI).
- The deployer identity is a member of `role_deployer` (holds catalog DDL).

## Validate
```bash
databricks bundle validate -t dev      # also run for qa / prod
```

## First-time bootstrap (per environment)
One command creates the RBAC groups, deploys the bundle, and provisions the catalog,
schemas, Volume, and grants (all idempotent):
```bash
make bootstrap ENV=dev          # or: ./deployment/bootstrap.sh --env dev
```
SQL-only reference for the catalog/grants step lives in `bootstrap/00_catalog_schema.sql`
and `bootstrap/10_grants.sql`; the automated path runs `bootstrap/bootstrap_uc.py` on
serverless via the `vic_suburbs_bootstrap_job`.

## Deploy & run
```bash
make deploy ENV=dev             # or: databricks bundle deploy -t dev
make run    ENV=dev             # or: databricks bundle run vic_suburbs_job -t dev
```

## Promotion
Promotion is re-deploying the **same** bundle to the next target; only the target
variables (catalog, warehouse size, schedule, notifications, run-as) change, so
environments cannot drift structurally.
```bash
databricks bundle deploy -t qa     # smoke test
databricks bundle deploy -t prod   # runs as sp_vic_suburbs_prod, on schedule
```

## Rollback
- **Code:** re-deploy a previous bundle revision (git tag).
- **Data:** Delta time travel / `RESTORE` on the affected Gold tables, independent of code.

## Teardown

Remove **every** deployed object for an environment — bundle resources and the catalog
with all its contents:

```bash
make destroy ENV=dev                  # prompts for confirmation
./deployment/destroy.sh --env dev     # add --force to skip the prompt
```

1. `databricks bundle destroy -t <env>` removes the pipeline, job, and dashboards.
2. `databricks catalogs delete vic_suburbs_<env> --force` drops the catalog and, by
   cascade, every schema, table, view, and Volume (including Auto Loader checkpoints).

Account groups are left intact by design. SQL-only equivalent of step 2:
`bootstrap/99_teardown.sql`.

---

## Environments at a glance
| Target | Catalog | Mode | Schedule | Run-as |
|---|---|---|---|---|
| dev | `vic_suburbs_dev` | development | paused | developer |
| qa | `vic_suburbs_qa` | production | unpaused | deployer |
| prod | `vic_suburbs_prod` | production | weekly (Mon 06:00 AEST) | `sp_vic_suburbs_prod` |

See `docs/design/07-deployment-and-rbac-spec.md` for the full RBAC model.
