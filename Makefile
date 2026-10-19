# Makefile
# Short names for the commands in CLAUDE.md, so developers and CI run the same thing.
# Targets for Docker, dbt and the frontend are added by the tasks that introduce them.

.DEFAULT_GOAL := help
.PHONY: help setup up down fmt lint typecheck test check clean

help: ## List available targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

setup: ## Install dependencies and git hooks
	uv sync --all-extras
	uv run pre-commit install --hook-type pre-commit --hook-type commit-msg

up: ## Start local services (Postgres with pgvector) and wait until healthy
	docker compose up -d --wait postgres

down: ## Stop local services (data stays in the pgdata volume)
	docker compose down

fmt: ## Format code
	uv run ruff format .

lint: ## Lint without changing files
	uv run ruff format --check .
	uv run ruff check .

typecheck: ## Static type check (strict)
	uv run mypy src

test: ## Run unit tests with coverage
	uv run pytest --cov

check: lint typecheck ## Formatting, linting and type checks

clean: ## Remove tool caches and build output
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov dist build
