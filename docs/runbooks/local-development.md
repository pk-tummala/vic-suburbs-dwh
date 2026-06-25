# Runbook — Local Development

> **Status:** Reference doc · linked from README
>
> The day-to-day local workflow — generate data, run the pipeline logic that *can* run off
> a workspace, test, lint, and regenerate diagrams. Every step lists the **`make`** form and
> the **direct** command it wraps, so you can use either and know exactly what runs.

First-time environment setup (WSL, venv, CLI, JDK, auth) is in
[`intellij-wsl-setup.md`](intellij-wsl-setup.md). This guide assumes that's done and the venv
is active:

```bash
cd ~/projects/vic-suburbs-dwh
source .venv/bin/activate          # prompt shows (.venv)
```

## Contents

- [What runs locally vs. on Databricks](#what-runs-locally-vs-on-databricks)
- [1. Install / refresh dependencies](#1-install--refresh-dependencies)
- [2. Generate synthetic data](#2-generate-synthetic-data)
  - [Emit modes](#emit-modes)
  - [Inspect what was generated](#inspect-what-was-generated)
- [3. Extract from a real (or synthetic) source](#3-extract-from-a-real-or-synthetic-source)
- [4. Run the tests](#4-run-the-tests)
- [5. Format and lint](#5-format-and-lint)
- [6. Regenerate the ER diagram](#6-regenerate-the-er-diagram)
- [7. Validate the bundle (no deploy)](#7-validate-the-bundle-no-deploy)
- [8. Clean local artefacts](#8-clean-local-artefacts)
- [The inner loop, condensed](#the-inner-loop-condensed)

---

## What runs locally vs. on Databricks

| Runs locally (no workspace) | Databricks-only |
|---|---|
| Synthetic generator (`seed`, `emit`) | The DLT pipeline (Auto Loader, `APPLY CHANGES`, event log) |
| Config loading + the DQ rule compiler | The orchestration notebooks (`pre_task` / `post_task`) |
| All unit tests | AI/BI dashboards |
| The Spark transforms via the integration test | Anything reading Unity Catalog system tables |

So you can build and test the load-bearing logic on a laptop, then deploy the bundle to run
the full pipeline.

---

## 1. Install / refresh dependencies

```bash
make install
# direct (inside the activated venv):
pip install -r requirements-dev.txt
pip install -e .
```

`pip install -e .` puts the `vic_suburbs` package on the path (editable), so
`python -m vic_suburbs....` and the tests resolve imports.

> `make install` appends `--break-system-packages` so it also works in the CI container (which
> has no venv). Inside an activated venv that flag is a harmless no-op; the direct commands
> above are the clean local form.

---

## 2. Generate synthetic data

```bash
make generate
# direct:
python -m vic_suburbs.generator.seed   --config config/synthetic/seed_config.yaml
python -m vic_suburbs.generator.emit   --mode mixed --landing .local/landing
```

- **`seed`** builds the universe **once** — a deterministic ~50-year back-cast for all
  entities, stored in `synthetic_universe.db` (SQLite). Re-run only when you change
  `seed_config.yaml` or the suburb spine.
- **`emit`** writes landing CSVs from that universe. Run it as often as you like.

### Emit modes

```bash
python -m vic_suburbs.generator.emit --mode history --landing .local/landing   # full back-cast (first load / backfill)
python -m vic_suburbs.generator.emit --mode new     --landing .local/landing   # next-period net-new inserts
python -m vic_suburbs.generator.emit --mode update  --landing .local/landing   # mutations → SCD2 / restatements
python -m vic_suburbs.generator.emit --mode mixed   --landing .local/landing   # all three (the realistic default)
```

Files land at `.local/landing/<entity>/<entity>_<batch8>[_part].csv`, stamped with
`batch_id`, `source_system=SYNTHETIC`, and a per-row `effective_ts` — exactly the shape Auto
Loader ingests on Databricks. To get these files onto a workspace, **don't** point `--landing` at
`/Volumes/...` (that path isn't mounted on your laptop — it would write to a junk local folder).
Instead generate locally, then push to the env's landing Volume with `make load ENV=dev`
(generate + upload) or `make upload ENV=dev` (upload an existing batch). See
[`deployment-guide.md`](deployment-guide.md) § Deploy and run.

### Inspect what was generated

```bash
ls -R .local/landing
head -3 .local/landing/property/property_*.csv
# how many rows per entity:
for f in .local/landing/*/*.csv; do echo "$(tail -n +2 "$f" | wc -l)  $f"; done
```

---

## 3. Extract from a real (or synthetic) source

```bash
python -m vic_suburbs.extract.run_extract property --landing .local/landing
```

The connector is chosen from `config/sources/<entity>.yaml`. The synthetic connector needs
nothing; the DataVic (CKAN) connector reads public datasets with no key (set `DATAVIC_API_KEY`
only for higher limits); the ABS connector is a stub to wire per dataflow.

---

## 4. Run the tests

```bash
make test
# direct:
pytest tests/unit                                   # fast, no Spark

pytest tests/integration -v                          # local Spark (needs JDK + pyspark)
pytest tests/unit/test_dq.py::test_not_null_expr -v  # a single test
pytest tests/unit --cov=vic_suburbs                  # with coverage
```

The unit suite covers config loading, the DQ compiler, the transform helpers, and generator
determinism (same seed ⇒ identical universe; age bands always sum to the total). The
integration test exercises `conform_sal_code` + DQ-expression evaluation in a real
`SparkSession` and **auto-skips** if `pyspark`/Java aren't installed.

---

## 5. Format and lint

```bash
make fmt            # auto-fix:  ruff check --fix . ; ruff format .
make lint           # check-only: ruff check . ; ruff format --check .   (matches CI)
# direct equivalents:
ruff check --fix . && ruff format .
ruff check . && ruff format --check .
```

Optional pre-commit hooks (run on every `git commit`):

```bash
pre-commit install
pre-commit run --all-files
```

---

## 6. Regenerate the ER diagram

If you change the data model, regenerate the SVG embedded in the README and data-model doc:

```bash
make er-diagram
# direct:
python3 tools/build-er-diagram.py        # writes docs/data-model/er-fact-constellation.svg
```

Uses only the standard library and resolves paths relative to itself, so it runs from any
directory.

---

## 7. Validate the bundle (no deploy)

A safe offline-ish check that the bundle is well-formed (needs CLI auth, creates nothing):

```bash
make validate ENV=dev
# direct:
databricks bundle validate -t dev
```

---

## 8. Clean local artefacts

```bash
make clean
# direct:
rm -rf .local synthetic_universe.db .pytest_cache .ruff_cache
```

Removes generated landing files, the SQLite universe, and tool caches. The venv and your
config are untouched.

---

## The inner loop, condensed

```bash
source .venv/bin/activate
make generate      # data
make test          # logic
make lint          # style
# iterate on src/ or config/, repeat
```

When you're ready to run the *full* pipeline, move to [`deployment-guide.md`](deployment-guide.md).
