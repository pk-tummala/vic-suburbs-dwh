# Victoria Suburbs Profiler — developer entrypoints
# Run `make help` for the list.

ENV ?= dev
LANDING ?= .local/landing
MODE ?= mixed
CONFIG_DIR ?= config
CATALOG ?= vic_suburbs_$(ENV)
VOLUME ?= dbfs:/Volumes/$(CATALOG)/00_landing/files
ENTITY ?=

.PHONY: help install fmt lint test seed emit generate er-diagram dashboard auth bootstrap validate deploy run upload load query verify diagnose diagnose-silver run-pipeline destroy clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

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

seed:  ## Build the synthetic universe + emit the full 50-year baseline
	python -m vic_suburbs.generator.seed --config $(CONFIG_DIR)/synthetic/seed_config.yaml --landing $(LANDING)

emit:  ## Emit an incremental batch of landing files (MODE=new|update|mixed)
	python -m vic_suburbs.generator.emit --mode $(MODE) --landing $(LANDING)

generate: seed emit  ## Full 50-year baseline + one incremental batch


er-diagram:  ## Regenerate the ER diagram SVG from tools/build-er-diagram.py
	python3 tools/build-er-diagram.py

dashboard:  ## Regenerate the AI/BI dashboard JSON from tools/build_dashboard.py
	python3 tools/build_dashboard.py

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

run-pipeline:  ## Run ONLY the DLT pipeline (streams per-flow progress to console; skips run-log tasks)
	databricks bundle run vic_suburbs_pipeline -t $(ENV)

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

diagnose-silver:  ## Explain an empty Silver measure (localize, key health, DQ, ordering) [ENTITY=<name>]
	bash operations/diagnose_silver.sh $(ENV) $(ENTITY)

destroy:  ## Tear down ALL deployed objects for ENV (pipeline, job, catalog+contents)
	bash deployment/destroy.sh --env $(ENV)

clean:  ## Remove local generated artefacts
	rm -rf .local synthetic_universe.db .pytest_cache
