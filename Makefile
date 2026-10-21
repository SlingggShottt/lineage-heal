# Makefile
# Short names for the commands in CLAUDE.md, so developers and CI run the same thing.
# Targets for Docker, dbt and the frontend are added by the tasks that introduce them.

.DEFAULT_GOAL := help
.PHONY: help setup up down dagster fmt lint typecheck test test-integration check clean

help: ## List available targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

setup: ## Install dependencies and git hooks
	uv sync --all-extras
	uv run pre-commit install --hook-type pre-commit --hook-type commit-msg

up: ## Start local services (Postgres with pgvector) and wait until healthy
	docker compose up -d --wait postgres

down: ## Stop local services (data stays in the pgdata volume)
	docker compose down

# DAGSTER_HOME holds the instance settings (dagster_home/dagster.yaml). Dagster also reads .env
# from here, so RAW_ZONE_DIR and the other settings apply to the runs it launches.
dagster: ## Start the Dagster UI and daemon at http://localhost:3000
	DAGSTER_HOME=$(CURDIR)/dagster_home uv run dagster dev -m lineageheal.orchestration.definitions

fmt: ## Format code
	uv run ruff format .

lint: ## Lint without changing files
	uv run ruff format --check .
	uv run ruff check .

typecheck: ## Static type check (strict)
	uv run mypy src

# The dbt scenarios exercise SQL, not our Python (that is covered by the tests next to it), and
# coverage tracing doubles their run time, so they run as a second step without it.
test: ## Run unit tests: Python with coverage, then the dbt scenarios
	uv run pytest --cov -m "unit and not dbt"
	uv run pytest -m "unit and dbt"

test-integration: ## Run tests that need the local Postgres (start it with make up first)
	uv run pytest -m integration

check: lint typecheck ## Formatting, linting and type checks

clean: ## Remove tool caches and build output
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov dist build
