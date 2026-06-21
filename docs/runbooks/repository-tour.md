# Runbook — Repository Tour

A five-minute orientation to where everything lives and why.

## Top level
| Path | What it is |
|---|---|
| `databricks.yml` | The Asset Bundle: one definition, three targets (`dev`/`qa`/`prod`). |
| `resources/` | Bundle resources — the DLT `pipeline`, the orchestration `job`, and `dashboards`. |
| `config/` | **The behaviour lives here.** Entities, sources, schemas, DQ rules, per-env settings, synthetic config. |
| `src/vic_suburbs/` | Python: shared framework, generator, extractors, pipeline builders, orchestration. |
| `bootstrap/` | UC bootstrap notebook (`bootstrap_uc.py`) + SQL for catalog/schema/volume, grants, and teardown. |
| `deployment/` | `bootstrap.sh` (one-command env setup) and `destroy.sh` (full teardown). |
| `tests/` | `unit/` (pure Python, no Spark) and `integration/` (local Spark, auto-skipped if unavailable). |
| `docs/` | Design specs, data model, architecture diagrams, these runbooks. |

## `src/vic_suburbs/` map
- `common/config.py` — single loader for all YAML config.
- `common/dq.py` — compiles DQ rules → Spark SQL expressions (the engine).
- `common/transforms.py` — typing, suburb-name conforming, dedup (Spark + pure helpers).
- `common/lineage.py`, `common/runlog.py` — batch ids and the run-log writer.
- `generator/seed.py`, `generator/emit.py` — build and emit the synthetic universe.
- `extract/run_extract.py` — connector interface + CKAN / ABS / synthetic.
- `pipeline/{bronze,silver,gold}.py` — the generic, config-driven builders.
- `pipeline/dlt_entry.py` — wires the builders for every registered entity.
- `orchestration/{pre,post}_task.py` — open/close the run log around the pipeline.

## The golden rule
The pipeline code is generic. To add a subject area you touch `config/`, not `src/`.
See `docs/design/04-pipeline-pattern.md`.
