# Runbook — Local Development

How to work on the project on a laptop (IntelliJ + WSL Ubuntu, or any Linux/macOS shell)
without a Databricks connection.

## Prerequisites
- Python 3.10+
- Java 17+ (only for the optional Spark integration test)
- `make`

## Set up
```bash
make install        # installs dev deps and the package (editable, src layout)
```

## Generate data
```bash
make generate       # = seed (build universe) + emit (write landing files)
# or step by step:
python -m vic_suburbs.generator.seed --config config/synthetic/seed_config.yaml
python -m vic_suburbs.generator.emit --mode mixed --landing .local/landing
```
Emit modes: `history` (full back-cast), `update` (mutations → SCD2/CDC changes),
`mixed` (the realistic default).

## Run tests
```bash
make test           # unit tests (fast, no Spark)
pytest tests/integration   # local Spark; auto-skips if pyspark/Java absent
```

## Format & lint
```bash
make fmt            # black + ruff --fix
make lint           # check only (matches CI)
pre-commit install  # optional: run hooks on every commit
```

## What you can and can't run locally
- **Local:** the synthetic generator, config loading, the DQ compiler, all unit tests, and
  the Spark transforms via the integration test.
- **Databricks only:** the DLT pipeline itself (Auto Loader, `APPLY CHANGES`, the event log)
  and the orchestration notebooks — these need a workspace. Use the bundle to deploy.
