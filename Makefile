# Victoria Suburbs Profiler — developer entrypoints
# Run `make help` for the list.

ENV ?= dev
LANDING ?= .local/landing
CONFIG_DIR ?= config
CATALOG ?= vic_suburbs_$(ENV)
VOLUME ?= dbfs:/Volumes/$(CATALOG)/00_landing/files
ENTITY ?=

.PHONY: help install fmt lint test seed emit generate extract er-diagram auth bootstrap validate deploy run upload load query verify diagnose destroy clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Install dev dependencies
	pip install -r requirements-dev.txt --break-system-packages
	pip install -e . --break-system-packages

fmt:  ## Format code (ruff)
	ruff check --fix .
	ruff format .

lint:  ## Lint code (ruff check + format check)
	ruff check .
	ruff format --check .

test:  ## Run unit tests
	pytest tests/unit

seed:  ## Build the synthetic universe (one-time)
	python -m vic_suburbs.generator.seed --config $(CONFIG_DIR)/synthetic/seed_config.yaml

emit:  ## Emit a batch of synthetic landing files
	python -m vic_suburbs.generator.emit --mode mixed --landing $(LANDING)

generate: seed emit  ## Seed + emit in one step

extract:  ## Extract ONE real source into local landing: make extract ENTITY=property
	@test -n "$(ENTITY)" || { echo "Set ENTITY=<name>, e.g. make extract ENTITY=property (then 'make upload ENV=...')"; exit 1; }
	python -m vic_suburbs.extract.run_extract $(ENTITY) --landing $(LANDING)

er-diagram:  ## Regenerate the ER diagram SVG from tools/build-er-diagram.py
	python3 tools/build-er-diagram.py

auth:  ## Authenticate the Databricks CLI (OAuth) — set HOST=https://<workspace-url>
	databricks auth login --host $(HOST)

bootstrap:  ## One-command env setup: groups + bundle + catalog/schemas/volume/grants
	bash deployment/bootstrap.sh --env $(ENV)

validate:  ## Validate the Databricks bundle for ENV
	databricks bundle validate -t $(ENV)

deploy:  ## Deploy the bundle to ENV
	databricks bundle deploy -t $(ENV)

run:  ## Run the pipeline job on ENV
	databricks bundle run vic_suburbs_job -t $(ENV)

upload:  ## Upload local landing files into the ENV landing Volume
	@ls -d $(LANDING)/*/ >/dev/null 2>&1 || { echo "No landing files in $(LANDING) — run 'make generate' first (or use 'make load')."; exit 1; }
	@echo "Uploading $(LANDING)/* -> $(VOLUME)"
	@for d in $(LANDING)/*/; do \
		e=$$(basename "$$d"); \
		printf '  %s\n' "$$e"; \
		databricks fs cp -r "$$d" "$(VOLUME)/$$e" --overwrite; \
	done
	@echo "Uploaded. Verify with: databricks fs ls $(VOLUME)"

load: generate upload  ## One command: generate a synthetic batch + upload it to the ENV Volume

query:  ## Run a SQL statement on a warehouse: make query SQL="SELECT ..."
	bash tools/dbsql.sh "$(SQL)"

verify:  ## Validate the built warehouse (run health, row flow, keys, joins, serving) for ENV
	bash operations/verify_pipeline.sh $(ENV)

diagnose:  ## Explain fact<->dim joins (per-year resolution, orphans) for ENV [ENTITY=<name>]
	bash operations/diagnose_fact_joins.sh $(ENV) $(ENTITY)

destroy:  ## Tear down ALL deployed objects for ENV (pipeline, job, catalog+contents)
	bash deployment/destroy.sh --env $(ENV)

clean:  ## Remove local generated artefacts
	rm -rf .local synthetic_universe.db .pytest_cache
