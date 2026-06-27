# Runbook — Deployment

> **Status:** Reference doc · linked from README
>
> Deploying the warehouse to a Databricks workspace, running it, tearing it down, and rolling
> back. The project ships as one Databricks Asset Bundle plus a small bootstrap; deployment is
> idempotent (objects are created only if absent, resources are reconciled in place).

## Contents

- [Prerequisites](#prerequisites)
- [The deployment model](#the-deployment-model)
- [First-time setup (per environment)](#first-time-setup-per-environment)
- [Deploy and run](#deploy-and-run)
- [Subsequent deployments and promotion](#subsequent-deployments-and-promotion)
- [Teardown](#teardown)
- [Rollback](#rollback)
  - [Code rollback](#code-rollback)
  - [Data rollback (Delta time travel)](#data-rollback-delta-time-travel)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

- **Databricks CLI authenticated** (workspace) — `databricks current-user me` returns your user.
  Setup: [`intellij-wsl-setup.md`](intellij-wsl-setup.md) §4. CI uses `DATABRICKS_HOST` +
  `DATABRICKS_TOKEN`.
- **A Premium-tier workspace** with Unity Catalog and serverless enabled (one-time account
  toggle if not auto-enabled). Premium is required for RBAC.
- **Account admin** (for the RBAC groups). Unity Catalog grants only resolve **account-level**
  groups, so `make bootstrap` creates them via the account API — which needs a one-time
  account-level login. The script handles this for you (see *First-time setup*); you just need to
  be an account admin and have your **Account ID** handy. On a solo account you are the admin.
- **`jq`** installed (the bootstrap/teardown scripts and `dbsql.sh` parse JSON with it):
  `sudo apt-get install -y jq`.

> **Running the scripts directly?** If you call `./deployment/*.sh` (or `./tools/*.sh`) instead
> of the `make` targets, give them execute permission once:
> ```bash
> find . -type f -name "*.sh" -exec chmod +x {} +
> ```
> The `make` targets invoke the scripts via `bash …`, so they work without this.

---

## The deployment model

Everything is described once in `databricks.yml` (the bundle) and provisioned by a small
bootstrap. Three layers, all idempotent:

1. **Identity** — five **account-level** RBAC groups (`role_deployer`, `svc_ingest`,
   `svc_transform`, `role_analyst`, `role_steward`), created via the account API. Account groups
   (not workspace-local ones) are required because Unity Catalog grants only resolve
   account-level principals.
2. **Data-plane objects** — the catalog, six schemas, the landing Volume, and the least-privilege
   grants. The **catalog** is created with `CREATE CATALOG` SQL run through a serverless SQL
   warehouse, because the CLI can't create a catalog on a Default-Storage workspace; the schemas,
   Volume, and grants are applied by `bootstrap/bootstrap_uc.py` running as a serverless job. All
   `CREATE … IF NOT EXISTS`; grants are no-ops if already present.
3. **Bundle resources** — the DLT pipeline, the orchestration job, the bootstrap job, and
   dashboards. Created/updated by `databricks bundle deploy`, which **reconciles** the workspace
   to match the bundle (it updates in place; it never duplicates).

So re-running setup or deploy is always safe.

---

## End-to-end in one block

Two ready-to-paste sequences. The first stands up a brand-new environment; the second wipes an
existing one and rebuilds it from scratch (use this after a schema change).

**First time (nothing exists yet):**

```bash
# one-time local setup
make install                                                   # Python deps + the package (editable)
make auth HOST=https://<your-workspace>.cloud.databricks.com   # OAuth login (one-time)

# stand up the environment, load data, build, check
make bootstrap ENV=dev      # groups + catalog + schemas + Volume + grants + bundle deploy
make load      ENV=dev      # full 50-year baseline + one batch, generated locally and uploaded
make run       ENV=dev      # pre_task -> DLT pipeline -> post_task
make verify    ENV=dev      # confirm rows flowed through every layer
```

**Full clean-slate rebuild (wipe and start over):**

```bash
make clean ENV=dev                              # remove local generated files (.local, the SQLite db)
bash deployment/destroy.sh --env dev --force    # tear down catalog, jobs, pipeline, warehouse, groups (no prompt)
make bootstrap ENV=dev                          # recreate everything (this also deploys the bundle)
make load      ENV=dev                          # full 50-year baseline + a batch -> Volume
make run       ENV=dev                          # build the warehouse
make verify    ENV=dev                          # confirm it's correct
```

Notes:

- `make bootstrap` already runs `databricks bundle deploy`, so there's **no** separate `make deploy`
  in these blocks. Use `make deploy ENV=dev` only for day-to-day code changes *after* bootstrap.
- `make destroy ENV=dev` does the same teardown but **prompts** you to type the catalog name; the
  `--force` form above skips that prompt so the block runs unattended.
- A from-scratch rebuild is the safe way to apply a schema change — the fresh pipeline's first run
  is a complete build, so there's no incremental-state or full-refresh step to remember.
- To load *new* data later (not a full rebuild), see "Loading new data after the first run" in
  [`operations.md`](operations.md).

---

## First-time setup (per environment)

One command provisions everything — identity, catalog, schemas, Volume, grants, and all bundle
resources:

```bash
make bootstrap ENV=dev
# exactly equivalent to running the script directly:
./deployment/bootstrap.sh --env dev
```

**First run only:** because the RBAC groups are account-level, the script does a one-time account
login. It explains why, then prompts for your **Account ID** (find it at
`https://accounts.cloud.databricks.com` → top-right user menu) and runs `databricks auth login`
against the account, saving a CLI profile (`vic-account`). This is **idempotent** — once that
profile is in `~/.databrickscfg`, later runs skip it. You can pre-supply the ID non-interactively
with `DATABRICKS_ACCOUNT_ID=<id>`, override the profile name with `DATABRICKS_ACCOUNT_PROFILE`, or
skip the groups entirely with `--skip-groups`.

Use **one** of the two lines above. You do **not** run the internal steps yourself; for
transparency, the script performs, in order:

1. **Account RBAC groups** — ensures account auth, then creates the five account groups (idempotent).
2. **Catalog** — `databricks catalogs get`; if absent, finds or creates a small serverless SQL
   warehouse (`vic_suburbs_<env>_wh`, 2X-Small, auto-stops after 10 idle min) and runs
   `CREATE CATALOG IF NOT EXISTS` through it (the Default-Storage-compatible path), then verifies.
3. **`databricks bundle deploy -t dev`** — deploys the pipeline, jobs, and bootstrap job.
4. **`databricks bundle run vic_suburbs_bootstrap_job -t dev`** — creates the schemas, the landing
   Volume, and the grants.

> SQL-only fallback for the data objects (e.g. running by hand in the SQL editor):
> `bootstrap/00_catalog_schema.sql` then `bootstrap/10_grants.sql`, substituting `${catalog}`.

---

## Deploy and run

After first-time setup, day-to-day changes ship with a plain deploy:

```bash
make deploy ENV=dev          # or: databricks bundle deploy -t dev
```

**The pipeline ingests landing files, so load at least one batch before running.** A run against
an empty Volume fails — Auto Loader has no source folders to read (you'll see a
`FileNotFoundException` on the first entity). One command generates a full synthetic batch and
uploads it to the env's landing Volume:

```bash
make load ENV=dev            # generate a synthetic batch + upload it to the dev Volume
make run  ENV=dev            # pre_task -> DLT pipeline -> post_task
```

`make load` does two things, because the generator runs **locally** and can't write to
`/Volumes/...` directly from WSL (that path only exists on Databricks compute):

1. **generate** — seeds the synthetic universe and emits a mixed batch to
   `.local/landing/<entity>/*.csv` for **every** entity (`make generate`).
2. **upload** — copies each entity folder into
   `dbfs:/Volumes/vic_suburbs_<env>/00_landing/files/<entity>/` via the Databricks CLI.

Already generated and only want to push the existing batch? Use `make upload ENV=dev`. To do it by
hand (or to see exactly what `upload` runs):

```bash
for d in .local/landing/*/; do
  databricks fs cp -r "$d" \
    "dbfs:/Volumes/vic_suburbs_dev/00_landing/files/$(basename "$d")" --overwrite
done
databricks fs ls dbfs:/Volumes/vic_suburbs_dev/00_landing/files   # expect: crime/ demographics/ … transport/
```

Confirm it worked:

```bash
./tools/dbsql.sh "SELECT * FROM vic_suburbs_dev.05_metadata.vw_pipeline_health"
./tools/dbsql.sh "SELECT * FROM vic_suburbs_dev.04_reporting.vw_q6_most_expensive LIMIT 10"
```

Re-running with **no new files** logs a `NO_OP` (zero rows written) — the idempotency proof. (That
NO_OP is for an *incremental* re-run after data is loaded; a first run against a truly empty
Volume is not a meaningful run and will error, as above.)

---

## Subsequent deployments and promotion

Promotion is re-deploying the **same** bundle to the next target; only the target variables
(catalog, warehouse size, schedule, run-as) change, so environments can't drift structurally.

```bash
make bootstrap ENV=qa        # first time on qa only
make deploy    ENV=qa        # or: databricks bundle deploy -t qa
# ... smoke test ...
make bootstrap ENV=prod      # first time on prod only
make deploy    ENV=prod      # runs as sp_vic_suburbs_prod, weekly schedule
```

| Target | Catalog | Mode | Schedule | Run-as |
|---|---|---|---|---|
| dev | `vic_suburbs_dev` | development | paused | developer |
| qa | `vic_suburbs_qa` | production | unpaused | deployer |
| prod | `vic_suburbs_prod` | production | weekly (Mon 06:00 AEST) | `sp_vic_suburbs_prod` |

---

## Teardown

**Run one command** for a true clean slate — it removes *everything* the project provisions for
an environment:

```bash
make destroy ENV=dev
# equivalently (same thing):
./deployment/destroy.sh --env dev          # add --force to skip the typed confirmation
```

By default it asks you to type the catalog name to confirm. You do **not** run anything else; for
transparency, the script performs, in order (**not** steps to run yourself):

```text
1.  databricks bundle destroy -t dev --auto-approve
        → deletes the pipeline, job, bootstrap job, and dashboards, then removes the
          leftover bundle workspace folder
          (/Workspace/Users/<you>/.bundle/vic_suburbs_dwh, and .bundle if now empty)
2.  databricks catalogs delete vic_suburbs_dev --force
        → drops the catalog and, by cascade, every schema, table, view, and Volume
          (including Auto Loader checkpoints)
3.  delete SQL warehouse vic_suburbs_dev_wh           (the one bootstrap provisioned)
4.  delete the five account RBAC groups                (uses the account profile; best-effort)
```

Group deletion uses the same account profile as bootstrap (`vic-account` by default); if account
auth isn't configured it's skipped with a warning rather than failing the teardown. SQL-only
equivalent of step 2: `bootstrap/99_teardown.sql` (`DROP CATALOG … CASCADE`).

> The RBAC groups are workspace-wide (not env-suffixed), so destroying one environment removes
> them for all. For a single-environment POC that's the intended clean-slate behaviour.

After teardown, the next setup starts fresh from `make bootstrap ENV=dev`.

---

## Rollback

This is a POC: a single `main` branch, no release tags. Rollback therefore has two independent
parts — **code** (the bundle definition) and **data** (the Delta tables). Do whichever you need.

### Code rollback

Because deploy is declarative and idempotent, rolling back code is "check out the good state,
deploy again":

```bash
# 1. find the last good commit
git log --oneline -n 10

# 2. undo the bad change on main (revert keeps history; no force-push, safe on a shared branch)
git revert <bad-commit-sha>            # for a range: git revert <oldest>^..<newest>
#    (local-only alternative if main isn't pushed yet: git reset --hard <good-sha>)

# 3. re-deploy — the workspace reconciles back to the reverted definition
make deploy ENV=dev                    # or: databricks bundle deploy -t dev
```

### Data rollback (Delta time travel)

Every Gold table is Delta, so any table can be restored to an earlier version independently of
code. First inspect the history, then restore.

**Simplest:** run the SQL in the workspace **SQL editor**:

```sql
DESCRIBE HISTORY vic_suburbs_dev.03_gold.fact_suburb_property;
-- pick the good version number (or a timestamp), then:
RESTORE TABLE vic_suburbs_dev.03_gold.fact_suburb_property TO VERSION AS OF 5;
-- or:  RESTORE TABLE ... TO TIMESTAMP AS OF '2026-06-20T00:00:00';
```

**From the WSL terminal** via the Databricks CLI (Statement Execution API). Get a SQL warehouse
id once, then run the statements:

```bash
WID=$(databricks warehouses list -o json | jq -r '.[0].id')   # any running SQL warehouse

# inspect history
databricks api post /api/2.0/sql/statements --json "{
  \"warehouse_id\": \"$WID\",
  \"statement\": \"DESCRIBE HISTORY vic_suburbs_dev.03_gold.fact_suburb_property\"
}"

# restore to a known-good version
databricks api post /api/2.0/sql/statements --json "{
  \"warehouse_id\": \"$WID\",
  \"statement\": \"RESTORE TABLE vic_suburbs_dev.03_gold.fact_suburb_property TO VERSION AS OF 5\"
}"
```

> **Note:** time-travel restore recovers the *data* in a table. The next pipeline run reasserts
> the *current code's* logic, so for a genuine revert, roll back the code first (above), then
> restore/reprocess. To rebuild from scratch instead, a DLT **full refresh** of the affected
> tables re-derives them from the immutable landing Volume:
> `databricks bundle run vic_suburbs_pipeline -t dev --full-refresh-all` (or
> `--full-refresh <table>` for specific tables).

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `bundle deploy` fails: `CATALOG_DOES_NOT_EXIST` | Run `make bootstrap ENV=<env>` (it creates the catalog before deploying), not `make deploy`. |
| Bootstrap grants skipped: `PRINCIPAL_DOES_NOT_EXIST` | The groups aren't account-level. Make sure the account profile is set up — re-run `make bootstrap`, which creates **account** groups (workspace-local `groups create` won't work for UC). |
| `catalogs create` fails: *Default Storage enabled / metastore storage root URL does not exist* | Expected on Default-Storage workspaces — the script creates the catalog via `CREATE CATALOG` SQL instead. Just ensure serverless SQL can run. |
| `No SQL warehouse found` during bootstrap | The script now auto-provisions `vic_suburbs_<env>_wh`; this only appears if serverless SQL can't be created — enable serverless, or pre-create a warehouse and set `DATABRICKS_WAREHOUSE_ID`. |
| `cannot reach the account API` during bootstrap | Account auth missing. Re-run and enter your Account ID at the prompt, set `DATABRICKS_ACCOUNT_ID`, or pass `--skip-groups`. |
| Pipeline run does nothing (`NO_OP`) unexpectedly | No new files in the landing Volume — emit a batch first (`make emit` + `make upload`). |
| `account group already exists` during bootstrap | Expected and harmless; the script skips it. Use `--skip-groups` if your IdP manages groups. |
| `databricks catalogs delete` fails: catalog not empty | Re-run with `--force` (the script already passes it); ensure no other workspace holds the catalog. |

**Cross-references:** local workflow → [`local-development.md`](local-development.md) · RBAC model
→ [`../design/07-deployment-and-rbac-spec.md`](../design/07-deployment-and-rbac-spec.md).
