# Victoria Suburbs Profiler — developer entrypoints
# Run `make help` for the list.

ENV ?= dev
LANDING ?= .local/landing
CONFIG_DIR ?= config

.PHONY: help install fmt lint test seed emit generate auth bootstrap validate deploy run destroy clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Install dev dependencies
	pip install -r requirements-dev.txt --break-system-packages
	pip install -e . --break-system-packages

fmt:  ## Format code
	black src tests
	ruff check --fix src tests

lint:  ## Lint code
	ruff check src tests
	black --check src tests

test:  ## Run unit tests
	pytest tests/unit

seed:  ## Build the synthetic universe (one-time)
	python -m vic_suburbs.generator.seed --config $(CONFIG_DIR)/synthetic/seed_config.yaml

emit:  ## Emit a batch of synthetic landing files
	python -m vic_suburbs.generator.emit --mode mixed --landing $(LANDING)

generate: seed emit  ## Seed + emit in one step

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

destroy:  ## Tear down ALL deployed objects for ENV (pipeline, job, catalog+contents)
	bash deployment/destroy.sh --env $(ENV)

clean:  ## Remove local generated artefacts
	rm -rf .local synthetic_universe.db .pytest_cache
