# Runbook — Repository Tour

> **Status:** Reference doc · linked from README
>
> The annotated map of the repository. Every directory and file has a one-line "what it's for",
> with pointers to the deeper design docs. The tree below mirrors the actual layout.

## Contents

- [Top level](#top-level)
- [`databricks.yml` + `resources/` — the deployable bundle](#databricksyml--resources--the-deployable-bundle)
- [`config/` — all configuration (the behaviour lives here)](#config--all-configuration-the-behaviour-lives-here)
- [`src/vic_suburbs/` — the Python package](#srcvic_suburbs--the-python-package)
- [`bootstrap/` — Unity Catalog provisioning + teardown](#bootstrap--unity-catalog-provisioning--teardown)
- [`deployment/` — one-command setup and teardown](#deployment--one-command-setup-and-teardown)
- [`tests/` — unit (no Spark) + integration (local Spark)](#tests--unit-no-spark--integration-local-spark)
- [`tools/` — auxiliary developer scripts](#tools--auxiliary-developer-scripts)
- [`docs/` — documentation](#docs--documentation)
- [Files excluded by `.gitignore`](#files-excluded-by-gitignore)
- [Navigation — where to look for what](#navigation--where-to-look-for-what)

---

## Top level

```
vic-suburbs-dwh/
├── README.md                     # The hero doc — start here
├── LICENSE                       # MIT
├── Makefile                      # Common commands (install, test, lint, generate, bootstrap, deploy, load, run, destroy)
├── databricks.yml                # Databricks Asset Bundle: dev / qa / prod targets + variables
├── pyproject.toml                # Python project + tooling config (ruff, black, pytest, src layout)
├── requirements.txt              # Runtime deps (pyyaml, pandas, requests)
├── requirements-dev.txt          # Dev deps (pytest, ruff, black, pre-commit, pyspark)
├── .pre-commit-config.yaml       # Lint hooks: ruff, ruff-format + standard checks
└── .gitignore                    # Python + repo-specific exclusions (.venv, .local, etc.)
```

**Where to start as a new reader**
1. `README.md` — the overview and quickstart
2. `docs/design/00-overview-and-architecture.md` — the big picture + catalog topology
3. `docs/design/04-pipeline-pattern.md` — ★ the transform contract everything implements

---

## `databricks.yml` + `resources/` — the deployable bundle

```
databricks.yml                    # bundle name, variables (catalog, landing_volume, ...), 3 targets
resources/
├── pipelines/
│   └── vic_suburbs.pipeline.yml       # Lakeflow Declarative Pipeline (DLT): serverless, libraries → dlt_entry.py
├── jobs/
│   ├── vic_suburbs.job.yml            # orchestration job: pre_task → pipeline → post_task (weekly schedule)
│   └── vic_suburbs_bootstrap.job.yml  # one-off UC bootstrap job (runs bootstrap/bootstrap_uc.py on serverless)
└── dashboards/
    └── .gitkeep                       # placeholder for the AI/BI dashboard (added after first Gold load)
```

**Cross-reference**: [`docs/design/07-deployment-and-rbac-spec.md`](../design/07-deployment-and-rbac-spec.md).

---

## `config/` — all configuration (the behaviour lives here)

```
config/
├── entities.yaml                 # ★ registers every entity (kind: reference|measure, grain, SCD2 keys)
├── sources/                      # one per entity: connector + landing path + effective field + keys
│   ├── demographics.yaml  property.yaml  crime.yaml  transport.yaml  education.yaml
│   └── suburb_ref.yaml    lga_ref.yaml
├── schemas/                      # Silver column names + types per entity
│   ├── demographics.yaml  property.yaml  crime.yaml  transport.yaml  education.yaml
│   └── suburb_ref.yaml
├── dq_rules/                     # data-quality rules per entity (WARN | FATAL)
│   └── demographics.yaml  property.yaml  crime.yaml  transport.yaml  education.yaml
├── pipeline/                     # per-environment runtime settings
│   └── dev.yaml  qa.yaml  prod.yaml
└── synthetic/                    # synthetic-universe inputs
    ├── seed_config.yaml              # back-cast parameters (horizon, growth, noise)
    ├── mutation_rules.yaml           # mutation probabilities for SCD2/restatement changes
    └── suburb_seed.csv               # real VIC suburb spine (identities) + synthetic metric anchors
```

**Key file**: `entities.yaml` is the manifest the pipeline loops over. Adding a subject area =
add an entry here + the three matching `sources/`, `schemas/`, `dq_rules/` files.
**Cross-reference**: [`docs/design/05-data-quality-spec.md`](../design/05-data-quality-spec.md) (DQ grammar).

---

## `src/vic_suburbs/` — the Python package

```
src/vic_suburbs/
├── common/                       # shared, entity-agnostic framework
│   ├── config.py                     # single loader for all YAML config (resolves VIC_CONFIG_DIR / repo root)
│   ├── dq.py                         # ★ compiles DQ rules → Spark SQL boolean expressions (the engine)
│   ├── transforms.py                 # typing + dedup helpers (Spark fns + pure helpers)
│   ├── lineage.py                    # batch_id minting + lineage column constants
│   └── runlog.py                     # run-log DDL + open/close helpers (05_metadata)
├── generator/                    # the synthetic universe
│   ├── seed.py                       # build the universe + write the full 50-yr baseline (deterministic → SQLite)
│   └── emit.py                       # emit an incremental batch: --mode new | update | mixed
├── pipeline/                     # the generic, config-driven DLT builders
│   ├── bronze.py                     # Auto Loader ingestion → raw_<entity>
│   ├── silver.py                     # type + DQ + dedup; CDC feeds for SCD2
│   ├── gold.py                       # SCD2 dims (APPLY CHANGES), dim_year, facts (temporal SK lookup)
│   └── dlt_entry.py                  # ★ wires all builders for every registered entity
└── orchestration/                # Databricks notebooks bracketing the pipeline
    ├── pre_task.py                   # opens the run-log row (status=RUNNING)
    └── post_task.py                  # reads the DLT event log, sets SUCCESS / NO_OP / FAILED
```

**Cross-reference**: [`docs/design/04-pipeline-pattern.md`](../design/04-pipeline-pattern.md) (the contract these implement).

---

## `bootstrap/` — Unity Catalog provisioning + teardown

```
bootstrap/
├── bootstrap_uc.py               # ★ notebook run by vic_suburbs_bootstrap_job: catalog, schemas, volume, grants (idempotent)
├── 00_catalog_schema.sql         # SQL reference for the catalog/schema/volume DDL (manual fallback)
├── 10_grants.sql                 # SQL reference for the least-privilege grants (manual fallback)
└── 99_teardown.sql               # SQL reference for DROP CATALOG ... CASCADE (manual fallback)
```

The automated path is `bootstrap_uc.py` (run via `make bootstrap`); the `.sql` files are the
human-readable equivalent for the SQL editor.

---

## `deployment/` — one-command setup and teardown

```
deployment/
├── bootstrap.sh                  # account groups (1-time account login) → ensure catalog (CREATE CATALOG SQL on a serverless warehouse) → bundle deploy → run UC bootstrap job
└── destroy.sh                    # bundle destroy (+ workspace-folder cleanup) → drop catalog (cascade) → delete SQL warehouse → delete account groups; confirmation-guarded
```

Both are thin orchestrators over the Databricks CLI, wrapped by `make bootstrap` / `make destroy`.
**Cross-reference**: [`deployment-guide.md`](deployment-guide.md).

---

## `tests/` — unit (no Spark) + integration (local Spark)

```
tests/
├── conftest.py                   # shared fixtures (config_dir)
├── unit/                         # fast, no Spark
│   ├── test_config.py                # config loading + entity manifest
│   ├── test_dq.py                    # the DQ rule compiler
│   ├── test_transforms.py            # cast plan + suburb-name normalisation
│   └── test_generator.py             # generator determinism + emit modes
└── integration/
    └── test_pipeline_smoke.py        # dedup + DQ-expr in a real SparkSession (auto-skips if no pyspark)
```

---

## `tools/` — auxiliary developer scripts

```
tools/
├── README.md                     # what's here + how to run
├── build-er-diagram.py           # regenerates docs/data-model/er-fact-constellation.svg (stdlib only)
└── dbsql.sh                      # run a SQL statement on a warehouse (Statement Execution API + jq)
```

These are **standalone scripts**, not an importable package (no `__init__.py` by design). Run
directly: `python3 tools/build-er-diagram.py` or `make er-diagram`. The script resolves paths
relative to itself, so it works from any directory.

---

## `docs/` — documentation

```
docs/
├── architecture/
│   ├── 01-high-level-architecture.svg     # system view (in README)
│   ├── 02-operational-data-flow.svg       # step-by-step flow (in README)
│   ├── 03-dashboard-showcase.png          # the six AI/BI charts + leaderboard (in README)
│   └── 04-make-command-wiring.svg         # how every make target wires to code (in README)
├── data-model/
│   ├── data-model.md                      # fact constellation, grain, SCD2 rationale
│   ├── data-dictionary.md                 # column-by-column dictionary of every layer
│   └── er-fact-constellation.svg          # the ER diagram (in README + data-model.md)
├── design/                                # 00 overview · 01 SCD2 · 02 incremental · 03 sourcing
│   ├── 04-pipeline-pattern.md             # ★ the transform contract
│   ├── 05-data-quality-spec.md  06-observability-and-lineage-spec.md  07-deployment-and-rbac-spec.md
│   └── 00..03 ...
└── runbooks/
    ├── intellij-wsl-setup.md              # local dev environment (this stack)
    ├── local-development.md               # local workflow (make + direct commands)
    ├── deployment-guide.md                # deploy / run / teardown / rollback
    └── repository-tour.md                 # this file
```

---

## Files excluded by `.gitignore`

`.venv/` (the virtualenv), `.local/` (locally emitted landing files), `synthetic_universe.db`
(the generated SQLite universe), `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`,
`*.egg-info/`, and `.databricks/` (CLI/bundle state). All are regenerated and never committed.

---

## Navigation — where to look for what

| I want to… | Go to |
|---|---|
| Understand the whole system | `README.md` → `docs/design/00` |
| See how a table is built | `docs/design/04-pipeline-pattern.md` + `src/vic_suburbs/pipeline/` |
| Add a new subject area | `config/entities.yaml` + `config/{sources,schemas,dq_rules}/` |
| Change DQ rules | `config/dq_rules/<entity>.yaml` (grammar: `docs/design/05`) |
| Set up locally | `docs/runbooks/intellij-wsl-setup.md` |
| Deploy / tear down | `docs/runbooks/deployment-guide.md` |
| See how the make commands wire to code | `docs/architecture/04-make-command-wiring.svg` (in README → Quickstart) |
| Regenerate the ER diagram | `make er-diagram` (`tools/build-er-diagram.py`) |
